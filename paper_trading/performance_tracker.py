"""paper_trading/performance_tracker.py — Monthly performance analytics.

Reads from trades.db (signals + portfolio_snapshots tables) and computes
per-month metrics: PnL, win rate, drawdown, Sharpe, regime breakdown.
"""
from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from datetime import date
from typing import Any, Generator

from config import SQLITE_DB_FILE
from paper_trading.session import MILESTONE_GATES, SESSION_FILE, _TRADING_DAYS_PER_MONTH

import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGS_DIR = os.path.join(_ROOT, "logs")


@contextmanager
def _conn(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _load_session_start() -> date | None:
    try:
        with open(SESSION_FILE) as f:
            data = json.load(f)
        s = data.get("start_date")
        return date.fromisoformat(s) if s else None
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return None


class MonthlyPerformanceTracker:
    """Computes per-month trading performance from trades.db."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or SQLITE_DB_FILE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_monthly_summary(self, month_num: int) -> dict[str, Any]:
        """Return metrics for the given 1-based month number."""
        start, end = self._month_date_range(month_num)
        if start is None:
            return self._empty_summary(month_num)

        trades = self._fetch_closed_trades(start, end)
        if not trades:
            return self._empty_summary(month_num, period_start=start.isoformat(), period_end=end.isoformat())

        return self._compute_metrics(trades, month_num, start, end)

    def get_all_months_summary(self) -> list[dict[str, Any]]:
        """Return metrics for all elapsed months since session start."""
        session_start = _load_session_start()
        if session_start is None:
            return []

        today = date.today()
        from paper_trading.session import _estimate_trading_days_static
        elapsed = _estimate_trading_days_static(session_start, today)
        completed_months = min(6, elapsed // _TRADING_DAYS_PER_MONTH)
        current_partial = min(6, (elapsed // _TRADING_DAYS_PER_MONTH) + 1)

        summaries = []
        for m in range(1, current_partial + 1):
            summaries.append(self.get_monthly_summary(m))
        return summaries

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _month_date_range(self, month_num: int) -> tuple[date | None, date | None]:
        session_start = _load_session_start()
        if session_start is None:
            return None, None

        # Approximate: each month = 21 trading days ≈ 30 calendar days
        from datetime import timedelta
        cal_days_per_month = 30
        start = session_start + timedelta(days=(month_num - 1) * cal_days_per_month)
        end = session_start + timedelta(days=month_num * cal_days_per_month)
        return start, end

    def _fetch_closed_trades(self, start: date, end: date) -> list[dict]:
        """Fetch closed trades (outcome != NULL) within the date range."""
        if not os.path.exists(self.db_path):
            return []

        query = """
            SELECT symbol, action, confidence, entry_price, exit_price,
                   pnl, outcome, regime, signal_ts, exit_ts
            FROM signals
            WHERE outcome IS NOT NULL
              AND date(signal_ts) >= ?
              AND date(signal_ts) < ?
            ORDER BY signal_ts
        """
        try:
            with _conn(self.db_path) as c:
                rows = c.execute(query, (start.isoformat(), end.isoformat())).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def _compute_metrics(
        self, trades: list[dict], month_num: int, start: date, end: date
    ) -> dict[str, Any]:
        pnls = [t["pnl"] for t in trades if t.get("pnl") is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        trade_count = len(pnls)
        win_rate = len(wins) / trade_count if trade_count else 0.0
        net_pnl = sum(pnls)
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
        profit_factor = avg_win / avg_loss if avg_loss > 0 else (999.0 if avg_win > 0 else 0.0)

        max_drawdown = self._calc_drawdown(pnls)
        sharpe = self._calc_sharpe(pnls)
        max_consec_losses = self._max_consecutive_losses(trades)
        regime_breakdown = self._regime_breakdown(trades)

        sorted_by_pnl = sorted(trades, key=lambda t: t.get("pnl") or 0)
        worst = sorted_by_pnl[0] if sorted_by_pnl else None
        best = sorted_by_pnl[-1] if sorted_by_pnl else None

        return {
            "month": month_num,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "trade_count": trade_count,
            "win_rate": win_rate,
            "net_pnl": net_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "max_drawdown": max_drawdown,
            "sharpe": sharpe,
            "max_consec_losses": max_consec_losses,
            "regime_breakdown": regime_breakdown,
            "best_trade": {"symbol": best["symbol"], "pnl": best["pnl"]} if best else None,
            "worst_trade": {"symbol": worst["symbol"], "pnl": worst["pnl"]} if worst else None,
        }

    def _calc_drawdown(self, pnls: list[float]) -> float:
        if not pnls:
            return 0.0
        peak = 0.0
        cumulative = 0.0
        max_dd = 0.0
        for pnl in pnls:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / max(abs(peak), 1.0)
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def _calc_sharpe(self, pnls: list[float], risk_free: float = 0.0) -> float:
        if len(pnls) < 2:
            return 0.0
        mean = sum(pnls) / len(pnls)
        variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        std = math.sqrt(variance)
        if std == 0:
            return 0.0
        daily_rf = risk_free / 252
        return ((mean - daily_rf) / std) * math.sqrt(252)

    def _max_consecutive_losses(self, trades: list[dict]) -> int:
        max_streak = 0
        streak = 0
        for t in trades:
            if (t.get("pnl") or 0) <= 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        return max_streak

    def _regime_breakdown(self, trades: list[dict]) -> dict[str, dict]:
        breakdown: dict[str, dict] = {}
        for t in trades:
            regime = t.get("regime") or "UNKNOWN"
            if regime not in breakdown:
                breakdown[regime] = {"trades": 0, "pnl": 0.0, "wins": 0}
            breakdown[regime]["trades"] += 1
            pnl = t.get("pnl") or 0
            breakdown[regime]["pnl"] += pnl
            if pnl > 0:
                breakdown[regime]["wins"] += 1
        for r in breakdown.values():
            r["win_rate"] = r["wins"] / r["trades"] if r["trades"] else 0.0
        return breakdown

    @staticmethod
    def _empty_summary(month_num: int, period_start: str = "", period_end: str = "") -> dict[str, Any]:
        return {
            "month": month_num,
            "period_start": period_start,
            "period_end": period_end,
            "trade_count": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "max_consec_losses": 0,
            "regime_breakdown": {},
            "best_trade": None,
            "worst_trade": None,
        }
