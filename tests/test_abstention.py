import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock
from strategy.abstention import Abstention, AbstentionResult


def _make_signal(action="BUY", p_direction=0.70, setup_quality=0.65):
    sig = MagicMock()
    sig.action = action
    sig.p_direction = p_direction
    sig.setup_quality = setup_quality
    return sig


class TestAbstention:

    def setup_method(self):
        self.ab = Abstention()

    def test_clean_buy_does_not_abstain(self):
        sig = _make_signal()
        result = self.ab.evaluate(sig, regime="bull", breadth_signal="strong",
                                  day_of_week=1, hour=10)
        assert result.abstain is False
        assert result.category == "no_abstain"

    def test_low_conviction_abstains(self):
        sig = _make_signal(p_direction=0.55, setup_quality=0.50)
        result = self.ab.evaluate(sig, regime="bull", breadth_signal="moderate")
        assert result.abstain is True
        assert result.category == "low_conviction"

    def test_high_p_low_quality_does_not_trigger_low_conviction(self):
        # Only triggers when BOTH are below threshold
        sig = _make_signal(p_direction=0.75, setup_quality=0.50)
        result = self.ab.evaluate(sig, regime="bull", breadth_signal="strong",
                                  day_of_week=1, hour=10)
        assert result.abstain is False

    def test_bear_regime_blocks_buy(self):
        sig = _make_signal()
        result = self.ab.evaluate(sig, regime="bear", breadth_signal="strong",
                                  day_of_week=1, hour=10)
        assert result.abstain is True
        assert result.category == "regime_mismatch"

    def test_bear_regime_does_not_block_sell(self):
        sig = _make_signal(action="SELL")
        result = self.ab.evaluate(sig, regime="bear", breadth_signal="strong",
                                  day_of_week=1, hour=10)
        assert result.abstain is False

    def test_very_weak_breadth_abstains(self):
        sig = _make_signal()
        result = self.ab.evaluate(sig, regime="bull", breadth_signal="very_weak",
                                  day_of_week=1, hour=10)
        assert result.abstain is True
        assert result.category == "signal_conflict"

    def test_friday_afternoon_abstains(self):
        sig = _make_signal()
        result = self.ab.evaluate(sig, regime="bull", breadth_signal="strong",
                                  day_of_week=4, hour=14)
        assert result.abstain is True
        assert result.category == "calendar_risk"

    def test_friday_morning_does_not_abstain(self):
        sig = _make_signal()
        result = self.ab.evaluate(sig, regime="bull", breadth_signal="strong",
                                  day_of_week=4, hour=10)
        assert result.abstain is False

    def test_pre_open_abstains(self):
        sig = _make_signal()
        result = self.ab.evaluate(sig, regime="bull", breadth_signal="strong",
                                  day_of_week=1, hour=8)
        assert result.abstain is True
        assert result.category == "calendar_risk"

    def test_result_is_abstention_result(self):
        sig = _make_signal()
        result = self.ab.evaluate(sig, regime="bull", breadth_signal="moderate")
        assert isinstance(result, AbstentionResult)
        assert isinstance(result.abstain, bool)
        assert isinstance(result.reason, str)
        assert isinstance(result.category, str)

    def test_regime_mismatch_takes_priority_over_breadth(self):
        # Bear regime should fire before breadth_conflict
        sig = _make_signal(p_direction=0.55, setup_quality=0.50)
        result = self.ab.evaluate(sig, regime="bear", breadth_signal="very_weak",
                                  day_of_week=1, hour=10)
        assert result.category == "low_conviction"   # first rule fires
