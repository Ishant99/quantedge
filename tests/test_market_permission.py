import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from strategy.market_permission import MarketPermission, PermissionResult
from config import REGIME_STABILITY_GATE


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _mp():
    """Return a fresh MarketPermission instance."""
    return MarketPermission()


def _eval(
    symbol="TEST",
    action="BUY",
    regime="bull",
    regime_stability=REGIME_STABILITY_GATE,
    pcr_signal="neutral",
    fii_signal="neutral",
    sector_signal="neutral",
    breadth_signal="neutral",
    earnings_days=999,
    fno_banned=False,
    journal=None,
):
    """Thin wrapper so tests only need to specify the params they care about."""
    return _mp().evaluate(
        symbol=symbol,
        action=action,
        regime=regime,
        regime_stability=regime_stability,
        pcr_signal=pcr_signal,
        fii_signal=fii_signal,
        sector_signal=sector_signal,
        breadth_signal=breadth_signal,
        earnings_days=earnings_days,
        fno_banned=fno_banned,
        journal=journal,
    )


# ---------------------------------------------------------------------------
# TestBlockConditions
# ---------------------------------------------------------------------------

class TestBlockConditions:
    """All paths that should produce permission='BLOCK' with reduction_factor=0.0."""

    def test_bear_regime_blocks_buy(self):
        result = _eval(action="BUY", regime="bear")
        assert result.permission == "BLOCK"
        assert result.reduction_factor == 0.0
        assert "regime_bear_blocks_buy" in result.block_reasons

    def test_sideways_regime_blocks_buy(self):
        result = _eval(action="BUY", regime="sideways")
        assert result.permission == "BLOCK"
        assert result.reduction_factor == 0.0
        assert "regime_sideways_blocks_buy" in result.block_reasons

    def test_earnings_exactly_3_days_blocks(self):
        result = _eval(earnings_days=3)
        assert result.permission == "BLOCK"
        assert result.reduction_factor == 0.0
        assert "earnings_in_3d" in result.block_reasons

    def test_earnings_fewer_than_3_days_blocks(self):
        result = _eval(earnings_days=1)
        assert result.permission == "BLOCK"
        assert result.reduction_factor == 0.0
        assert "earnings_in_1d" in result.block_reasons

    def test_fno_banned_blocks(self):
        result = _eval(fno_banned=True)
        assert result.permission == "BLOCK"
        assert result.reduction_factor == 0.0
        assert "fno_ban_active" in result.block_reasons

    def test_multiple_block_reasons_all_listed(self):
        """bear + fno_banned must both appear in block_reasons."""
        result = _eval(action="BUY", regime="bear", fno_banned=True)
        assert result.permission == "BLOCK"
        assert result.reduction_factor == 0.0
        assert "regime_bear_blocks_buy" in result.block_reasons
        assert "fno_ban_active" in result.block_reasons
        assert len(result.block_reasons) == 2


# ---------------------------------------------------------------------------
# TestBoundaryConditions
# ---------------------------------------------------------------------------

class TestBoundaryConditions:
    """Edge cases around threshold values."""

    def test_earnings_4_days_not_blocked(self):
        """earnings_days=4 is one day past the threshold — must not block."""
        result = _eval(earnings_days=4)
        assert result.permission != "BLOCK"
        assert "earnings_in_4d" not in result.block_reasons

    def test_sideways_sell_not_blocked(self):
        """Sideways regime only blocks BUY; SELL should be allowed."""
        result = _eval(action="SELL", regime="sideways")
        assert result.permission != "BLOCK"
        assert "regime_sideways_blocks_buy" not in result.block_reasons

    def test_bear_sell_not_blocked(self):
        """Bear regime only blocks BUY; SELL should be allowed."""
        result = _eval(action="SELL", regime="bear")
        assert result.permission != "BLOCK"
        assert "regime_bear_blocks_buy" not in result.block_reasons

    def test_regime_stability_exactly_at_gate_no_reduction(self):
        """regime_stability == REGIME_STABILITY_GATE must NOT trigger the reduction."""
        result = _eval(regime_stability=REGIME_STABILITY_GATE)
        assert result.permission == "ALLOW"
        assert result.reduction_factor == 1.0

    def test_regime_stability_zero_no_reduction(self):
        """regime_stability=0 satisfies 0 < stability, so the condition is False — no reduction."""
        result = _eval(regime_stability=0)
        assert result.permission == "ALLOW"
        assert result.reduction_factor == 1.0


# ---------------------------------------------------------------------------
# TestReduceConditions
# ---------------------------------------------------------------------------

class TestReduceConditions:
    """Paths that produce permission='REDUCE' with a specific reduction_factor."""

    def test_recovery_regime_reduces_buy(self):
        result = _eval(regime="recovery")
        assert result.permission == "REDUCE"
        assert result.reduction_factor == pytest.approx(0.80)

    def test_fii_sell_reduces_buy(self):
        result = _eval(fii_signal="sell")
        assert result.permission == "REDUCE"
        assert result.reduction_factor == pytest.approx(0.85)

    def test_fii_strong_sell_and_pcr_strong_sell_double_bearish(self):
        """Double-bearish path takes the combined 0.70 factor, not the individual 0.85."""
        result = _eval(fii_signal="strong_sell", pcr_signal="strong_sell")
        assert result.permission == "REDUCE"
        assert result.reduction_factor == pytest.approx(0.70)

    def test_breadth_very_weak_reduces_buy(self):
        result = _eval(breadth_signal="very_weak")
        assert result.permission == "REDUCE"
        assert result.reduction_factor == pytest.approx(0.75)

    def test_breadth_weak_reduces_buy(self):
        result = _eval(breadth_signal="weak")
        assert result.permission == "REDUCE"
        assert result.reduction_factor == pytest.approx(0.85)

    def test_sector_bearish_reduces_buy(self):
        result = _eval(sector_signal="bearish")
        assert result.permission == "REDUCE"
        assert result.reduction_factor == pytest.approx(0.85)

    def test_regime_stability_below_gate_reduces_buy(self):
        stability = REGIME_STABILITY_GATE - 1  # one step below gate (e.g. 1)
        result = _eval(regime_stability=stability)
        assert result.permission == "REDUCE"
        assert result.reduction_factor == pytest.approx(0.80)

    def test_multiple_reductions_are_multiplicative(self):
        """recovery ×0.80 + fii_sell ×0.85 = 0.68."""
        result = _eval(regime="recovery", fii_signal="sell")
        assert result.permission == "REDUCE"
        assert result.reduction_factor == pytest.approx(0.80 * 0.85, rel=1e-3)

    def test_fii_bearish_does_not_trigger_for_sell_action(self):
        """REDUCE conditions on FII are BUY-only; SELL action must not be reduced."""
        result = _eval(action="SELL", fii_signal="strong_sell", pcr_signal="strong_sell")
        assert result.permission == "ALLOW"
        assert result.reduction_factor == 1.0


# ---------------------------------------------------------------------------
# TestAllowConditions
# ---------------------------------------------------------------------------

class TestAllowConditions:
    """Paths that produce permission='ALLOW' with reduction_factor=1.0."""

    def test_clean_bull_buy_allowed(self):
        result = _eval(
            action="BUY",
            regime="bull",
            regime_stability=REGIME_STABILITY_GATE,
        )
        assert result.permission == "ALLOW"
        assert result.reduction_factor == 1.0
        assert result.block_reasons == []

    def test_bear_sell_allowed(self):
        """SELL in a bear market should be fully allowed."""
        result = _eval(action="SELL", regime="bear")
        assert result.permission == "ALLOW"
        assert result.reduction_factor == 1.0

    def test_allow_reason_is_all_clear(self):
        result = _eval()
        assert result.reason == "all clear"


# ---------------------------------------------------------------------------
# TestPermissionResultInvariants
# ---------------------------------------------------------------------------

class TestPermissionResultInvariants:
    """Cross-cutting invariants that must hold for every permission value."""

    def test_block_always_zero_reduction(self):
        result = _eval(fno_banned=True)
        assert result.permission == "BLOCK"
        assert result.reduction_factor == 0.0

    def test_allow_always_full_reduction(self):
        result = _eval()
        assert result.permission == "ALLOW"
        assert result.reduction_factor == 1.0

    def test_reduce_factor_strictly_between_zero_and_one(self):
        result = _eval(regime="recovery")
        assert result.permission == "REDUCE"
        assert 0.0 < result.reduction_factor < 1.0

    def test_result_is_permission_result_instance(self):
        result = _eval()
        assert isinstance(result, PermissionResult)

    def test_block_reasons_empty_on_allow(self):
        result = _eval()
        assert result.block_reasons == []

    def test_block_reasons_empty_on_reduce(self):
        result = _eval(regime="recovery")
        assert result.permission == "REDUCE"
        assert result.block_reasons == []
