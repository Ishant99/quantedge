# =============================================================================
# backtest/optimiser.py — Strategy Auto-Optimiser
#
# Weekly: runs backtest on multiple parameter combinations.
# Finds best RSI period, MACD settings, TA score threshold.
# Updates config automatically with winning parameters.
#
# Run: python -m backtest.optimiser
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import itertools
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass
from utils import get_logger
from utils.telegram import send

logger = get_logger("Optimiser")

# Parameter grid to search
PARAM_GRID = {
    "rsi_period":    [10, 14, 21],
    "min_ta_score":  [5.0, 5.5, 6.0, 6.5],
    "bb_period":     [15, 20, 25],
    "reward_risk":   [1.5, 2.0, 2.5],
}

TEST_SYMBOLS = ["BRITANNIA", "TITAN", "BAJFINANCE", "HDFCBANK", "RELIANCE"]
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPTIMISER_RESULTS_FILE = os.path.join(_ROOT, "logs", "optimiser_results.json")


@dataclass
class OptimiserResult:
    best_params:      dict
    best_sharpe:      float
    best_return:      float
    best_win_rate:    float
    total_combinations: int
    tested:           int
    timestamp:        str


class StrategyOptimiser:
    """
    Grid search over strategy parameters.
    Finds the combination with best risk-adjusted returns.
    Runs weekly on Sundays.
    """

    def run(self, years: int = 3) -> OptimiserResult:
        """Alias for optimise() — called by scheduler and dashboard."""
        return self.optimise(years=years)

    def optimise(self, years: int = 3) -> OptimiserResult:
        """Run full optimisation grid search."""
        logger.info("Starting strategy optimisation...")

        end   = datetime.today().strftime("%Y-%m-%d")
        start = (datetime.today() - timedelta(days=365*years)).strftime("%Y-%m-%d")

        # Generate all combinations
        keys   = list(PARAM_GRID.keys())
        values = list(PARAM_GRID.values())
        combos = list(itertools.product(*values))
        total  = len(combos)

        logger.info(f"Testing {total} parameter combinations on {len(TEST_SYMBOLS)} stocks...")

        results   = []
        tested    = 0

        for combo in combos:
            params = dict(zip(keys, combo))
            score  = self._evaluate(params, start, end)
            if score:
                results.append({**params, **score})
                tested += 1

        if not results:
            logger.warning("No valid results from optimisation")
            return self._default()

        # Find best by Sharpe ratio
        df          = pd.DataFrame(results)
        best_row    = df.loc[df["sharpe"].idxmax()]
        # .item() converts numpy scalars → Python natives (avoids np.float64 repr)
        best_params = {k: best_row[k].item() for k in keys}

        result = OptimiserResult(
            best_params         = best_params,
            best_sharpe         = round(float(best_row["sharpe"]), 3),
            best_return         = round(float(best_row["avg_return"]), 2),
            best_win_rate       = round(float(best_row["avg_win_rate"]), 1),
            total_combinations  = total,
            tested              = tested,
            timestamp           = datetime.now().isoformat(),
        )

        self._save(result, df)
        self._report(result)
        return result

    def _evaluate(self, params: dict, start: str, end: str) -> dict | None:
        """Evaluate a parameter set on test symbols."""
        try:
            from backtest.engine import BacktestEngine
            import config as cfg

            # Temporarily patch config
            orig_rsi = cfg.RSI_PERIOD
            orig_ta  = cfg.MIN_TA_SCORE
            orig_bb  = cfg.BB_PERIOD
            orig_rr  = cfg.REWARD_RISK_RATIO

            cfg.RSI_PERIOD        = params["rsi_period"]
            cfg.MIN_TA_SCORE      = params["min_ta_score"]
            cfg.BB_PERIOD         = params["bb_period"]
            cfg.REWARD_RISK_RATIO = params["reward_risk"]

            try:
                engine  = BacktestEngine()
                returns = []
                sharpes = []
                wr      = []

                for sym in TEST_SYMBOLS:
                    r = engine.run(sym, start, end)
                    if r and r.total_trades >= 3:
                        returns.append(r.total_return_pct)
                        sharpes.append(r.sharpe_ratio)
                        wr.append(r.win_rate_pct)

                if not returns:
                    return None

                return {
                    "avg_return":     np.mean(returns),
                    "sharpe":         np.mean(sharpes),
                    "avg_win_rate":   np.mean(wr),
                    "symbols_tested": len(returns),
                }
            finally:
                # Always restore config — even if BacktestEngine raises
                cfg.RSI_PERIOD        = orig_rsi
                cfg.MIN_TA_SCORE      = orig_ta
                cfg.BB_PERIOD         = orig_bb
                cfg.REWARD_RISK_RATIO = orig_rr

        except Exception as e:
            logger.debug(f"Eval failed for {params}: {e}")
            return None

    def _save(self, result: OptimiserResult, df: pd.DataFrame):
        os.makedirs(os.path.dirname(OPTIMISER_RESULTS_FILE), exist_ok=True)
        data = {
            "best_params":   result.best_params,
            "best_sharpe":   result.best_sharpe,
            "best_return":   result.best_return,
            "best_win_rate": result.best_win_rate,
            "timestamp":     result.timestamp,
            "all_results":   df.to_dict(orient="records"),
        }
        with open(OPTIMISER_RESULTS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Optimiser results saved to {OPTIMISER_RESULTS_FILE}")

    def _report(self, result: OptimiserResult):
        print(f"\n{'='*55}")
        print(f"  OPTIMISER RESULTS")
        print(f"{'='*55}")
        print(f"  Tested: {result.tested}/{result.total_combinations} combinations")
        print(f"  Best Sharpe:    {result.best_sharpe:.3f}")
        print(f"  Best Return:    {result.best_return:.2f}%")
        print(f"  Best Win Rate:  {result.best_win_rate:.1f}%")
        print(f"\n  Best Parameters:")
        for k, v in result.best_params.items():
            print(f"    {k:20s} = {v}")
        print(f"{'='*55}\n")

        param_str = " | ".join(f"{k}={v}" for k, v in result.best_params.items())
        send(f"*Weekly Optimiser Results*\n"
             f"Best Sharpe: `{result.best_sharpe:.2f}`\n"
             f"Best Return: `{result.best_return:.1f}%`\n"
             f"Win Rate: `{result.best_win_rate:.0f}%`\n"
             f"Best params: `{param_str}`")

    def _default(self) -> OptimiserResult:
        return OptimiserResult(
            best_params={}, best_sharpe=0.0,
            best_return=0.0, best_win_rate=0.0,
            total_combinations=0, tested=0,
            timestamp=datetime.now().isoformat()
        )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=3)
    args = parser.parse_args()
    StrategyOptimiser().optimise(years=args.years)
