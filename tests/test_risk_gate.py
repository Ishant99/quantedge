import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock
from risk.risk_gate import RiskGate, RiskGateResult


def _make_signal(action="BUY", position_size=10, p_direction=0.70,
                 expected_value=1.5, execution_risk=0.2):
    sig = MagicMock()
    sig.action = action
    sig.position_size = position_size
    sig.p_direction = p_direction
    sig.expected_value = expected_value
    sig.execution_risk = execution_risk
    sig.journal = None
    return sig


def _make_portfolio(value=1_000_000):
    return {"portfolio_value": value}


class TestRiskGate:

    def setup_method(self):
        self.gate = RiskGate()

    def test_clean_buy_passes(self):
        sig = _make_signal()
        result = self.gate.check(sig, _make_portfolio(), open_positions_count=2)
        assert result.passed is True
        assert result.blocks == []

    def test_max_positions_blocks_buy(self):
        sig = _make_signal(action="BUY")
        result = self.gate.check(sig, _make_portfolio(), open_positions_count=5)
        assert result.passed is False
        assert any("max_positions" in b["check"] for b in result.blocks)

    def test_max_positions_does_not_block_sell(self):
        sig = _make_signal(action="SELL")
        result = self.gate.check(sig, _make_portfolio(), open_positions_count=5)
        assert result.passed is True

    def test_zero_position_size_blocks_buy(self):
        sig = _make_signal(position_size=0)
        result = self.gate.check(sig, _make_portfolio(), open_positions_count=0)
        assert result.passed is False
        assert any("position_size_zero" in b["check"] for b in result.blocks)

    def test_negative_expected_value_blocks_buy(self):
        sig = _make_signal(expected_value=-0.5)
        result = self.gate.check(sig, _make_portfolio(), open_positions_count=0)
        assert result.passed is False
        assert any("expected_value" in b["check"] for b in result.blocks)

    def test_low_confidence_blocks_buy(self):
        sig = _make_signal(p_direction=0.40)
        result = self.gate.check(sig, _make_portfolio(), open_positions_count=0)
        assert result.passed is False
        assert any("min_confidence" in b["check"] for b in result.blocks)

    def test_high_execution_risk_warns_not_blocks(self):
        sig = _make_signal(execution_risk=0.90)
        result = self.gate.check(sig, _make_portfolio(), open_positions_count=0)
        assert result.passed is True
        assert len(result.warnings) > 0

    def test_circuit_breaker_triggered_blocks(self):
        sig = _make_signal()
        cb = MagicMock()
        cb.check.return_value = (False, "daily loss exceeded")
        result = self.gate.check(sig, _make_portfolio(), open_positions_count=0,
                                 circuit_breaker=cb)
        assert result.passed is False
        assert any("circuit_breaker" in b["check"] for b in result.blocks)

    def test_circuit_breaker_ok_does_not_block(self):
        sig = _make_signal()
        cb = MagicMock()
        cb.check.return_value = (True, "")
        result = self.gate.check(sig, _make_portfolio(), open_positions_count=0,
                                 circuit_breaker=cb)
        assert result.passed is True

    def test_journal_add_block_called_on_failure(self):
        journal = MagicMock()
        sig = _make_signal(position_size=0)
        sig.journal = journal
        self.gate.check(sig, _make_portfolio(), open_positions_count=0)
        journal.add_block.assert_called()

    def test_result_is_risk_gate_result_dataclass(self):
        sig = _make_signal()
        result = self.gate.check(sig, _make_portfolio(), open_positions_count=0)
        assert isinstance(result, RiskGateResult)
        assert isinstance(result.passed, bool)
        assert isinstance(result.blocks, list)
        assert isinstance(result.warnings, list)
        assert isinstance(result.reduction_factor, float)
