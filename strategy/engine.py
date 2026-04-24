# =============================================================================
# strategy/engine.py — M4: Strategy Engine + M5: Risk Manager
#
# Three-layer architecture:
#   Layer 1 — Setup Quality    (TA inputs only → p_direction, setup_quality)
#   Layer 2 — Market Permission (macro + events → permission, via market_permission.py)
#   Layer 3 — Execution Sizing  (risk + portfolio → position_size, execution_risk)
# =============================================================================

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    TA_WEIGHT, TREND_WEIGHT,
    MIN_CONFIDENCE, TOP_N_SIGNALS,
    RISK_PER_TRADE_PCT, REWARD_RISK_RATIO, MAX_OPEN_POSITIONS,
    VIRTUAL_CAPITAL, ATR_SL_MULTIPLIER,
    SELL_CONFIDENCE, THESIS_DROP_SELL_PCT,
)
# SENTIMENT_WEIGHT intentionally not imported — sentiment is a Layer 3 modifier only
from analysis.technical_agent import TAResult
from analysis.sentiment_agent import SentimentResult
from strategy.decision_journal import DecisionJournal, SizingRationale
from utils import get_logger
from datetime import datetime

logger = get_logger("StrategyEngine")


@dataclass
class TradeSignal:
    """
    A fully resolved trade signal carrying outputs from all three decision layers.

    Layer 1 outputs:  p_direction, setup_quality, risk_reward
    Layer 2 outputs:  permission, permission_reason
    Layer 3 outputs:  position_size, position_size_pct, execution_risk
    """
    symbol:          str
    action:          str            # BUY | SELL | HOLD | ABSTAIN | BLOCKED

    # Layer 1 — Setup Quality (TA only)
    p_direction:     float = 0.0   # directional probability 0.0–1.0
    setup_quality:   float = 0.0   # setup cleanliness 0.0–1.0
    risk_reward:     float = 0.0

    # Layer 2 — Market Permission
    permission:        str = "ALLOW"  # ALLOW | REDUCE | BLOCK
    permission_reason: str = ""

    # Layer 3 — Execution Sizing
    position_size:     int   = 0
    position_size_pct: float = 0.0
    execution_risk:    float = 0.0   # 0.0–1.0

    # Derived
    expected_value:  float = 0.0    # p_direction × avg_win − (1−p) × avg_loss

    # Prices
    entry_price:    float = 0.0
    stop_loss:      float = 0.0
    take_profit:    float = 0.0
    capital_at_risk: float = 0.0

    # Legacy / compatibility fields (kept so existing code doesn't break)
    ta_score:        float = 0.0
    sentiment:       str   = "neutral"
    sentiment_score: float = 0.0
    reasoning:       str   = ""
    setup_type:      str   = "technical_base"
    regime_tag:      str   = ""
    quality_score:   float = 0.0
    expectancy_score: float = 0.0
    symbol_edge:     float = 0.0
    setup_edge:      float = 0.0
    quality_flags:   list[str] = field(default_factory=list)
    raw_ta:          dict = field(default_factory=dict)

    # Decision journal — full audit trail
    journal: Optional[DecisionJournal] = field(default=None, repr=False)

    @property
    def confidence(self) -> float:
        """Legacy alias — callers that read signal.confidence still work."""
        return self.p_direction

    @confidence.setter
    def confidence(self, value: float):
        self.p_direction = value


class StrategyEngine:
    """
    M4 — Combines signals from TA, sentiment, and trend into a trade decision.
    M5 — Applies 2% risk rule to calculate position sizing.
    """

    def generate(
        self,
        ta:        TAResult,
        sentiment: SentimentResult,
        df:        pd.DataFrame,
        portfolio_value: float = VIRTUAL_CAPITAL,
        open_positions:  int   = 0,
        position_size_multiplier: float = 1.0,
        held_position:   bool  = False,
        entry_confidence: float = 0.0,
        regime:          str  = "bull",
        regime_stability: int = 0,
    ) -> TradeSignal:
        """
        Layer 1: Generate a trade signal from TA inputs only.
        Sentiment is NOT a directional input here — it is a Layer 3 sizing modifier.
        """
        last_close = float(df["close"].iloc[-1])
        atr        = self._atr(df)

        # Start the decision journal for this symbol
        journal = DecisionJournal(
            symbol=ta.symbol,
            timestamp=datetime.now(),
            regime=regime,
            regime_stability=regime_stability,
        )

        # ------------------------------------------------------------------
        # Layer 1: Compute p_direction from TA + trend only (no sentiment)
        # ------------------------------------------------------------------
        ta_norm    = ta.score / 10.0       # 0–1
        trend_norm = self._trend_score(df) # 0–1

        # Normalised weights: TA_WEIGHT + TREND_WEIGHT (sentiment removed from L1)
        # Use regime-conditional weights from Phase 6.1
        try:
            from strategy.regime_weights import get_weight
            w_ta    = get_weight(regime, "technical",      default=TA_WEIGHT    / (TA_WEIGHT + TREND_WEIGHT))
            w_trend = get_weight(regime, "trend_strength", default=TREND_WEIGHT / (TA_WEIGHT + TREND_WEIGHT))
            total_w = w_ta + w_trend
            p_direction = (ta_norm * w_ta + trend_norm * w_trend) / max(total_w, 0.01)
        except Exception:
            total_l1_weight = TA_WEIGHT + TREND_WEIGHT
            w_ta    = TA_WEIGHT    / total_l1_weight
            w_trend = TREND_WEIGHT / total_l1_weight
            total_w = 1.0
            p_direction = (ta_norm * (TA_WEIGHT / total_l1_weight) +
                           trend_norm * (TREND_WEIGHT / total_l1_weight))
        p_direction = round(min(p_direction, 1.0), 3)

        # Layer 1 journal votes
        journal.add_vote(1, "technical", "BUY" if ta.signal == "bullish" else
                         ("SELL" if ta.signal == "bearish" else "NEUTRAL"),
                         raw_score=ta_norm,
                         weight=w_ta / total_w,
                         note=f"TA score {ta.score:.1f}/10, signal={ta.signal}")
        journal.add_vote(1, "trend_strength", "BUY" if trend_norm > 0.6 else
                         ("SELL" if trend_norm < 0.3 else "NEUTRAL"),
                         raw_score=trend_norm,
                         weight=w_trend / total_w,
                         note=f"52w position {trend_norm:.2f}")

        # setup_quality: how clean is the setup (1.0 = perfect, 0 = noisy)
        setup_quality = round(
            (ta_norm * 0.6) + (trend_norm * 0.4), 3
        )

        # ------------------------------------------------------------------
        # Determine action (Layer 1 only — regime/permission gate is Layer 2)
        # ------------------------------------------------------------------
        if open_positions >= MAX_OPEN_POSITIONS and not held_position:
            action = "HOLD"
            reason = f"Max open positions ({MAX_OPEN_POSITIONS}) reached"
        elif p_direction >= MIN_CONFIDENCE and ta.signal == "bullish" and not held_position:
            action = "BUY"
            reason = self._build_reason(ta, sentiment, p_direction)
        elif p_direction <= SELL_CONFIDENCE and ta.signal == "bearish":
            action = "SELL"
            reason = self._build_reason(ta, sentiment, p_direction)
        elif held_position and entry_confidence > 0:
            drop = (entry_confidence - p_direction) / entry_confidence if entry_confidence else 0
            if drop >= THESIS_DROP_SELL_PCT and ta.signal in ("bearish", "neutral"):
                action = "SELL"
                reason = (self._build_reason(ta, sentiment, p_direction) +
                          f" | thesis degraded {drop:.0%} from entry")
            else:
                action = "HOLD"
                reason = f"Held position, confidence {p_direction:.0%} — holding"
        else:
            action = "HOLD"
            reason = f"p_direction {p_direction:.0%} below threshold — no trade"

        # ------------------------------------------------------------------
        # Entry / SL / TP
        # ------------------------------------------------------------------
        entry  = last_close
        atr_sl = round(entry - (ATR_SL_MULTIPLIER * atr), 2)

        # Pivot-based SL (pre-computed S/R passed in via signal enrichment in main.py;
        # fallback call here kept for cases where it wasn't pre-computed)
        try:
            from analysis.support_resistance import SupportResistanceAnalyser
            sr = SupportResistanceAnalyser().analyse(ta.symbol, df)
            if sr and sr.nearest_support and sr.nearest_support < entry:
                pivot_sl  = round(sr.nearest_support * 0.995, 2)
                stop_loss = max(atr_sl, pivot_sl)
            else:
                stop_loss = atr_sl
        except Exception:
            stop_loss = atr_sl

        take_profit = round(entry + (REWARD_RISK_RATIO * (entry - stop_loss)), 2)
        sl_distance = entry - stop_loss
        risk_reward = round(REWARD_RISK_RATIO, 2)

        # ------------------------------------------------------------------
        # Layer 3 (basic): position size from 2% risk rule + regime multiplier
        # (DynamicPositionSizer in main.py overwrites this with full sizing)
        # ------------------------------------------------------------------
        risk_per_trade = portfolio_value * RISK_PER_TRADE_PCT
        if sl_distance > 0:
            position_size = int(risk_per_trade / sl_distance)
        else:
            position_size = 0
        position_size   = int(position_size * position_size_multiplier)
        capital_at_risk = round(position_size * sl_distance, 2)
        position_size_pct = round(
            (capital_at_risk / portfolio_value * 100) if portfolio_value > 0 else 0, 2
        )

        # expected_value (simple: p × reward − (1−p) × risk)
        avg_win_pct  = REWARD_RISK_RATIO * 2.0   # approximate
        avg_loss_pct = 2.0
        expected_value = round(
            p_direction * avg_win_pct - (1 - p_direction) * avg_loss_pct, 2
        )

        journal.final_action = action

        return TradeSignal(
            symbol            = ta.symbol,
            action            = action,
            p_direction       = p_direction,
            setup_quality     = setup_quality,
            risk_reward       = risk_reward,
            permission        = "ALLOW",
            permission_reason = "",
            position_size     = max(0, position_size),
            position_size_pct = position_size_pct,
            execution_risk    = 0.0,
            expected_value    = expected_value,
            entry_price       = round(entry, 2),
            stop_loss         = stop_loss,
            take_profit       = take_profit,
            capital_at_risk   = capital_at_risk,
            ta_score          = ta.score,
            sentiment         = sentiment.label,
            sentiment_score   = sentiment.score,
            reasoning         = reason,
            raw_ta            = ta.indicators,
            journal           = journal,
        )

    def generate_all(
        self,
        ta_results:   dict[str, TAResult],
        sent_results: dict[str, SentimentResult],
        market_data:  dict[str, pd.DataFrame],
        portfolio_value: float = VIRTUAL_CAPITAL,
        open_positions:  int   = 0,
        position_size_multiplier: float = 1.0,
        regime:          str  = "bull",
        regime_stability: int = 0,
    ) -> list[TradeSignal]:
        """
        Generate signals for all stocks. Returns BUY signals sorted by
        p_direction, then all SELL signals, then HOLDs.
        """
        signals = []
        for sym, ta in ta_results.items():
            sent = sent_results.get(sym)
            df   = market_data.get(sym)
            if sent is None or df is None:
                continue
            sig = self.generate(ta, sent, df, portfolio_value, open_positions,
                                position_size_multiplier, regime=regime,
                                regime_stability=regime_stability)
            signals.append(sig)

        # Sort: BUY first by confidence desc, then SELL, then HOLD
        buy_sigs  = sorted(
            [s for s in signals if s.action == "BUY"],
            key=lambda x: (x.quality_score or 0.0, x.confidence, x.ta_score),
            reverse=True,
        )
        sell_sigs = sorted(
            [s for s in signals if s.action == "SELL"],
            key=lambda x: (x.quality_score or 0.0, x.confidence, x.ta_score),
            reverse=True,
        )
        hold_sigs = [s for s in signals if s.action == "HOLD"]

        top = buy_sigs + sell_sigs + hold_sigs
        logger.info(
            f"Strategy output: {len(buy_sigs)} BUY, {len(sell_sigs)} SELL, "
            f"{len(hold_sigs)} HOLD"
        )
        return top

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Average True Range — used for dynamic SL/TP calculation."""
        high  = df["high"]
        low   = df["low"]
        close = df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs(),
        ], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    def _trend_score(self, df: pd.DataFrame) -> float:
        """
        Simple trend strength 0–1 based on where price sits
        relative to its 52-week high/low range.
        """
        close   = df["close"]
        high_52 = close.tail(252).max()
        low_52  = close.tail(252).min()
        last    = close.iloc[-1]
        rng     = high_52 - low_52
        return float((last - low_52) / rng) if rng > 0 else 0.5

    def _build_reason(self, ta: TAResult, sent: SentimentResult, conf: float) -> str:
        parts = [f"Confidence {conf:.0%}"]
        parts += ta.reasoning[:2]           # top 2 TA reasons
        if sent.label != "neutral":
            parts.append(f"{sent.label.capitalize()} news sentiment")
        return ". ".join(parts)
