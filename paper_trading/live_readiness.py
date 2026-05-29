"""paper_trading/live_readiness.py — Strict go/no-go gate for live trading.

Aggregates all 6-month paper trading evidence into a single LiveReadinessReport.
ALL blocking conditions must pass before `go_live` is True.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from paper_trading.session import (
    MILESTONE_GATES,
    _TOTAL_TRADING_DAYS,
    _estimate_trading_days_static,
    PaperTradingSession,
    SESSION_FILE,
)
from paper_trading.performance_tracker import MonthlyPerformanceTracker

import json
import os

_MIN_CONSECUTIVE_LOSING_MONTHS = 2
_MIN_POSITIVE_MONTHS = 3
_MIN_REGIMES = 2


@dataclass
class LiveReadinessReport:
    go_live: bool
    score: int  # 0-100
    blocking_reasons: list[str]
    advisory_notes: list[str]
    gate_details: list[dict]
    estimated_ready_date: str
    evaluated_at: str = field(default_factory=lambda: date.today().isoformat())


class LiveReadinessGate:
    """Evaluates whether the paper trading session is ready for live trading."""

    def __init__(self) -> None:
        self._session = PaperTradingSession()
        self._tracker = MonthlyPerformanceTracker()

    def evaluate(self) -> LiveReadinessReport:
        status = self._session.get_status()
        if status.get("status") == "not_started":
            return LiveReadinessReport(
                go_live=False,
                score=0,
                blocking_reasons=["Paper trading session has not been started."],
                advisory_notes=["Call PaperTradingSession().start_session() to begin."],
                gate_details=[],
                estimated_ready_date="unknown",
            )

        blocking: list[str] = []
        advisory: list[str] = []
        gate_details: list[dict] = []
        points_earned = 0
        total_points = 0

        # --- Gate 1: Time requirement ---
        trading_elapsed = status.get("trading_days_elapsed", 0)
        total_points += 15
        if trading_elapsed >= _TOTAL_TRADING_DAYS:
            points_earned += 15
            gate_details.append({"gate": "trading_days", "passed": True,
                                  "detail": f"{trading_elapsed} trading days elapsed (≥{_TOTAL_TRADING_DAYS} required)"})
        else:
            remaining = _TOTAL_TRADING_DAYS - trading_elapsed
            blocking.append(f"Insufficient paper trading time: {trading_elapsed}/{_TOTAL_TRADING_DAYS} trading days elapsed.")
            gate_details.append({"gate": "trading_days", "passed": False,
                                  "detail": f"{trading_elapsed}/{_TOTAL_TRADING_DAYS} days — {remaining} more needed"})

        # --- Gate 2: Month 6 milestone gates all pass ---
        m6_data = self._tracker.get_monthly_summary(6)
        m6_gate = MILESTONE_GATES[5]
        m6_phase = self._session._evaluate_phase_from_metrics(6, m6_data)
        total_points += 30
        if m6_phase.all_passed:
            points_earned += 30
            gate_details.append({"gate": "month6_milestones", "passed": True,
                                  "detail": f"All Month 6 gates pass (score {m6_phase.score:.0f}/100)"})
        else:
            failed = [g.name for g in m6_phase.gates if not g.passed]
            blocking.append(f"Month 6 milestone gates failing: {', '.join(failed)}")
            gate_details.append({"gate": "month6_milestones", "passed": False,
                                  "detail": f"Failed gates: {', '.join(failed)} (score {m6_phase.score:.0f}/100)"})

        # --- Gate 3: No more than 2 consecutive losing months ---
        all_months = self._tracker.get_all_months_summary()
        consec_losing = self._max_consecutive_losing_months(all_months)
        total_points += 15
        if consec_losing <= _MIN_CONSECUTIVE_LOSING_MONTHS:
            points_earned += 15
            gate_details.append({"gate": "consecutive_losing_months", "passed": True,
                                  "detail": f"{consec_losing} consecutive losing months (≤{_MIN_CONSECUTIVE_LOSING_MONTHS} allowed)"})
        else:
            blocking.append(f"Too many consecutive losing months: {consec_losing} (max {_MIN_CONSECUTIVE_LOSING_MONTHS}).")
            gate_details.append({"gate": "consecutive_losing_months", "passed": False,
                                  "detail": f"{consec_losing} consecutive losing months"})

        # --- Gate 4: Regime coverage ≥ 2 distinct regimes ---
        regimes = self._count_distinct_regimes(all_months)
        total_points += 15
        if regimes >= _MIN_REGIMES:
            points_earned += 15
            gate_details.append({"gate": "regime_coverage", "passed": True,
                                  "detail": f"Traded in {regimes} distinct market regimes (≥{_MIN_REGIMES} required)"})
        else:
            blocking.append(f"Insufficient regime coverage: {regimes} regime(s) observed (need ≥{_MIN_REGIMES}).")
            gate_details.append({"gate": "regime_coverage", "passed": False,
                                  "detail": f"Only {regimes} distinct regime(s) observed"})

        # --- Gate 5: ≥ 3 months of positive net PnL ---
        positive_months = sum(1 for m in all_months if m.get("net_pnl", 0) > 0)
        total_points += 15
        if positive_months >= _MIN_POSITIVE_MONTHS:
            points_earned += 15
            gate_details.append({"gate": "positive_months", "passed": True,
                                  "detail": f"{positive_months} months with positive PnL (≥{_MIN_POSITIVE_MONTHS} required)"})
        else:
            blocking.append(f"Insufficient profitable months: {positive_months} (need ≥{_MIN_POSITIVE_MONTHS}).")
            gate_details.append({"gate": "positive_months", "passed": False,
                                  "detail": f"{positive_months} profitable months out of {len(all_months)}"})

        # --- Advisory: drawdown trend ---
        total_points += 10
        if all_months:
            recent_dd = all_months[-1].get("max_drawdown", 0)
            if recent_dd < 0.08:
                points_earned += 10
                gate_details.append({"gate": "drawdown_trend", "passed": True,
                                      "detail": f"Recent drawdown {recent_dd:.1%} is healthy (<8%)"})
            else:
                advisory.append(f"Recent drawdown {recent_dd:.1%} is elevated — monitor carefully after going live.")
                points_earned += 5
                gate_details.append({"gate": "drawdown_trend", "passed": False,
                                      "detail": f"Recent drawdown {recent_dd:.1%} above comfort zone"})

        score = int(points_earned / total_points * 100) if total_points else 0
        go_live = len(blocking) == 0

        # Estimate ready date
        est_date = self._estimate_ready_date(status, all_months)

        if not go_live and not advisory:
            advisory.append("Address all blocking reasons before switching PAPER_MODE=false.")
        if go_live:
            advisory.append("Congratulations! Start with 25% of planned capital and scale up over 4 weeks.")

        return LiveReadinessReport(
            go_live=go_live,
            score=score,
            blocking_reasons=blocking,
            advisory_notes=advisory,
            gate_details=gate_details,
            estimated_ready_date=est_date,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _max_consecutive_losing_months(self, months: list[dict]) -> int:
        max_streak = streak = 0
        for m in months:
            if m.get("net_pnl", 0) <= 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        return max_streak

    def _count_distinct_regimes(self, months: list[dict]) -> int:
        regimes: set[str] = set()
        for m in months:
            for r in m.get("regime_breakdown", {}).keys():
                if r and r != "UNKNOWN":
                    regimes.add(r)
        return len(regimes)

    def _estimate_ready_date(self, status: dict, all_months: list[dict]) -> str:
        trading_elapsed = status.get("trading_days_elapsed", 0)
        trading_remaining = max(0, _TOTAL_TRADING_DAYS - trading_elapsed)
        if trading_remaining == 0:
            return date.today().isoformat()
        days_elapsed = status.get("days_elapsed", 1)
        days_per_trading = days_elapsed / max(1, trading_elapsed)
        est = date.today() + timedelta(days=int(trading_remaining * days_per_trading))
        return est.isoformat()
