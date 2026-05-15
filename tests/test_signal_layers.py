"""
Layer isolation tests — Phase 3 Task 3.5

Verify that:
  - Layer 1 votes (setup quality) come from TA-only inputs: "technical", "trend_strength"
  - Layer 1 votes do NOT contain macro inputs: fii_dii, pcr, sector_rotation, breadth
  - Layer 2 votes (market permission) contain only macro/permission checks
  - Layer 2 votes do NOT contain TA inputs: rsi, macd, ema, bollinger
  - Sentiment does not appear in Layer 1 votes
"""
import sys, os

# Mock heavy optional dependencies that may not be installed in the test environment
from unittest.mock import MagicMock
for _mod in ("feedparser", "yfinance", "requests", "chromadb"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import datetime

from strategy.decision_journal import DecisionJournal, ModuleVote


# ─── helpers ──────────────────────────────────────────────────────────────────

MACRO_INPUTS = {"fii_dii", "fii", "pcr", "pcr_signal", "sector_rotation",
                "breadth", "market_breadth", "regime", "macro"}

TA_INPUTS = {"rsi", "macd", "ema", "bollinger", "obv", "adx",
             "stochastic", "volume", "atr"}

LAYER1_ALLOWED = {"technical", "trend_strength"}

LAYER2_ALLOWED = {"regime", "pcr_signal", "fii_signal", "sector_signal",
                  "breadth_signal", "earnings_days", "fno_banned"}


def _make_ta_result(symbol="TEST", score=7.0, signal="bullish"):
    ta = MagicMock()
    ta.symbol    = symbol
    ta.score     = score
    ta.signal    = signal
    ta.tradeable = True
    ta.indicators = {"rsi": 55.0, "macd_hist": 0.5, "adx": 28.0}
    ta.reasoning  = ["RSI bullish", "MACD crossover"]
    return ta


def _make_sentiment_result(symbol="TEST"):
    sent = MagicMock()
    sent.symbol     = symbol
    sent.score      = 0.4
    sent.label      = "positive"
    sent.confidence = 0.7
    sent.headlines  = ["Company beats earnings"]
    return sent


def _make_df():
    import pandas as pd
    import numpy as np
    n = 300
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    base = 1000.0
    closes = base + np.cumsum(np.random.randn(n) * 10)
    df = pd.DataFrame({
        "open":   closes * 0.999,
        "high":   closes * 1.005,
        "low":    closes * 0.995,
        "close":  closes,
        "volume": np.random.randint(100_000, 1_000_000, n),
    }, index=idx)
    return df


# ─── tests ────────────────────────────────────────────────────────────────────

class TestLayer1VoteIsolation:
    """Layer 1 votes must come from TA inputs only."""

    def test_layer1_votes_populated(self):
        from strategy.engine import StrategyEngine
        engine = StrategyEngine()
        ta   = _make_ta_result()
        sent = _make_sentiment_result()
        df   = _make_df()
        sig  = engine.generate(ta, sent, df, portfolio_value=1_000_000)

        journal = sig.journal
        assert journal is not None, "Signal must carry a DecisionJournal"
        assert len(journal.layer1_votes) > 0, "Layer 1 votes must not be empty"

    def test_layer1_vote_modules_are_ta_only(self):
        from strategy.engine import StrategyEngine
        engine = StrategyEngine()
        sig    = engine.generate(_make_ta_result(), _make_sentiment_result(),
                                 _make_df(), portfolio_value=1_000_000)
        l1_modules = {v.module for v in sig.journal.layer1_votes}

        # All Layer 1 modules must be TA-related
        for mod in l1_modules:
            assert mod not in MACRO_INPUTS, (
                f"Macro input '{mod}' must NOT appear in Layer 1 votes"
            )

    def test_layer1_contains_no_sentiment_vote(self):
        from strategy.engine import StrategyEngine
        engine = StrategyEngine()
        sig    = engine.generate(_make_ta_result(), _make_sentiment_result(),
                                 _make_df(), portfolio_value=1_000_000)
        l1_modules = {v.module for v in sig.journal.layer1_votes}
        assert "sentiment" not in l1_modules, (
            "Sentiment must NOT appear in Layer 1 votes — it is a Layer 3 sizing modifier only"
        )

    def test_layer1_expected_modules_present(self):
        from strategy.engine import StrategyEngine
        engine = StrategyEngine()
        sig    = engine.generate(_make_ta_result(), _make_sentiment_result(),
                                 _make_df(), portfolio_value=1_000_000)
        l1_modules = {v.module for v in sig.journal.layer1_votes}
        assert "technical" in l1_modules, "Layer 1 must include 'technical' vote"
        assert "trend_strength" in l1_modules, "Layer 1 must include 'trend_strength' vote"

    def test_layer1_vote_layers_are_all_1(self):
        from strategy.engine import StrategyEngine
        engine = StrategyEngine()
        sig    = engine.generate(_make_ta_result(), _make_sentiment_result(),
                                 _make_df(), portfolio_value=1_000_000)
        for vote in sig.journal.layer1_votes:
            assert vote.layer == 1, (
                f"Vote '{vote.module}' in layer1_votes has layer={vote.layer}, expected 1"
            )

    def test_p_direction_is_float_between_0_and_1(self):
        from strategy.engine import StrategyEngine
        engine = StrategyEngine()
        sig    = engine.generate(_make_ta_result(), _make_sentiment_result(),
                                 _make_df(), portfolio_value=1_000_000)
        assert 0.0 <= sig.p_direction <= 1.0

    def test_setup_quality_is_float_between_0_and_1(self):
        from strategy.engine import StrategyEngine
        engine = StrategyEngine()
        sig    = engine.generate(_make_ta_result(), _make_sentiment_result(),
                                 _make_df(), portfolio_value=1_000_000)
        assert 0.0 <= sig.setup_quality <= 1.0


class TestLayer2VoteIsolation:
    """Layer 2 votes (MarketPermission) must contain only macro/event checks."""

    def test_layer2_votes_populated_after_permission(self):
        from strategy.market_permission import MarketPermission
        perm = MarketPermission()
        journal = DecisionJournal(
            symbol="TEST", timestamp=datetime.now(),
            regime="bull", regime_stability=3,
        )
        result = perm.evaluate(
            symbol="TEST", action="BUY",
            regime="bull", regime_stability=3,
            pcr_signal="neutral", fii_signal="buy",
            sector_signal="neutral", breadth_signal="moderate",
            earnings_days=30, fno_banned=False,
            journal=journal,
        )
        assert len(journal.layer2_votes) > 0, "Layer 2 votes must not be empty"

    def test_layer2_vote_modules_are_macro_only(self):
        from strategy.market_permission import MarketPermission
        perm    = MarketPermission()
        journal = DecisionJournal(
            symbol="TEST", timestamp=datetime.now(),
            regime="bull", regime_stability=3,
        )
        perm.evaluate(
            symbol="TEST", action="BUY",
            regime="bull", regime_stability=3,
            pcr_signal="neutral", fii_signal="neutral",
            sector_signal="neutral", breadth_signal="moderate",
            earnings_days=30, fno_banned=False,
            journal=journal,
        )
        l2_modules = {v.module for v in journal.layer2_votes}
        for mod in l2_modules:
            assert mod not in TA_INPUTS, (
                f"TA input '{mod}' must NOT appear in Layer 2 votes"
            )

    def test_layer2_vote_layers_are_all_2(self):
        from strategy.market_permission import MarketPermission
        perm    = MarketPermission()
        journal = DecisionJournal(
            symbol="TEST", timestamp=datetime.now(),
            regime="bull", regime_stability=3,
        )
        perm.evaluate(
            symbol="TEST", action="BUY",
            regime="bull", regime_stability=3,
            pcr_signal="neutral", fii_signal="neutral",
            sector_signal="neutral", breadth_signal="moderate",
            earnings_days=30, fno_banned=False,
            journal=journal,
        )
        for vote in journal.layer2_votes:
            assert vote.layer == 2, (
                f"Vote '{vote.module}' in layer2_votes has layer={vote.layer}, expected 2"
            )

    def test_layer2_blocks_bear_buy(self):
        from strategy.market_permission import MarketPermission
        perm = MarketPermission()
        result = perm.evaluate(
            symbol="TEST", action="BUY",
            regime="bear", regime_stability=3,
            pcr_signal="neutral", fii_signal="neutral",
            sector_signal="neutral", breadth_signal="moderate",
            earnings_days=30, fno_banned=False,
        )
        assert result.permission == "BLOCK", "Bear regime must block BUY"

    def test_layer2_blocks_earnings_risk(self):
        from strategy.market_permission import MarketPermission
        perm = MarketPermission()
        result = perm.evaluate(
            symbol="TEST", action="BUY",
            regime="bull", regime_stability=3,
            pcr_signal="neutral", fii_signal="neutral",
            sector_signal="neutral", breadth_signal="moderate",
            earnings_days=2, fno_banned=False,
        )
        assert result.permission == "BLOCK", "Earnings within 3 days must block"

    def test_layer2_allows_clean_bull(self):
        from strategy.market_permission import MarketPermission
        perm = MarketPermission()
        result = perm.evaluate(
            symbol="TEST", action="BUY",
            regime="bull", regime_stability=3,
            pcr_signal="neutral", fii_signal="buy",
            sector_signal="neutral", breadth_signal="moderate",
            earnings_days=30, fno_banned=False,
        )
        assert result.permission in ("ALLOW", "REDUCE"), (
            "Clean bull signal must be ALLOW or REDUCE, not BLOCK"
        )


class TestLayerSeparation:
    """Cross-layer: verify the layers don't bleed into each other."""

    def test_sentiment_not_in_layer1_or_layer2(self):
        from strategy.engine import StrategyEngine
        from strategy.market_permission import MarketPermission

        engine = StrategyEngine()
        sig    = engine.generate(_make_ta_result(), _make_sentiment_result(),
                                 _make_df(), portfolio_value=1_000_000)
        journal = sig.journal

        all_non_l3 = journal.layer1_votes + journal.layer2_votes
        for vote in all_non_l3:
            assert "sentiment" not in vote.module.lower(), (
                f"Sentiment module '{vote.module}' must not appear in Layer 1 or 2 — "
                "it is a Layer 3 sizing modifier only"
            )

    def test_journal_attached_to_signal(self):
        from strategy.engine import StrategyEngine
        engine = StrategyEngine()
        sig    = engine.generate(_make_ta_result(), _make_sentiment_result(),
                                 _make_df(), portfolio_value=1_000_000)
        assert sig.journal is not None
        assert sig.journal.symbol == "TEST"

    def test_confidence_is_alias_for_p_direction(self):
        from strategy.engine import StrategyEngine
        engine = StrategyEngine()
        sig    = engine.generate(_make_ta_result(), _make_sentiment_result(),
                                 _make_df(), portfolio_value=1_000_000)
        assert sig.confidence == sig.p_direction
