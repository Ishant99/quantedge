# =============================================================================
# risk/circuit_breaker.py — Max Daily Loss Circuit Breaker
#
# If portfolio drops X% in one day → block all new trades for that day.
# Prevents spiral losses on bad market days.
# Resets automatically at midnight.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime, date
from config import (VIRTUAL_CAPITAL, VIRTUAL_PORTFOLIO_FILE, MAX_DAILY_LOSS_PCT,
                    MAX_WEEKLY_LOSS_PCT, MAX_MONTHLY_LOSS_PCT, MAX_DRAWDOWN_PCT)
from utils import get_logger
from utils.telegram import send

logger = get_logger("CircuitBreaker")

_PROJECT_ROOT        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CIRCUIT_BREAKER_FILE = os.path.join(_PROJECT_ROOT, "logs", "circuit_breaker.json")


class CircuitBreaker:
    """
    Monitors intraday and weekly portfolio loss.
    Blocks all new BUY signals when limits are breached.
    Resets daily at midnight automatically.
    """

    def __init__(self):
        self.state = self._load_state()

    def check(self, current_portfolio_value: float) -> tuple[bool, str]:
        """
        Check if trading should be allowed.
        Returns (allow_trading: bool, reason: str)
        """
        today = str(date.today())

        if self.state.get("date") != today:
            self._reset(today, current_portfolio_value)

        opening_value   = self.state.get("opening_value", current_portfolio_value)
        if opening_value <= 0:
            opening_value = current_portfolio_value or 1.0
        daily_loss_pct  = (opening_value - current_portfolio_value) / opening_value * 100
        weekly_loss_pct = self._weekly_loss(current_portfolio_value)

        # High-water mark for max-drawdown check (persists across days)
        hwm = max(self.state.get("high_water_mark", 0) or 0, current_portfolio_value)
        self.state["high_water_mark"] = hwm
        drawdown_pct = (hwm - current_portfolio_value) / hwm * 100 if hwm > 0 else 0.0

        # Month opening value for monthly loss limit (resets on month change)
        this_month = today[:7]  # YYYY-MM
        if self.state.get("month") != this_month:
            self.state["month"] = this_month
            self.state["month_opening_value"] = current_portfolio_value
            self.state["monthly_alert_sent"] = False
        month_open = self.state.get("month_opening_value") or current_portfolio_value
        monthly_loss_pct = (month_open - current_portfolio_value) / month_open * 100 if month_open > 0 else 0.0

        self.state["current_value"]    = current_portfolio_value
        self.state["daily_loss_pct"]   = round(daily_loss_pct, 2)
        self.state["weekly_loss_pct"]  = round(weekly_loss_pct, 2)
        self.state["monthly_loss_pct"] = round(monthly_loss_pct, 2)
        self.state["drawdown_pct"]     = round(drawdown_pct, 2)
        self._save_state()

        # Max drawdown from high-water mark — hardest stop, checked first
        if drawdown_pct >= MAX_DRAWDOWN_PCT * 100:
            reason = (f"Max drawdown circuit breaker triggered — "
                      f"portfolio down {drawdown_pct:.1f}% from peak ₹{hwm:,.0f} "
                      f"(limit: {MAX_DRAWDOWN_PCT*100:.0f}%)")
            if not self.state.get("drawdown_alert_sent"):
                send(
                    f"🛑 *Trading HALTED — Max Drawdown Hit*\n"
                    f"Portfolio is down *{drawdown_pct:.1f}%* from its peak of "
                    f"`₹{hwm:,.0f}` (limit is {MAX_DRAWDOWN_PCT*100:.0f}%).\n"
                    f"No new trades until the drawdown is reviewed.\n"
                    f"_This gate does NOT auto-reset — review the strategy, then "
                    f"reset the high-water mark in logs/circuit\\_breaker.json._"
                )
                self.state["drawdown_alert_sent"] = True
                self._save_state()
            logger.warning(reason)
            return False, reason

        # Monthly loss limit
        if monthly_loss_pct >= MAX_MONTHLY_LOSS_PCT * 100:
            reason = (f"Monthly circuit breaker triggered — "
                      f"portfolio down {monthly_loss_pct:.1f}% this month "
                      f"(limit: {MAX_MONTHLY_LOSS_PCT*100:.0f}%)")
            if not self.state.get("monthly_alert_sent"):
                send(
                    f"🚨 *Trading Paused — Monthly Loss Limit Hit*\n"
                    f"Portfolio is down *{monthly_loss_pct:.1f}%* this month "
                    f"(limit is {MAX_MONTHLY_LOSS_PCT*100:.0f}%).\n"
                    f"No new trades until next month.\n"
                    f"_Existing positions are still being monitored._"
                )
                self.state["monthly_alert_sent"] = True
                self._save_state()
            logger.warning(reason)
            return False, reason

        if daily_loss_pct >= MAX_DAILY_LOSS_PCT * 100:
            reason = (f"Daily circuit breaker triggered — "
                      f"portfolio down {daily_loss_pct:.1f}% today "
                      f"(limit: {MAX_DAILY_LOSS_PCT*100:.0f}%)")
            if not self.state.get("daily_alert_sent"):
                loss_amt = round((current_portfolio_value * daily_loss_pct) / 100, 0)
                send(
                    f"🚨 *Trading Paused — Daily Loss Limit Hit*\n"
                    f"Portfolio is down *{daily_loss_pct:.1f}%* today "
                    f"(limit is {MAX_DAILY_LOSS_PCT*100:.0f}%).\n"
                    f"Today's loss: `₹{loss_amt:,.0f}`\n"
                    f"No new trades until tomorrow morning.\n"
                    f"_Existing positions are still being monitored._"
                )
                self.state["daily_alert_sent"] = True
                self._save_state()
            logger.warning(reason)
            return False, reason

        # Check weekly circuit breaker
        if weekly_loss_pct >= MAX_WEEKLY_LOSS_PCT * 100:
            reason = (f"Weekly circuit breaker triggered — "
                      f"portfolio down {weekly_loss_pct:.1f}% this week "
                      f"(limit: {MAX_WEEKLY_LOSS_PCT*100:.0f}%)")
            if not self.state.get("weekly_alert_sent"):
                send(
                    f"🚨 *Trading Paused — Weekly Loss Limit Hit*\n"
                    f"Portfolio is down *{weekly_loss_pct:.1f}%* this week "
                    f"(limit is {MAX_WEEKLY_LOSS_PCT*100:.0f}%).\n"
                    f"No new trades until next Monday.\n"
                    f"_Existing positions are still being monitored._"
                )
                self.state["weekly_alert_sent"] = True
                self._save_state()
            logger.warning(reason)
            return False, reason

        # All clear
        logger.info(f"Circuit breaker OK — "
                    f"daily: {daily_loss_pct:+.1f}% | "
                    f"weekly: {weekly_loss_pct:+.1f}%")
        return True, "OK"

    def get_status(self) -> dict:
        return {
            "date":              self.state.get("date"),
            "opening_value":     self.state.get("opening_value", 0),
            "current_value":     self.state.get("current_value", 0),
            "daily_loss_pct":    self.state.get("daily_loss_pct", 0),
            "weekly_loss_pct":   self.state.get("weekly_loss_pct", 0),
            "monthly_loss_pct":  self.state.get("monthly_loss_pct", 0),
            "drawdown_pct":      self.state.get("drawdown_pct", 0),
            "high_water_mark":   self.state.get("high_water_mark", 0),
            "max_daily_pct":     MAX_DAILY_LOSS_PCT * 100,
            "max_weekly_pct":    MAX_WEEKLY_LOSS_PCT * 100,
            "max_monthly_pct":   MAX_MONTHLY_LOSS_PCT * 100,
            "max_drawdown_pct":  MAX_DRAWDOWN_PCT * 100,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reset(self, today: str, current_value: float):
        logger.info(f"Circuit breaker reset for {today}")
        # Preserve weekly_alert_sent within the same ISO week so the alert
        # fires at most once per week, not once per day.
        prev_date = self.state.get("date", "")
        try:
            same_week = (
                date.fromisoformat(prev_date).isocalendar().week
                == date.fromisoformat(today).isocalendar().week
            )
        except Exception:
            same_week = False
        self.state = {
            "date":              today,
            "opening_value":     current_value,
            "current_value":     current_value,
            "daily_loss_pct":    0.0,
            "weekly_loss_pct":   0.0,
            "daily_alert_sent":  False,
            "weekly_alert_sent": self.state.get("weekly_alert_sent", False) if same_week else False,
            # Persist across daily resets — these track longer horizons
            "high_water_mark":     self.state.get("high_water_mark", 0),
            "drawdown_alert_sent": self.state.get("drawdown_alert_sent", False),
            "month":               self.state.get("month", ""),
            "month_opening_value": self.state.get("month_opening_value", 0),
            "monthly_alert_sent":  self.state.get("monthly_alert_sent", False),
        }
        self._save_state()

    def _weekly_loss(self, current: float) -> float:
        """Compare to value ~5 trading days ago using date arithmetic, not index offset."""
        try:
            from memory.portfolio_memory import PortfolioMemory
            snaps = PortfolioMemory().get_snapshots()
            if not snaps:
                return 0.0

            now = datetime.now()
            ref = None

            # Walk snapshots newest-first to find one that is 4-6 trading days old
            for snap in reversed(snaps):
                try:
                    snap_dt = datetime.fromisoformat(snap["timestamp"])
                    delta_days = (now - snap_dt).days
                    # 4-6 calendar days covers Mon-Wed entries for a full trading week
                    if 4 <= delta_days <= 8:
                        ref = snap
                        break
                except Exception:
                    continue

            # Fallback: oldest available snapshot if no window match
            if ref is None:
                ref = snaps[0]

            week_ago = ref.get("portfolio_value")
            if week_ago and week_ago > 0:
                return max(0, (week_ago - current) / week_ago * 100)
        except Exception:
            pass
        return 0.0

    def _load_state(self) -> dict:
        os.makedirs(os.path.join(_PROJECT_ROOT, "logs"), exist_ok=True)
        if os.path.exists(CIRCUIT_BREAKER_FILE):
            try:
                with open(CIRCUIT_BREAKER_FILE) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_state(self):
        with open(CIRCUIT_BREAKER_FILE, "w") as f:
            json.dump(self.state, f, indent=2)
