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
from config import VIRTUAL_CAPITAL, VIRTUAL_PORTFOLIO_FILE, MAX_DAILY_LOSS_PCT, MAX_WEEKLY_LOSS_PCT
from utils import get_logger
from utils.telegram import send

logger = get_logger("CircuitBreaker")

CIRCUIT_BREAKER_FILE = "logs/circuit_breaker.json"


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

        # Reset if new day
        if self.state.get("date") != today:
            self._reset(today, current_portfolio_value)

        # Get opening value for today
        opening_value   = self.state.get("opening_value", current_portfolio_value)
        daily_loss_pct  = (opening_value - current_portfolio_value) / opening_value * 100
        weekly_loss_pct = self._weekly_loss(current_portfolio_value)

        # Update state
        self.state["current_value"]    = current_portfolio_value
        self.state["daily_loss_pct"]   = round(daily_loss_pct, 2)
        self.state["weekly_loss_pct"]  = round(weekly_loss_pct, 2)
        self._save_state()

        # Check daily circuit breaker
        if daily_loss_pct >= MAX_DAILY_LOSS_PCT * 100:
            reason = (f"Daily circuit breaker triggered — "
                      f"portfolio down {daily_loss_pct:.1f}% today "
                      f"(limit: {MAX_DAILY_LOSS_PCT*100:.0f}%)")
            if not self.state.get("daily_alert_sent"):
                send(f"*Circuit Breaker Triggered*\n"
                     f"Portfolio down {daily_loss_pct:.1f}% today\n"
                     f"All new trades blocked until tomorrow.")
                self.state["daily_alert_sent"] = True
                self._save_state()
            logger.warning(reason)
            return False, reason

        # Check weekly circuit breaker
        if weekly_loss_pct >= MAX_WEEKLY_LOSS_PCT * 100:
            reason = (f"Weekly circuit breaker triggered — "
                      f"portfolio down {weekly_loss_pct:.1f}% this week "
                      f"(limit: {MAX_WEEKLY_LOSS_PCT*100:.0f}%)")
            logger.warning(reason)
            return False, reason

        # All clear
        logger.info(f"Circuit breaker OK — "
                    f"daily: {daily_loss_pct:+.1f}% | "
                    f"weekly: {weekly_loss_pct:+.1f}%")
        return True, "OK"

    def get_status(self) -> dict:
        return {
            "date":             self.state.get("date"),
            "opening_value":    self.state.get("opening_value", 0),
            "current_value":    self.state.get("current_value", 0),
            "daily_loss_pct":   self.state.get("daily_loss_pct", 0),
            "weekly_loss_pct":  self.state.get("weekly_loss_pct", 0),
            "max_daily_pct":    MAX_DAILY_LOSS_PCT * 100,
            "max_weekly_pct":   MAX_WEEKLY_LOSS_PCT * 100,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reset(self, today: str, current_value: float):
        logger.info(f"Circuit breaker reset for {today}")
        self.state = {
            "date":            today,
            "opening_value":   current_value,
            "current_value":   current_value,
            "daily_loss_pct":  0.0,
            "weekly_loss_pct": 0.0,
            "daily_alert_sent":False,
        }
        self._save_state()

    def _weekly_loss(self, current: float) -> float:
        """Compare to value 5 trading days ago from snapshots."""
        try:
            from memory.portfolio_memory import PortfolioMemory
            snaps = PortfolioMemory().get_snapshots()
            if len(snaps) >= 5:
                week_ago = snaps[-5]["portfolio_value"]
                return max(0, (week_ago - current) / week_ago * 100)
        except Exception:
            pass
        return 0.0

    def _load_state(self) -> dict:
        os.makedirs("logs", exist_ok=True)
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
