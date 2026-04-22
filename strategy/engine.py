# =============================================================================
# strategy/engine.py — M4: Strategy Engine + M5: Risk Manager
#
# M4: Combines TA score + sentiment + trend → BUY / SELL / HOLD
# M5: Calculates entry, stop-loss, take-profit, position size
# =============================================================================

from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    TA_WEIGHT, SENTIMENT_WEIGHT, TREND_WEIGHT,
    MIN_CONFIDENCE, TOP_N_SIGNALS,
    RISK_PER_TRADE_PCT, REWARD_RISK_RATIO, MAX_OPEN_POSITIONS,
    VIRTUAL_CAPITAL, ATR_SL_MULTIPLIER,
    SELL_CONFIDENCE, THESIS_DROP_SELL_PCT,
)
from analysis.technical_agent import TAResult
from analysis.sentiment_agent import SentimentResult
from utils import get_logger

logger = get_logger("StrategyEngine")


@dataclass
class TradeSignal:
    """A fully resolved trade signal — output of M4 + M5."""
    symbol:         str
    action:         str           # BUY | SELL | HOLD
    confidence:     float         # 0.0 – 1.0
    entry_price:    float
    stop_loss:      float
    take_profit:    float
    position_size:  int           # number of shares
    capital_at_risk: float        # ₹ amount risked
    ta_score:       float
    sentiment:      str
    sentiment_score: float
    reasoning:      str           # single human-readable string
    setup_type:     str = "technical_base"
    regime_tag:     str = ""
    quality_score:  float = 0.0
    expectancy_score: float = 0.0
    symbol_edge:    float = 0.0
    setup_edge:     float = 0.0
    quality_flags:  list[str] = field(default_factory=list)
    raw_ta:         dict = field(default_factory=dict)


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
    ) -> TradeSignal:
        """
        Generate a trade signal for one stock.

        Args:
            ta:               Output from TechnicalAgent
            sentiment:        Output from SentimentAgent
            df:               OHLCV DataFrame for this stock
            portfolio_value:  Current total portfolio value (₹)
            open_positions:   Number of currently open positions
            held_position:    True if this symbol is already in portfolio
            entry_confidence: Confidence at time of entry (for thesis check)
        """
        last_close = float(df["close"].iloc[-1])
        atr        = self._atr(df)

        # ------------------------------------------------------------------
        # M4: Compute composite confidence score
        # ------------------------------------------------------------------
        ta_norm   = ta.score / 10.0                    # 0 – 1
        sent_norm = (sentiment.score + 1) / 2          # -1..1 → 0..1
        trend_norm= self._trend_score(df)              # 0 – 1

        confidence = (
            ta_norm   * TA_WEIGHT +
            sent_norm * SENTIMENT_WEIGHT +
            trend_norm* TREND_WEIGHT
        )
        confidence = round(min(confidence, 1.0), 3)

        # ------------------------------------------------------------------
        # Determine action
        # ------------------------------------------------------------------
        if open_positions >= MAX_OPEN_POSITIONS and not held_position:
            action = "HOLD"
            reason = f"Max open positions ({MAX_OPEN_POSITIONS}) reached"
        elif confidence >= MIN_CONFIDENCE and ta.signal == "bullish" and not held_position:
            action = "BUY"
            reason = self._build_reason(ta, sentiment, confidence)
        elif confidence <= SELL_CONFIDENCE and ta.signal == "bearish":
            action = "SELL"
            reason = self._build_reason(ta, sentiment, confidence)
        elif held_position and entry_confidence > 0:
            # Thesis re-evaluation: if confidence dropped significantly, exit
            drop = (entry_confidence - confidence) / entry_confidence if entry_confidence else 0
            if drop >= THESIS_DROP_SELL_PCT and ta.signal in ("bearish", "neutral"):
                action = "SELL"
                reason = (self._build_reason(ta, sentiment, confidence) +
                          f" | thesis degraded {drop:.0%} from entry")
            else:
                action = "HOLD"
                reason = f"Held position, confidence {confidence:.0%} — holding"
        else:
            action = "HOLD"
            reason = f"Confidence {confidence:.0%} below threshold — no trade"

        # ------------------------------------------------------------------
        # M5: Risk management — entry / SL / TP / position size
        # ------------------------------------------------------------------
        entry     = last_close
        atr_sl    = round(entry - (ATR_SL_MULTIPLIER * atr), 2)

        # Pivot-based SL: anchor to nearest support level when available.
        # Use whichever is HIGHER (closer to entry = tighter risk control).
        try:
            from analysis.support_resistance import SupportResistanceAnalyser
            sr = SupportResistanceAnalyser().analyse(ta.symbol, df)
            if sr and sr.nearest_support and sr.nearest_support < entry:
                pivot_sl = round(sr.nearest_support * 0.995, 2)  # 0.5% buffer below support
                stop_loss = max(atr_sl, pivot_sl)
                if stop_loss != atr_sl:
                    logger.debug(
                        f"{ta.symbol}: pivot SL Rs.{pivot_sl:,.2f} "
                        f"used over ATR SL Rs.{atr_sl:,.2f}"
                    )
            else:
                stop_loss = atr_sl
        except Exception:
            stop_loss = atr_sl

        take_profit = round(entry + (REWARD_RISK_RATIO * (entry - stop_loss)), 2)

        # 2% portfolio risk rule
        risk_per_trade = portfolio_value * RISK_PER_TRADE_PCT
        sl_distance    = entry - stop_loss
        if sl_distance > 0:
            position_size = int(risk_per_trade / sl_distance)
        else:
            position_size = 0

        # Apply market regime multiplier (0.5 in sideways, 0.0 in bear)
        position_size   = int(position_size * position_size_multiplier)
        capital_at_risk = round(position_size * sl_distance, 2)

        return TradeSignal(
            symbol          = ta.symbol,
            action          = action,
            confidence      = confidence,
            entry_price     = round(entry, 2),
            stop_loss       = stop_loss,
            take_profit     = take_profit,
            position_size   = max(0, position_size),
            capital_at_risk = capital_at_risk,
            ta_score        = ta.score,
            sentiment       = sentiment.label,
            sentiment_score = sentiment.score,
            reasoning       = reason,
            raw_ta          = ta.indicators,
        )

    def generate_all(
        self,
        ta_results:   dict[str, TAResult],
        sent_results: dict[str, SentimentResult],
        market_data:  dict[str, pd.DataFrame],
        portfolio_value: float = VIRTUAL_CAPITAL,
        open_positions:  int   = 0,
        position_size_multiplier: float = 1.0,
    ) -> list[TradeSignal]:
        """
        Generate signals for all stocks. Returns BUY signals sorted by
        confidence, then all SELL signals, then HOLDs.
        """
        signals = []
        for sym, ta in ta_results.items():
            sent = sent_results.get(sym)
            df   = market_data.get(sym)
            if sent is None or df is None:
                continue
            sig = self.generate(ta, sent, df, portfolio_value, open_positions,
                                  position_size_multiplier)
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
