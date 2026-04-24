import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass, field
from config import _S, MAX_OPEN_POSITIONS
from utils import get_logger

logger = get_logger("RiskGate")

# RISK_GATE_MIN_CONFIDENCE can be added to config.py as:
#   RISK_GATE_MIN_CONFIDENCE = float(_S("RISK_GATE_MIN_CONFIDENCE", default=0.55))
MIN_SIGNAL_CONFIDENCE = float(_S("RISK_GATE_MIN_CONFIDENCE", default=0.55))


@dataclass
class RiskGateResult:
    passed: bool
    blocks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    reduction_factor: float = 1.0


class RiskGate:
    def check(self, signal, portfolio_state: dict, open_positions_count: int,
              circuit_breaker=None) -> RiskGateResult:
        blocks = []
        warnings = []
        journal = getattr(signal, "journal", None)

        if circuit_breaker is not None:
            cb_result = circuit_breaker.check(portfolio_state.get("portfolio_value", 0))
            # Support both tuple (bool, str) and dict {"triggered": bool, "reason": str}
            if isinstance(cb_result, dict):
                triggered = cb_result.get("triggered", False)
                cb_reason = cb_result.get("reason", "circuit breaker triggered")
            else:
                allow, cb_reason = cb_result
                triggered = not allow
            if triggered:
                block = {"check": "circuit_breaker", "reason": cb_reason}
                blocks.append(block)
                if journal is not None:
                    journal.add_block("circuit_breaker", cb_reason, None)
                logger.warning("RiskGate BLOCK circuit_breaker: %s", cb_reason)

        if signal.action == "BUY" and open_positions_count >= MAX_OPEN_POSITIONS:
            reason = f"open positions {open_positions_count} >= MAX_OPEN_POSITIONS {MAX_OPEN_POSITIONS}"
            block = {"check": "max_positions", "reason": reason}
            blocks.append(block)
            if journal is not None:
                journal.add_block("max_positions", reason, open_positions_count)
            logger.warning("RiskGate BLOCK max_positions: %s", reason)

        if signal.action == "BUY" and signal.position_size == 0:
            reason = "position_size=0 (insufficient capital or ATR too large)"
            block = {"check": "position_size_zero", "reason": reason}
            blocks.append(block)
            if journal is not None:
                journal.add_block("position_size_zero", reason, 0)
            logger.warning("RiskGate BLOCK position_size_zero: %s", reason)

        if signal.action == "BUY" and hasattr(signal, "expected_value") and signal.expected_value < 0:
            reason = f"negative EV ({signal.expected_value:.2f})"
            block = {"check": "expected_value", "reason": reason}
            blocks.append(block)
            if journal is not None:
                journal.add_block("expected_value", reason, signal.expected_value)
            logger.warning("RiskGate BLOCK expected_value: %s", reason)

        if signal.action == "BUY" and signal.p_direction < MIN_SIGNAL_CONFIDENCE:
            reason = (
                f"p_direction {signal.p_direction:.3f} < "
                f"MIN_SIGNAL_CONFIDENCE {MIN_SIGNAL_CONFIDENCE:.2f}"
            )
            block = {"check": "min_confidence", "reason": reason}
            blocks.append(block)
            if journal is not None:
                journal.add_block("min_confidence", reason, signal.p_direction)
            logger.warning("RiskGate BLOCK min_confidence: %s", reason)

        if getattr(signal, "execution_risk", 0) > 0.85:
            warn = f"execution_risk {signal.execution_risk:.3f} > 0.85 — high slippage/spread risk"
            warnings.append(warn)
            logger.warning("RiskGate WARN execution_risk_high: %s", warn)

        passed = len(blocks) == 0
        if passed:
            logger.info("RiskGate PASS symbol=%s action=%s", getattr(signal, "symbol", "?"), signal.action)

        return RiskGateResult(passed=passed, blocks=blocks, warnings=warnings)
