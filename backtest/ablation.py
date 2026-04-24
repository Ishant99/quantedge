# =============================================================================
# backtest/ablation.py — Module Ablation Test Runner
#
# Measures the real edge contribution of each pipeline module by running
# backtests with one module disabled at a time (N+1 total runs):
#   1 baseline run  — full pipeline
#   N ablated runs  — one module neutralised per run
#
# Edge contribution = baseline_return - ablated_return
#   Positive value → module adds value
#   Negative value → module hurts performance (removing it improves results)
#
# Usage:
#   python -m backtest.ablation --years 2
#   python -m backtest.ablation --symbols RELIANCE TCS INFY --years 1
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from backtest.engine import BacktestEngine, BacktestResult
from config import BACKTEST_START_DATE, BACKTEST_END_DATE, BACKTEST_CAPITAL
from utils import get_logger

logger = get_logger("Ablation")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class AblationResult:
    module_disabled:     str    # e.g. "sentiment", "fii_dii", "support_resistance"
    baseline_return:     float  # full pipeline return %
    ablated_return:      float  # return % with this module disabled
    edge_contribution:   float  # baseline - ablated  (positive = module adds value)
    baseline_win_rate:   float
    ablated_win_rate:    float
    n_trades_baseline:   int
    n_trades_ablated:    int


# ---------------------------------------------------------------------------
# AblationRunner
# ---------------------------------------------------------------------------

class AblationRunner:
    """
    Runs the BacktestEngine N+1 times — once for the full baseline pipeline,
    then once per module with that module's score contribution disabled /
    neutralised to 0.5 (chance level).

    The ablation config dict passed to BacktestEngine looks like:
        {"disabled_modules": ["sentiment"]}

    BacktestEngine._generate_signal() checks this dict and substitutes
    neutral scores for each disabled module.
    """

    MODULES = [
        "sentiment",
        "support_resistance",
        "multi_timeframe",
        "pattern_recognition",
        "volume_profile",
        "fii_dii",
        "pcr_signal",
        "momentum_filter",
        "earnings_guard",
    ]

    def run(
        self,
        symbols:    list,
        years:      int  = 2,
        start_date: str  = None,
        end_date:   str  = None,
        capital:    float = BACKTEST_CAPITAL,
    ) -> list:
        """
        Run baseline + ablation for each module.

        Parameters
        ----------
        symbols    : list of NSE symbol strings
        years      : number of years of history to backtest
        start_date : override start date (ISO string); derived from years if None
        end_date   : override end date (ISO string); defaults to today
        capital    : starting capital per backtest run

        Returns
        -------
        list[AblationResult] sorted by edge_contribution descending
        (highest-value modules first).
        """
        if end_date is None:
            end_date = datetime.today().strftime("%Y-%m-%d")
        if start_date is None:
            start_date = (
                datetime.today() - timedelta(days=365 * years)
            ).strftime("%Y-%m-%d")

        logger.info(
            f"Ablation: {len(symbols)} symbols | {start_date} → {end_date} | "
            f"{len(self.MODULES)} modules to test"
        )

        # --- Baseline (full pipeline) ---
        logger.info("Ablation: running baseline (full pipeline)")
        baseline = self._run_with_disabled(
            symbols, start_date, end_date, capital, disabled_module=None
        )
        if baseline is None:
            logger.error("Ablation: baseline run returned no result — aborting")
            return []

        base_ret      = baseline.total_return_pct
        base_win_rate = baseline.win_rate_pct
        base_trades   = baseline.total_trades

        logger.info(
            f"Ablation baseline: return={base_ret:+.2f}% "
            f"win_rate={base_win_rate:.1f}% trades={base_trades}"
        )

        # --- Per-module ablation runs ---
        results: list[AblationResult] = []

        for module in self.MODULES:
            logger.info(f"Ablation: disabling module '{module}'")
            ablated = self._run_with_disabled(
                symbols, start_date, end_date, capital, disabled_module=module
            )

            if ablated is None:
                logger.warning(f"Ablation: no result for module '{module}' — skipping")
                continue

            edge = round(base_ret - ablated.total_return_pct, 4)
            results.append(AblationResult(
                module_disabled   = module,
                baseline_return   = round(base_ret, 4),
                ablated_return    = round(ablated.total_return_pct, 4),
                edge_contribution = edge,
                baseline_win_rate = round(base_win_rate, 2),
                ablated_win_rate  = round(ablated.win_rate_pct, 2),
                n_trades_baseline = base_trades,
                n_trades_ablated  = ablated.total_trades,
            ))
            logger.info(
                f"  {module}: ablated_return={ablated.total_return_pct:+.2f}% "
                f"edge={edge:+.4f}%"
            )

        results.sort(key=lambda r: r.edge_contribution, reverse=True)
        return results

    def _run_with_disabled(
        self,
        symbols:         list,
        start_date:      str,
        end_date:        str,
        capital:         float,
        disabled_module: Optional[str],
    ) -> Optional[BacktestResult]:
        """
        Run BacktestEngine across all symbols with one module disabled.

        Disabling a module means passing ablation_config={"disabled_modules": [module]}
        to BacktestEngine.run_all(). The engine checks this dict in
        _generate_signal() and substitutes neutral (0.5 × max_pts) values
        for the disabled module's contribution.

        For the baseline run (disabled_module=None), no ablation config is passed.

        Returns a synthetic aggregated BacktestResult combining all symbols,
        or None if no individual symbol produced a result.
        """
        engine = BacktestEngine()

        ablation_cfg = (
            {"disabled_modules": [disabled_module]}
            if disabled_module is not None
            else None
        )

        # run_all returns a summary DataFrame; we need to aggregate the
        # individual BacktestResult objects. We collect them by calling
        # engine.run() per symbol and aggregating manually so we can return
        # a single BacktestResult-like object.
        individual_results = []
        for sym in symbols:
            r = engine.run(
                sym,
                start_date=start_date,
                end_date=end_date,
                capital=capital,
                ablation_config=ablation_cfg,
            )
            if r is not None:
                individual_results.append(r)

        if not individual_results:
            return None

        return self._aggregate(individual_results, disabled_module or "baseline")

    def _aggregate(
        self, results: list, label: str
    ) -> BacktestResult:
        """
        Aggregate a list of per-symbol BacktestResults into a single
        combined result, weighting each symbol equally by initial capital.

        The combined return is the mean return across symbols.
        Win rate is total_wins / total_trades across all symbols.
        """
        import numpy as np

        total_trades = sum(r.total_trades for r in results)
        total_wins   = sum(r.wins         for r in results)
        win_rate     = (total_wins / total_trades * 100) if total_trades > 0 else 0.0
        avg_return   = float(np.mean([r.total_return_pct for r in results]))
        avg_dd       = float(np.mean([r.max_drawdown_pct for r in results]))
        avg_sharpe   = float(np.mean([r.sharpe_ratio     for r in results]))
        avg_pf       = float(np.mean([r.profit_factor    for r in results]))
        avg_pnl      = float(np.mean([r.avg_trade_pnl    for r in results]))

        # Use the first result's date range and capital as representative
        rep = results[0]

        return BacktestResult(
            symbol           = f"[{label}] {len(results)} symbols",
            start_date       = rep.start_date,
            end_date         = rep.end_date,
            initial_capital  = rep.initial_capital,
            final_capital    = round(rep.initial_capital * (1 + avg_return / 100), 2),
            total_return_pct = round(avg_return, 4),
            total_trades     = total_trades,
            wins             = total_wins,
            losses           = total_trades - total_wins,
            win_rate_pct     = round(win_rate, 2),
            max_drawdown_pct = round(avg_dd, 4),
            sharpe_ratio     = round(avg_sharpe, 4),
            profit_factor    = round(avg_pf, 4),
            avg_trade_pnl    = round(avg_pnl, 2),
            best_trade_pnl   = max((r.best_trade_pnl  for r in results), default=0),
            worst_trade_pnl  = min((r.worst_trade_pnl for r in results), default=0),
            avg_hold_days    = round(
                float(np.mean([r.avg_hold_days for r in results])), 1
            ),
            equity_curve     = None,
            monthly_returns  = {},
            reasoning        = f"ablation aggregate — disabled={label}",
        )

    def print_report(self, results: list) -> None:
        """
        Print a formatted ablation table to stdout.

        Columns:
          Module | Baseline Ret% | Ablated Ret% | Edge Contrib% | Win Rate (B/A) | Trades (B/A)
        """
        if not results:
            print("Ablation: no results to display.")
            return

        sep  = "=" * 100
        hdr  = (
            f"{'Module':<22} {'Base Ret%':>10} {'Ablated Ret%':>14} "
            f"{'Edge%':>10} {'WinR B%':>9} {'WinR A%':>9} "
            f"{'Trades B':>9} {'Trades A':>9}"
        )
        print(f"\n{sep}")
        print(f"  ABLATION REPORT — edge contribution per module")
        print(f"  Positive edge = module adds value | Negative = module hurts")
        print(sep)
        print(hdr)
        print("-" * 100)

        for r in results:
            edge_str = f"{r.edge_contribution:>+.4f}"
            flag     = " <-- REMOVE?" if r.edge_contribution < -0.5 else (
                       " *** HIGH VALUE" if r.edge_contribution > 2.0 else ""
            )
            print(
                f"  {r.module_disabled:<20} "
                f"{r.baseline_return:>10.2f} "
                f"{r.ablated_return:>14.2f} "
                f"{edge_str:>10} "
                f"{r.baseline_win_rate:>9.1f} "
                f"{r.ablated_win_rate:>9.1f} "
                f"{r.n_trades_baseline:>9} "
                f"{r.n_trades_ablated:>9}"
                f"{flag}"
            )

        print(sep)

        # Summary line
        if results:
            best = results[0]
            worst = results[-1]
            print(f"\n  Highest value module : {best.module_disabled} "
                  f"(edge {best.edge_contribution:+.4f}%)")
            print(f"  Lowest value module  : {worst.module_disabled} "
                  f"(edge {worst.edge_contribution:+.4f}%)")
            print()


# =============================================================================
# CLI — python -m backtest.ablation
# =============================================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Module ablation test runner")
    parser.add_argument(
        "--symbols", nargs="+",
        default=["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"],
        help="NSE symbols to test",
    )
    parser.add_argument("--years",  type=int,   default=2,   help="Years of history")
    parser.add_argument("--capital",type=float,  default=BACKTEST_CAPITAL, help="Starting capital")
    args = parser.parse_args()

    runner  = AblationRunner()
    results = runner.run(args.symbols, years=args.years, capital=args.capital)
    runner.print_report(results)
