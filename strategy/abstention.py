import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass
from typing import Optional
from utils import get_logger

logger = get_logger("Abstention")


@dataclass
class AbstentionResult:
    abstain: bool
    reason: str
    category: str


class Abstention:
    def evaluate(self, signal, regime: str, breadth_signal: str,
                 day_of_week: Optional[int] = None,
                 hour: Optional[int] = None) -> AbstentionResult:

        if (signal.action == "BUY"
                and signal.p_direction < 0.60
                and signal.setup_quality < 0.55):
            reason = "p_direction and setup_quality both below threshold"
            logger.info("Abstention low_conviction symbol=%s: %s",
                        getattr(signal, "symbol", "?"), reason)
            return AbstentionResult(abstain=True, reason=reason, category="low_conviction")

        if signal.action == "BUY" and regime == "bear":
            reason = "bear regime — no new longs"
            logger.info("Abstention regime_mismatch symbol=%s: %s",
                        getattr(signal, "symbol", "?"), reason)
            return AbstentionResult(abstain=True, reason=reason, category="regime_mismatch")

        if signal.action == "BUY" and breadth_signal == "very_weak":
            reason = "market breadth very weak"
            logger.info("Abstention signal_conflict symbol=%s: %s",
                        getattr(signal, "symbol", "?"), reason)
            return AbstentionResult(abstain=True, reason=reason, category="signal_conflict")

        if day_of_week == 4 and hour is not None and hour >= 14:
            reason = "Friday afternoon — avoid new positions"
            logger.info("Abstention calendar_risk symbol=%s: %s",
                        getattr(signal, "symbol", "?"), reason)
            return AbstentionResult(abstain=True, reason=reason, category="calendar_risk")

        if hour is not None and hour < 9:
            reason = "pre-market hours"
            logger.info("Abstention calendar_risk symbol=%s: %s",
                        getattr(signal, "symbol", "?"), reason)
            return AbstentionResult(abstain=True, reason=reason, category="calendar_risk")

        return AbstentionResult(abstain=False, reason="", category="no_abstain")
