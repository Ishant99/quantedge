# =============================================================================
# readiness/checker.py — Phase 2 Readiness Checker
#
# Monitors paper trading performance and tells you exactly when it is
# safe to go live. Runs automatically after every agent session.
#
# Green light criteria (all must pass):
#   1. Min 20 closed trades
#   2. Win rate >= 52%
#   3. Profit factor >= 1.2
#   4. Max drawdown <= 15%
#   5. Sharpe ratio >= 0.8
#   6. Consecutive losing days <= 3
#   7. At least 15 trading days of data
#   8. Backtest return >= 10% (if backtest has been run)
#
# Usage:
#   python -m readiness.checker           # print full report
#   python -m readiness.checker --watch   # re-check every day
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import sqlite3
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

from config import (
    SQLITE_DB_FILE, BACKTEST_RESULTS_DIR,
    VIRTUAL_CAPITAL, TRADING_MODE
)
from utils import get_logger

logger = get_logger("ReadinessChecker")


# ------------------------------------------------------------------
# Gate definitions — tweak thresholds here if needed
# ------------------------------------------------------------------
GATES = {
    "min_trades":          {"required": 20,   "label": "Minimum closed trades"},
    "win_rate":            {"required": 52.0, "label": "Win rate (%)"},
    "profit_factor":       {"required": 1.2,  "label": "Profit factor"},
    "max_drawdown":        {"required": 15.0, "label": "Max drawdown (%) — lower is better"},
    "sharpe_ratio":        {"required": 0.8,  "label": "Sharpe ratio"},
    "max_consec_losses":   {"required": 3,    "label": "Max consecutive losing days — lower is better"},
    "min_trading_days":    {"required": 15,   "label": "Minimum trading days"},
    "backtest_return":     {"required": 10.0, "label": "Backtest total return (%)"},
}


@dataclass
class GateResult:
    name:       str
    label:      str
    required:   float
    actual:     float
    passed:     bool
    message:    str


@dataclass
class ReadinessReport:
    timestamp:      str
    gates:          list[GateResult]
    passed_count:   int
    total_gates:    int
    is_ready:       bool
    recommendation: str
    next_steps:     list[str]
    days_remaining: Optional[int]  # estimated days until ready


class ReadinessChecker:
    """
    Monitors paper trading metrics and generates a Phase 2 go/no-go report.
    All data sourced from SQLite trade log — no extra dependencies.
    """

    def __init__(self):
        self.db_path = SQLITE_DB_FILE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self) -> ReadinessReport:
        """Run all gate checks and return a full readiness report."""
        metrics = self._get_metrics()
        gates   = self._evaluate_gates(metrics)

        passed        = sum(1 for g in gates if g.passed)
        total         = len(gates)
        is_ready      = passed == total
        days_remaining= self._estimate_days(metrics, gates)
        recommendation= self._recommendation(is_ready, passed, total, metrics)
        next_steps    = self._next_steps(gates, metrics)

        report = ReadinessReport(
            timestamp      = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            gates          = gates,
            passed_count   = passed,
            total_gates    = total,
            is_ready       = is_ready,
            recommendation = recommendation,
            next_steps     = next_steps,
            days_remaining = days_remaining,
        )

        self._save_report(report)
        return report

    def print_report(self, report: ReadinessReport):
        """Print a rich formatted report to console."""
        width = 62
        print("\n" + "=" * width)
        print("  PHASE 2 READINESS REPORT")
        print(f"  {report.timestamp}")
        print("=" * width)

        # Overall verdict
        if report.is_ready:
            print("\n  *** GREEN LIGHT — You are ready for Phase 2! ***\n")
        else:
            remaining = f"~{report.days_remaining} days" if report.days_remaining else "unknown"
            print(f"\n  RED LIGHT — {report.passed_count}/{report.total_gates} gates passed")
            print(f"  Estimated time to ready: {remaining}\n")

        # Gate breakdown
        print(f"  {'GATE':<35} {'REQUIRED':>10} {'ACTUAL':>10}  STATUS")
        print(f"  {'-'*35} {'-'*10} {'-'*10}  ------")

        for g in report.gates:
            status = "PASS" if g.passed else "FAIL"
            print(f"  {g.label:<35} {g.required:>10.1f} {g.actual:>10.1f}  {status}")

        # Recommendation
        print(f"\n  RECOMMENDATION:")
        print(f"  {report.recommendation}")

        # Next steps
        if report.next_steps:
            print(f"\n  NEXT STEPS:")
            for step in report.next_steps:
                print(f"    {step}")

        print("\n" + "=" * width + "\n")

    # ------------------------------------------------------------------
    # Metrics from SQLite
    # ------------------------------------------------------------------

    def _get_metrics(self) -> dict:
        """Pull all required metrics from the trade database."""
        if not os.path.exists(self.db_path):
            return self._empty_metrics()

        with sqlite3.connect(self.db_path) as conn:

            # Closed trades
            trades = conn.execute("""
                SELECT pnl, pnl_pct, entry_time, exit_time
                FROM trades WHERE status='closed'
                ORDER BY id
            """).fetchall()

            # Snapshots for drawdown + Sharpe
            snapshots = conn.execute("""
                SELECT portfolio_value, timestamp
                FROM snapshots ORDER BY id
            """).fetchall()

            # Signals count
            total_signals = conn.execute(
                "SELECT COUNT(*) FROM signals"
            ).fetchone()[0]

            # Trading days (distinct dates with signals)
            trading_days = conn.execute("""
                SELECT COUNT(DISTINCT DATE(timestamp))
                FROM signals
            """).fetchone()[0]

        if not trades:
            return self._empty_metrics()

        pnls      = [t[0] for t in trades]
        wins      = [p for p in pnls if p > 0]
        losses    = [p for p in pnls if p <= 0]
        win_rate  = len(wins) / len(trades) * 100 if trades else 0
        avg_win   = sum(wins)   / len(wins)   if wins   else 0
        avg_loss  = abs(sum(losses) / len(losses)) if losses else 1
        pf        = avg_win / avg_loss if avg_loss else 0

        # Max drawdown from snapshots
        vals = [s[0] for s in snapshots] if snapshots else [VIRTUAL_CAPITAL]
        max_dd = self._calc_drawdown(vals)

        # Sharpe from snapshot daily returns
        sharpe = self._calc_sharpe(vals)

        # Consecutive losses
        consec = self._max_consecutive_losses(pnls)

        # Backtest result (best return from saved results)
        bt_return = self._best_backtest_return()

        return {
            "total_trades":     len(trades),
            "win_rate":         round(win_rate, 2),
            "profit_factor":    round(pf, 2),
            "max_drawdown":     round(max_dd, 2),
            "sharpe_ratio":     round(sharpe, 2),
            "max_consec_losses":consec,
            "trading_days":     trading_days,
            "total_signals":    total_signals,
            "backtest_return":  bt_return,
            "total_pnl":        round(sum(pnls), 2),
        }

    def _empty_metrics(self) -> dict:
        return {
            "total_trades": 0, "win_rate": 0, "profit_factor": 0,
            "max_drawdown": 0, "sharpe_ratio": 0, "max_consec_losses": 0,
            "trading_days": 0, "total_signals": 0, "backtest_return": 0,
            "total_pnl": 0,
        }

    # ------------------------------------------------------------------
    # Gate evaluation
    # ------------------------------------------------------------------

    def _evaluate_gates(self, m: dict) -> list[GateResult]:
        results = []

        # 1. Min trades
        actual = m["total_trades"]
        req    = GATES["min_trades"]["required"]
        results.append(GateResult(
            name="min_trades", label=GATES["min_trades"]["label"],
            required=req, actual=actual,
            passed=actual >= req,
            message=f"Need {max(0, req-actual):.0f} more trades" if actual < req else "OK",
        ))

        # 2. Win rate
        actual = m["win_rate"]
        req    = GATES["win_rate"]["required"]
        results.append(GateResult(
            name="win_rate", label=GATES["win_rate"]["label"],
            required=req, actual=actual,
            passed=actual >= req,
            message=f"{actual:.1f}% — need {req:.1f}%" if actual < req else "OK",
        ))

        # 3. Profit factor
        actual = m["profit_factor"]
        req    = GATES["profit_factor"]["required"]
        results.append(GateResult(
            name="profit_factor", label=GATES["profit_factor"]["label"],
            required=req, actual=actual,
            passed=actual >= req,
            message=f"{actual:.2f} — need {req:.1f}" if actual < req else "OK",
        ))

        # 4. Max drawdown (lower is better — pass if BELOW threshold)
        actual = m["max_drawdown"]
        req    = GATES["max_drawdown"]["required"]
        results.append(GateResult(
            name="max_drawdown", label=GATES["max_drawdown"]["label"],
            required=req, actual=actual,
            passed=actual <= req,
            message=f"{actual:.1f}% drawdown — must stay under {req:.0f}%" if actual > req else "OK",
        ))

        # 5. Sharpe ratio
        actual = m["sharpe_ratio"]
        req    = GATES["sharpe_ratio"]["required"]
        results.append(GateResult(
            name="sharpe_ratio", label=GATES["sharpe_ratio"]["label"],
            required=req, actual=actual,
            passed=actual >= req,
            message=f"{actual:.2f} — need {req:.1f}" if actual < req else "OK",
        ))

        # 6. Consecutive losses (lower is better — pass if BELOW threshold)
        actual = m["max_consec_losses"]
        req    = GATES["max_consec_losses"]["required"]
        results.append(GateResult(
            name="max_consec_losses", label=GATES["max_consec_losses"]["label"],
            required=req, actual=actual,
            passed=actual <= req,
            message=f"{actual:.0f} consecutive losses — must stay under {req:.0f}" if actual > req else "OK",
        ))

        # 7. Trading days
        actual = m["trading_days"]
        req    = GATES["min_trading_days"]["required"]
        results.append(GateResult(
            name="min_trading_days", label=GATES["min_trading_days"]["label"],
            required=req, actual=actual,
            passed=actual >= req,
            message=f"{max(0,req-actual):.0f} more trading days needed" if actual < req else "OK",
        ))

        # 8. Backtest return
        actual = m["backtest_return"]
        req    = GATES["backtest_return"]["required"]
        results.append(GateResult(
            name="backtest_return", label=GATES["backtest_return"]["label"],
            required=req, actual=actual,
            passed=actual >= req,
            message="Run: python -m backtest.engine --all --years 3" if actual == 0 else (
                f"{actual:.1f}% — need {req:.0f}%" if actual < req else "OK"
            ),
        ))

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _calc_drawdown(self, values: list) -> float:
        if len(values) < 2:
            return 0.0
        peak   = values[0]
        max_dd = 0.0
        for v in values:
            peak   = max(peak, v)
            dd     = (peak - v) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        return max_dd

    def _calc_sharpe(self, values: list, risk_free: float = 0.07) -> float:
        if len(values) < 10:
            return 0.0
        import numpy as np
        vals      = pd.Series(values)
        daily_ret = vals.pct_change().dropna()
        rf_daily  = risk_free / 252
        excess    = daily_ret - rf_daily
        return float(excess.mean() / excess.std() * (252 ** 0.5)) if excess.std() > 0 else 0.0

    def _max_consecutive_losses(self, pnls: list) -> int:
        max_c = cur = 0
        for p in pnls:
            if p <= 0:
                cur  += 1
                max_c = max(max_c, cur)
            else:
                cur = 0
        return max_c

    def _best_backtest_return(self) -> float:
        """Find highest return across all saved backtest JSON files."""
        if not os.path.exists(BACKTEST_RESULTS_DIR):
            return 0.0
        best = 0.0
        for fname in os.listdir(BACKTEST_RESULTS_DIR):
            if not fname.endswith("_backtest.json"):
                continue
            try:
                with open(os.path.join(BACKTEST_RESULTS_DIR, fname)) as f:
                    data = json.load(f)
                    ret  = data["result"].get("total_return_pct", 0)
                    best = max(best, ret)
            except Exception:
                pass
        return round(best, 2)

    def _estimate_days(self, metrics: dict, gates: list[GateResult]) -> Optional[int]:
        """Rough estimate of how many more trading days needed."""
        failing = [g for g in gates if not g.passed]
        if not failing:
            return 0

        estimates = []
        trades_per_day = max(metrics["total_trades"] / max(metrics["trading_days"], 1), 0.5)

        for g in failing:
            if g.name == "min_trades":
                needed = GATES["min_trades"]["required"] - metrics["total_trades"]
                estimates.append(int(needed / max(trades_per_day, 0.3)))
            elif g.name == "min_trading_days":
                estimates.append(int(GATES["min_trading_days"]["required"] - metrics["trading_days"]))
            elif g.name == "backtest_return":
                estimates.append(0)  # can run today
            else:
                estimates.append(5)  # generic estimate

        return max(estimates) if estimates else None

    def _recommendation(self, is_ready: bool, passed: int, total: int, metrics: dict) -> str:
        if is_ready:
            return (
                "All gates passed! Set TRADING_MODE=live in .env and "
                "start with Rs.10,000-20,000 only. Monitor daily."
            )
        pct = passed / total * 100
        if pct >= 75:
            return "Almost there! A few more weeks of paper trading should clear the remaining gates."
        elif pct >= 50:
            return "Good progress. Keep paper trading daily and run backtests to validate your strategy."
        elif metrics["trading_days"] < 5:
            return "Too early to judge. Run the agent daily for at least 2 weeks before evaluating."
        else:
            return "Strategy needs improvement. Review which signals are losing and adjust MIN_TA_SCORE in config.py."

    def _next_steps(self, gates: list[GateResult], metrics: dict) -> list[str]:
        steps = []
        failing = [g for g in gates if not g.passed]

        for g in failing:
            if g.name == "min_trades":
                steps.append(f"[ ] Run agent daily — need {int(GATES['min_trades']['required'] - metrics['total_trades'])} more closed trades")
            elif g.name == "win_rate":
                steps.append(f"[ ] Win rate {metrics['win_rate']:.1f}% is below 52% — check which stocks are losing most")
            elif g.name == "profit_factor":
                steps.append(f"[ ] Profit factor {metrics['profit_factor']:.2f} too low — consider raising MIN_TA_SCORE to 7.0 in config.py")
            elif g.name == "max_drawdown":
                steps.append(f"[ ] Drawdown {metrics['max_drawdown']:.1f}% too high — consider lowering RISK_PER_TRADE_PCT to 0.01")
            elif g.name == "sharpe_ratio":
                steps.append(f"[ ] Sharpe {metrics['sharpe_ratio']:.2f} too low — strategy is too volatile for its returns")
            elif g.name == "max_consec_losses":
                steps.append(f"[ ] {metrics['max_consec_losses']} consecutive losses — review stop-loss levels in config.py")
            elif g.name == "min_trading_days":
                steps.append(f"[ ] Only {metrics['trading_days']} trading days — run agent daily until you have 15+ days")
            elif g.name == "backtest_return":
                steps.append("[ ] Run backtests: python -m backtest.engine --all --years 3")

        if not steps:
            steps.append("[x] Set TRADING_MODE=live in your .env file")
            steps.append("[x] Add KITE_API_KEY and KITE_API_SECRET to .env")
            steps.append("[x] Start with Rs.10,000 only — scale up after 1 profitable month")
            steps.append("[x] Run: python scheduler\\scheduler.py to automate")

        return steps

    def _save_report(self, report: ReadinessReport):
        """Save report as JSON for dashboard to read."""
        os.makedirs("logs", exist_ok=True)
        data = {
            "timestamp":     report.timestamp,
            "is_ready":      report.is_ready,
            "passed":        report.passed_count,
            "total":         report.total_gates,
            "days_remaining":report.days_remaining,
            "recommendation":report.recommendation,
            "gates": [
                {
                    "name":    g.name,
                    "label":   g.label,
                    "required":g.required,
                    "actual":  g.actual,
                    "passed":  g.passed,
                    "message": g.message,
                }
                for g in report.gates
            ],
        }
        with open("logs/readiness_report.json", "w") as f:
            json.dump(data, f, indent=2)


# =============================================================================
# CLI
# =============================================================================
if __name__ == "__main__":
    import argparse, time
    parser = argparse.ArgumentParser(description="Phase 2 Readiness Checker")
    parser.add_argument("--watch", action="store_true",
                        help="Re-check every 24 hours")
    args = parser.parse_args()

    checker = ReadinessChecker()

    while True:
        report = checker.check()
        checker.print_report(report)

        if not args.watch:
            break

        print("Next check in 24 hours. Press Ctrl+C to stop.\n")
        time.sleep(86400)
