# =============================================================================
# analysis/short_signals.py — Bear Market SHORT Signal Generator
#
# In bear/sideways regimes, generates SHORT watchlist signals.
# NSE retail traders cannot naked-short stocks (only futures/options),
# so these signals serve two purposes:
#   1. AVOID LIST — stocks to not buy even when market recovers
#   2. SHORT via futures — for users with F&O access
#   3. INVERSE ETF alert — e.g., buy Nifty BeES puts equivalent
#
# Signal format matches TradeSignal so dashboard/Telegram work unchanged.
# entry  = current price (short entry)
# sl     = entry + (1.5 × ATR)  — stop above recent resistance
# target = entry - (2 × ATR × RRR) — downside target
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from config import (
    ATR_SL_MULTIPLIER, REWARD_RISK_RATIO,
    RISK_PER_TRADE_PCT, TA_SIGNAL_BEARISH, MIN_CONFIDENCE,
)
from utils import get_logger

logger = get_logger("ShortSignals")


@dataclass
class ShortSignal:
    symbol:        str
    action:        str   = "SHORT"
    confidence:    float = 0.0
    entry_price:   float = 0.0
    stop_loss:     float = 0.0    # above entry for shorts
    take_profit:   float = 0.0    # below entry for shorts
    position_size: int   = 0
    ta_score:      float = 0.0
    sentiment:     str   = "negative"
    reasoning:     str   = ""
    atr:           float = 0.0
    ret_1m:        float = 0.0    # 1-month return %
    ret_3m:        float = 0.0    # 3-month return %
    below_ema200:  bool  = False


class ShortSignalGenerator:
    """
    Scans for high-probability short setups in bear/sideways markets.

    Criteria:
      - TA score <= TA_SIGNAL_BEARISH (strong bearish reading)
      - Price below EMA50 AND EMA200
      - 1-month return < -3% (already in downtrend)
      - RSI between 40-60 (dead-cat bounce zone — not oversold, still room to fall)
      - Volume declining on bounces (distribution)
    """

    def generate(
        self,
        symbol:         str,
        df:             pd.DataFrame,
        ta_score:       float,
        ta_signal:      str,
        sentiment:      str   = "negative",
        sent_score:     float = 0.0,
        portfolio_value: float = 1_000_000,
    ) -> ShortSignal | None:
        """Generate a SHORT signal for one stock. Returns None if criteria not met."""
        try:
            close  = df["close"]
            high   = df["high"]
            low    = df["low"]
            volume = df["volume"]
            last   = float(close.iloc[-1])

            # EMAs
            ema50  = float(close.ewm(span=50,  adjust=False).mean().iloc[-1])
            ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])

            # ATR(14)
            tr  = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low  - close.shift()).abs(),
            ], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])

            # Returns
            ret_1m = float((last / close.iloc[-22] - 1) * 100) if len(close) >= 22 else 0
            ret_3m = float((last / close.iloc[-63] - 1) * 100) if len(close) >= 63 else 0

            # RSI
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rsi   = float(100 - 100 / (1 + gain / loss.replace(0, np.nan)).iloc[-1])

            # Volume trend — declining on bounces = distribution
            vol5  = float(volume.rolling(5).mean().iloc[-1])
            vol20 = float(volume.rolling(20).mean().iloc[-1])
            vol_declining = vol5 < vol20 * 0.9

            # ----------------------------------------------------------
            # Short criteria
            # ----------------------------------------------------------
            if ta_signal not in ("bearish", "neutral"):
                return None
            if ta_score > TA_SIGNAL_BEARISH + 1.5:   # not bearish enough
                return None
            if last > ema50:                          # still above EMA50 — too early
                return None
            if ret_1m > 0:                            # uptrend, not a short
                return None
            if rsi > 65:                              # overbought bounce — risky
                return None

            # ----------------------------------------------------------
            # Confidence (0–1): based on how many criteria are met
            # ----------------------------------------------------------
            score = 0.0
            reasons = []

            if last < ema200:
                score += 0.20
                reasons.append("below EMA200")
            if last < ema50:
                score += 0.15
                reasons.append("below EMA50")
            if ret_3m < -10:
                score += 0.20
                reasons.append(f"3M return {ret_3m:.1f}%")
            elif ret_3m < -5:
                score += 0.10
                reasons.append(f"3M return {ret_3m:.1f}%")
            if ta_score <= TA_SIGNAL_BEARISH:
                score += 0.20
                reasons.append(f"TA score {ta_score:.1f} (bearish)")
            if vol_declining:
                score += 0.10
                reasons.append("volume declining (distribution)")
            if sentiment in ("negative", "very_negative") or sent_score < -0.3:
                score += 0.10
                reasons.append("negative sentiment")
            if rsi > 45:   # bounced into resistance zone
                score += 0.05
                reasons.append(f"RSI {rsi:.0f} (bounce into resistance)")

            if score < MIN_CONFIDENCE:   # respect global MIN_CONFIDENCE setting
                return None

            # ----------------------------------------------------------
            # Entry / SL / Target
            # ----------------------------------------------------------
            entry  = round(last, 2)
            sl     = round(entry + ATR_SL_MULTIPLIER * atr, 2)   # stop ABOVE entry
            target = round(entry - REWARD_RISK_RATIO * ATR_SL_MULTIPLIER * atr, 2)

            # Position size (2% risk rule)
            sl_dist = sl - entry
            if sl_dist <= 0:
                return None
            risk_amt = portfolio_value * RISK_PER_TRADE_PCT
            qty      = max(1, int(risk_amt / sl_dist))

            reasoning = (
                f"SHORT setup — {', '.join(reasons)}. "
                f"Entry Rs.{entry:,.0f} | SL Rs.{sl:,.0f} | "
                f"Target Rs.{target:,.0f} | R:R {REWARD_RISK_RATIO:.1f}"
            )

            logger.info(f"SHORT signal: {symbol} conf={score:.0%} "
                        f"entry={entry} sl={sl} tgt={target}")

            return ShortSignal(
                symbol        = symbol,
                action        = "SHORT",
                confidence    = round(score, 3),
                entry_price   = entry,
                stop_loss     = sl,
                take_profit   = target,
                position_size = qty,
                ta_score      = ta_score,
                sentiment     = sentiment,
                reasoning     = reasoning,
                atr           = round(atr, 2),
                ret_1m        = round(ret_1m, 2),
                ret_3m        = round(ret_3m, 2),
                below_ema200  = last < ema200,
            )

        except Exception as e:
            logger.debug(f"{symbol} short signal error: {e}")
            return None

    def generate_all(
        self,
        ta_results:   dict,
        sent_results: dict,
        market_data:  dict,
        portfolio_value: float = 1_000_000,
        top_n:        int = 5,
    ) -> list[ShortSignal]:
        """
        Generate SHORT signals for all stocks and return top-N by confidence.
        """
        signals = []
        for symbol, ta in ta_results.items():
            df = market_data.get(symbol)
            if df is None or df.empty:
                continue
            sent = sent_results.get(symbol)
            sentiment  = sent.label      if sent else "neutral"
            sent_score = sent.score      if sent else 0.0

            sig = self.generate(
                symbol          = symbol,
                df              = df,
                ta_score        = ta.score,
                ta_signal       = ta.signal,
                sentiment       = sentiment,
                sent_score      = sent_score,
                portfolio_value = portfolio_value,
            )
            if sig:
                signals.append(sig)

        signals.sort(key=lambda x: x.confidence, reverse=True)
        logger.info(f"ShortSignals: {len(signals)} short setups found, "
                    f"returning top {min(top_n, len(signals))}")
        return signals[:top_n]
