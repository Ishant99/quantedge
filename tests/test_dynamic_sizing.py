"""Tests for risk/dynamic_sizing.py — DynamicPositionSizer"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch
import pytest
from risk.dynamic_sizing import DynamicPositionSizer, SizingResult, _vix_multiplier

# base_risk_pct / adjusted_risk_pct are stored as PERCENTAGES (2.0 = 2%, not 0.02)
_PCT = 100  # conversion factor: 0.02 * _PCT = 2.0


def _calc(confidence=0.70, entry_price=100.0, atr=2.0, portfolio_value=1_000_000,
          **kwargs):
    sizer = DynamicPositionSizer()
    with patch("risk.dynamic_sizing._get_india_vix", return_value=14.0):
        return sizer.calculate(
            symbol="TEST",
            confidence=confidence,
            entry_price=entry_price,
            atr=atr,
            portfolio_value=portfolio_value,
            **kwargs,
        )


class TestConfidenceTiers:
    def test_high_confidence_full_risk(self):
        r = _calc(confidence=0.85)
        assert r.base_risk_pct == pytest.approx(0.02 * _PCT, rel=1e-3)
        assert r.multipliers["confidence"] == 1.0

    def test_medium_confidence_reduced_risk(self):
        r = _calc(confidence=0.70)
        assert r.base_risk_pct == pytest.approx(0.02 * 0.75 * _PCT, rel=1e-3)
        assert r.multipliers["confidence"] == 0.75

    def test_low_confidence_half_risk(self):
        r = _calc(confidence=0.55)
        assert r.base_risk_pct == pytest.approx(0.02 * 0.5 * _PCT, rel=1e-3)
        assert r.multipliers["confidence"] == 0.5


class TestMultipliers:
    def test_bullish_pattern_multiplier_recorded(self):
        r = _calc(pattern_bias="bullish")
        assert r.multipliers["pattern"] == 1.15

    def test_bearish_pattern_multiplier_recorded(self):
        r = _calc(pattern_bias="bearish")
        assert r.multipliers["pattern"] == 0.7

    def test_near_support_multiplier_recorded(self):
        r = _calc(sr_near_support=True)
        assert r.multipliers["near_support"] == 1.1

    def test_sector_multiplier_recorded(self):
        r = _calc(sector_multiplier=1.3)
        assert r.multipliers["sector"] == pytest.approx(1.3)

    def test_regime_multiplier_applies_to_adjusted_risk(self):
        # Regime 0.5 should cut adjusted_risk vs regime 1.0 (use tiny portfolio
        # so portfolio cap doesn't flatten the difference)
        r_bull = _calc(regime_multiplier=1.0, portfolio_value=10_000)
        r_side = _calc(regime_multiplier=0.5, portfolio_value=10_000)
        assert r_bull.adjusted_risk_pct > r_side.adjusted_risk_pct

    def test_fii_high_boosts_multiplier(self):
        r_low  = _calc(fii_score=0.0)
        r_high = _calc(fii_score=10.0)
        assert r_high.multipliers["fii"] > r_low.multipliers["fii"]

    def test_sentiment_modifier_clamped(self):
        # Values outside ±0.10 are clamped to exactly ±0.10
        r_over  = _calc(sentiment_modifier=0.5)   # clamped to 0.10
        r_limit = _calc(sentiment_modifier=0.10)
        assert r_over.position_size == r_limit.position_size

    def test_sentiment_modifier_negative_reduces_size(self):
        r_pos = _calc(sentiment_modifier=0.10, portfolio_value=10_000)
        r_neg = _calc(sentiment_modifier=-0.10, portfolio_value=10_000)
        assert r_pos.position_size >= r_neg.position_size


class TestPositionSizeLimits:
    def test_position_size_non_negative(self):
        r = _calc(confidence=0.51, entry_price=5000.0, atr=0.01)
        assert r.position_size >= 0

    def test_portfolio_cap_applied(self):
        r = _calc(confidence=0.90, entry_price=1.0, atr=0.01,
                  portfolio_value=10_000_000)
        from config import MAX_POSITION_VALUE_PCT
        max_shares = int((10_000_000 * MAX_POSITION_VALUE_PCT) / 1.0)
        assert r.position_size <= max_shares

    def test_zero_atr_uses_price_floor(self):
        # atr=0 → sl_distance falls back to entry_price * 0.02, so size > 0
        r = _calc(atr=0.0, entry_price=100.0)
        assert r.position_size >= 0
        # stop_loss should still be below entry
        assert r.stop_loss < 100.0


class TestVixMultiplier:
    def test_vix_below_threshold_full_size(self):
        with patch("risk.dynamic_sizing._get_india_vix", return_value=14.0):
            assert _vix_multiplier() == 1.0

    def test_vix_high_reduces_size(self):
        from config import VIX_HIGH_THRESHOLD
        with patch("risk.dynamic_sizing._get_india_vix", return_value=VIX_HIGH_THRESHOLD + 1):
            assert _vix_multiplier() == 0.75

    def test_vix_extreme_halves_size(self):
        from config import VIX_EXTREME_THRESHOLD
        with patch("risk.dynamic_sizing._get_india_vix", return_value=VIX_EXTREME_THRESHOLD + 1):
            assert _vix_multiplier() == 0.5


class TestSizingResult:
    def test_returns_sizing_result(self):
        r = _calc()
        assert isinstance(r, SizingResult)

    def test_stop_loss_below_entry(self):
        r = _calc(entry_price=200.0, atr=3.0)
        assert r.stop_loss < 200.0

    def test_take_profit_above_entry(self):
        r = _calc(entry_price=200.0, atr=3.0)
        assert r.take_profit > 200.0

    def test_combined_multiplier_cap(self):
        # adjusted_risk_pct ≤ base_risk_pct * 1.5  (combined multiplier capped at 1.5)
        r = _calc(confidence=0.90, pattern_bias="bullish", sr_near_support=True,
                  sector_multiplier=1.5, regime_multiplier=1.5, fii_score=10.0,
                  sentiment_modifier=0.10)
        assert r.adjusted_risk_pct <= r.base_risk_pct * 1.5 * 1.01  # 1% tolerance
