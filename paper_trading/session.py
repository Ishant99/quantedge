"""paper_trading/session.py — 6-month paper trading session manager.

Tracks the start date, current phase, milestone gate definitions, and
overall progress toward live-trading readiness.  State is persisted to
logs/paper_session.json so restarts are idempotent.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from config import SQLITE_DB_FILE

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGS_DIR = os.path.join(_ROOT, "logs")
SESSION_FILE = os.path.join(_LOGS_DIR, "paper_session.json")

# Approximate trading days per calendar month (NSE ~21 per month)
_TRADING_DAYS_PER_MONTH = 21
_TOTAL_TRADING_DAYS = 126  # 6 months × 21


@dataclass
class MilestoneGate:
    month: int
    min_trades: int
    min_win_rate: float
    max_drawdown: float
    min_profit_factor: float | None = None
    min_sharpe: float | None = None
    max_consec_losses: int | None = None


# Escalating gates for each month
MILESTONE_GATES: list[MilestoneGate] = [
    MilestoneGate(1, min_trades=20,  min_win_rate=0.48, max_drawdown=0.20),
    MilestoneGate(2, min_trades=50,  min_win_rate=0.50, max_drawdown=0.18, min_profit_factor=1.1),
    MilestoneGate(3, min_trades=80,  min_win_rate=0.52, max_drawdown=0.15, min_profit_factor=1.2,  min_sharpe=0.5),
    MilestoneGate(4, min_trades=120, min_win_rate=0.52, max_drawdown=0.12, min_profit_factor=1.3,  min_sharpe=0.8),
    MilestoneGate(5, min_trades=160, min_win_rate=0.54, max_drawdown=0.10, min_profit_factor=1.4,  min_sharpe=1.0),
    MilestoneGate(6, min_trades=200, min_win_rate=0.55, max_drawdown=0.10, min_profit_factor=1.5,  min_sharpe=1.2, max_consec_losses=3),
]


@dataclass
class GateResult:
    name: str
    passed: bool
    actual: float | int | None
    threshold: float | int | None
    detail: str = ""


@dataclass
class PhaseStatus:
    month: int
    gates: list[GateResult]
    all_passed: bool
    score: float  # 0-100


@dataclass
class SessionStatus:
    start_date: str
    capital_inr: float
    days_elapsed: int
    trading_days_elapsed: int
    days_remaining: int
    trading_days_remaining: int
    current_month: int
    phase_status: PhaseStatus
    is_complete: bool
    estimated_live_date: str
    summary: str


def _estimate_trading_days_static(start: date, end: date) -> int:
    """Module-level helper: count Mon–Fri business days between two dates."""
    days = 0
    current = start
    while current < end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


class PaperTradingSession:
    """Manages the 6-month paper trading validation program."""

    def __init__(self) -> None:
        os.makedirs(_LOGS_DIR, exist_ok=True)
        self._state: dict[str, Any] = self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_session(self, capital_inr: float = 1_000_000.0) -> dict[str, Any]:
        """Initialise the session.  Idempotent — won't reset if already running."""
        if self._state.get("start_date"):
            return {"status": "already_running", "start_date": self._state["start_date"]}

        self._state = {
            "start_date": date.today().isoformat(),
            "capital_inr": capital_inr,
            "milestone_results": {},
            "created_at": datetime.utcnow().isoformat(),
        }
        self._save()
        return {"status": "started", "start_date": self._state["start_date"]}

    def get_status(self) -> dict[str, Any]:
        """Return current session status including phase and gate summary."""
        if not self._state.get("start_date"):
            return {"status": "not_started"}

        start = date.fromisoformat(self._state["start_date"])
        today = date.today()
        calendar_elapsed = (today - start).days
        trading_elapsed = self._estimate_trading_days(start, today)
        trading_remaining = max(0, _TOTAL_TRADING_DAYS - trading_elapsed)
        current_month = min(6, max(1, (trading_elapsed // _TRADING_DAYS_PER_MONTH) + 1))

        phase = self._evaluate_phase(current_month)
        is_complete = trading_elapsed >= _TOTAL_TRADING_DAYS

        if trading_remaining > 0:
            days_per_trading = calendar_elapsed / max(1, trading_elapsed)
            est_live = today + timedelta(days=int(trading_remaining * days_per_trading))
        else:
            est_live = today

        status = SessionStatus(
            start_date=self._state["start_date"],
            capital_inr=self._state.get("capital_inr", 1_000_000),
            days_elapsed=calendar_elapsed,
            trading_days_elapsed=trading_elapsed,
            days_remaining=max(0, int(trading_remaining * 1.4)),
            trading_days_remaining=trading_remaining,
            current_month=current_month,
            phase_status=phase,
            is_complete=is_complete,
            estimated_live_date=est_live.isoformat(),
            summary=self._summary_text(current_month, trading_elapsed, phase),
        )
        return asdict(status)

    def generate_monthly_report(self, month_num: int) -> str:
        """Return a markdown-formatted report for the given month."""
        from paper_trading.performance_tracker import MonthlyPerformanceTracker

        tracker = MonthlyPerformanceTracker()
        data = tracker.get_monthly_summary(month_num)
        gate = MILESTONE_GATES[month_num - 1]
        phase = self._evaluate_phase_from_metrics(month_num, data)

        lines = [
            f"# QuantEdge Paper Trading — Month {month_num} Report",
            f"**Period:** {data.get('period_start', 'N/A')} → {data.get('period_end', 'N/A')}",
            "",
            "## Performance",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Trades | {data.get('trade_count', 0)} |",
            f"| Win Rate | {data.get('win_rate', 0):.1%} |",
            f"| Net PnL | ₹{data.get('net_pnl', 0):,.0f} |",
            f"| Profit Factor | {data.get('profit_factor', 0):.2f} |",
            f"| Max Drawdown | {data.get('max_drawdown', 0):.1%} |",
            f"| Sharpe Ratio | {data.get('sharpe', 0):.2f} |",
            "",
            "## Milestone Gates",
        ]

        for g in phase.gates:
            icon = "✅" if g.passed else "❌"
            lines.append(f"- {icon} **{g.name}**: {g.detail}")

        status = "PASSED" if phase.all_passed else "FAILED"
        lines += [
            "",
            f"**Month {month_num} Gate Status: {status}** (Score: {phase.score:.0f}/100)",
            "",
            "## Regime Breakdown",
        ]

        for regime, stats in data.get("regime_breakdown", {}).items():
            lines.append(f"- **{regime}**: {stats.get('trades', 0)} trades, PnL ₹{stats.get('pnl', 0):,.0f}")

        if data.get("best_trade"):
            bt = data["best_trade"]
            lines += ["", f"**Best Trade:** {bt.get('symbol')} +₹{bt.get('pnl', 0):,.0f}"]
        if data.get("worst_trade"):
            wt = data["worst_trade"]
            lines += [f"**Worst Trade:** {wt.get('symbol')} ₹{wt.get('pnl', 0):,.0f}"]

        report = "\n".join(lines)

        # Persist milestone result
        self._state.setdefault("milestone_results", {})[str(month_num)] = {
            "passed": phase.all_passed,
            "score": phase.score,
            "evaluated_at": datetime.utcnow().isoformat(),
        }
        self._save()
        return report

    def record_milestone(self, month_num: int, passed: bool, score: float) -> None:
        """Persist a milestone result (called by scheduler)."""
        self._state.setdefault("milestone_results", {})[str(month_num)] = {
            "passed": passed,
            "score": score,
            "evaluated_at": datetime.utcnow().isoformat(),
        }
        self._save()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evaluate_phase(self, month_num: int) -> PhaseStatus:
        from paper_trading.performance_tracker import MonthlyPerformanceTracker

        try:
            tracker = MonthlyPerformanceTracker()
            data = tracker.get_monthly_summary(month_num)
        except Exception:
            data = {}
        return self._evaluate_phase_from_metrics(month_num, data)

    def _evaluate_phase_from_metrics(self, month_num: int, data: dict) -> PhaseStatus:
        gate = MILESTONE_GATES[month_num - 1]
        results: list[GateResult] = []

        # Trade count
        tc = data.get("trade_count", 0)
        results.append(GateResult(
            "min_trades", tc >= gate.min_trades, tc, gate.min_trades,
            f"{tc} trades (need ≥{gate.min_trades})",
        ))

        # Win rate
        wr = data.get("win_rate", 0.0)
        results.append(GateResult(
            "win_rate", wr >= gate.min_win_rate, round(wr, 4), gate.min_win_rate,
            f"{wr:.1%} (need ≥{gate.min_win_rate:.0%})",
        ))

        # Drawdown
        dd = data.get("max_drawdown", 1.0)
        results.append(GateResult(
            "max_drawdown", dd <= gate.max_drawdown, round(dd, 4), gate.max_drawdown,
            f"{dd:.1%} (need ≤{gate.max_drawdown:.0%})",
        ))

        # Profit factor (optional gate)
        if gate.min_profit_factor is not None:
            pf = data.get("profit_factor", 0.0)
            results.append(GateResult(
                "profit_factor", pf >= gate.min_profit_factor, round(pf, 4), gate.min_profit_factor,
                f"{pf:.2f} (need ≥{gate.min_profit_factor})",
            ))

        # Sharpe (optional gate)
        if gate.min_sharpe is not None:
            sh = data.get("sharpe", 0.0)
            results.append(GateResult(
                "sharpe_ratio", sh >= gate.min_sharpe, round(sh, 4), gate.min_sharpe,
                f"{sh:.2f} (need ≥{gate.min_sharpe})",
            ))

        # Consecutive losses (optional gate)
        if gate.max_consec_losses is not None:
            cl = data.get("max_consec_losses", 0)
            results.append(GateResult(
                "consec_losses", cl <= gate.max_consec_losses, cl, gate.max_consec_losses,
                f"{cl} (need ≤{gate.max_consec_losses})",
            ))

        all_passed = all(r.passed for r in results)
        score = (sum(r.passed for r in results) / len(results)) * 100 if results else 0.0

        return PhaseStatus(month=month_num, gates=results, all_passed=all_passed, score=score)

    def _estimate_trading_days(self, start: date, end: date) -> int:
        """Count Mon–Fri business days between two dates (NSE approximation)."""
        days = 0
        current = start
        while current < end:
            if current.weekday() < 5:
                days += 1
            current += timedelta(days=1)
        return days

    def _summary_text(self, month: int, trading_elapsed: int, phase: PhaseStatus) -> str:
        pct = min(100, int(trading_elapsed / _TOTAL_TRADING_DAYS * 100))
        gate_txt = "PASS" if phase.all_passed else "FAIL"
        return (
            f"Month {month} | {trading_elapsed}/{_TOTAL_TRADING_DAYS} trading days ({pct}%) | "
            f"Current month gates: {gate_txt} ({phase.score:.0f}/100)"
        )

    def _load(self) -> dict[str, Any]:
        try:
            with open(SESSION_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        os.makedirs(_LOGS_DIR, exist_ok=True)
        with open(SESSION_FILE, "w") as f:
            json.dump(self._state, f, indent=2)
