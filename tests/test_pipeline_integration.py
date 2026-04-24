"""
Integration smoke tests: verify pipeline/contracts.py and pipeline/runner.py
import cleanly and the contracts dataclasses behave correctly.
These do NOT make network calls or touch SQLite.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import datetime
from pipeline.contracts import MarketContext, FeatureSet, Allocation, PipelineResult


class TestPipelineContracts:

    def test_market_context_instantiates(self):
        ctx = MarketContext(
            regime="bull",
            regime_stability=2,
            pcr_signal="neutral",
            fii_signal="buy",
            breadth_signal="strong",
            nifty_trend="up",
            sector_scores={"IT": 7.5, "Banking": 6.0},
            timestamp=datetime.now(),
        )
        assert ctx.regime == "bull"
        assert ctx.sector_scores["IT"] == 7.5

    def test_feature_set_defaults(self):
        fs = FeatureSet(symbol="RELIANCE", ta_result=None,
                        sentiment_result=None, df=None)
        assert fs.sector == ""
        assert fs.earnings_days == 999
        assert fs.fno_banned is False

    def test_allocation_defaults(self):
        alloc = Allocation(signal=None, sizing=None, permission=None)
        assert alloc.risk_passed is True
        assert alloc.abstained is False
        assert alloc.abstention_reason == ""

    def test_pipeline_result_defaults(self):
        result = PipelineResult(
            timestamp=datetime.now(),
            regime="bull",
            total_symbols=100,
            signals_generated=20,
            buys=5, sells=2, holds=13,
            blocked=3, abstained=2,
            allocations=[],
            market_context=None,
        )
        assert result.duration_seconds == 0.0
        assert result.errors == []

    def test_pipeline_result_counts_consistent(self):
        result = PipelineResult(
            timestamp=datetime.now(),
            regime="sideways",
            total_symbols=50,
            signals_generated=10,
            buys=3, sells=1, holds=6,
            blocked=2, abstained=1,
            allocations=[],
            market_context=None,
        )
        # buys + sells + holds should equal signals_generated (minus blocked/abstained)
        assert result.buys + result.sells + result.holds == result.signals_generated


class TestRiskGateAndAbstentionIntegration:
    """Verify that risk gate and abstention can be composed correctly."""

    def test_both_pass_independently(self):
        from unittest.mock import MagicMock
        from risk.risk_gate import RiskGate
        from strategy.abstention import Abstention

        sig = MagicMock()
        sig.action = "BUY"
        sig.position_size = 10
        sig.p_direction = 0.72
        sig.expected_value = 1.8
        sig.execution_risk = 0.15
        sig.setup_quality = 0.68
        sig.journal = None

        gate_result = RiskGate().check(sig, {"portfolio_value": 1_000_000},
                                       open_positions_count=2)
        abstain_result = Abstention().evaluate(sig, regime="bull",
                                               breadth_signal="moderate",
                                               day_of_week=2, hour=11)

        assert gate_result.passed is True
        assert abstain_result.abstain is False

    def test_blocked_by_gate_but_not_abstained(self):
        from unittest.mock import MagicMock
        from risk.risk_gate import RiskGate
        from strategy.abstention import Abstention

        sig = MagicMock()
        sig.action = "BUY"
        sig.position_size = 0        # will block at gate
        sig.p_direction = 0.72
        sig.expected_value = 1.8
        sig.execution_risk = 0.15
        sig.setup_quality = 0.68
        sig.journal = None

        gate_result = RiskGate().check(sig, {"portfolio_value": 1_000_000},
                                       open_positions_count=2)
        abstain_result = Abstention().evaluate(sig, regime="bull",
                                               breadth_signal="strong",
                                               day_of_week=2, hour=11)

        assert gate_result.passed is False
        assert abstain_result.abstain is False
