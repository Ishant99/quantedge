# =============================================================================
# analysis/technical_agent.py — M2: Technical Analysis Agent
#
# Responsibilities:
#   - Compute RSI, MACD, EMA20/50/200, Bollinger Bands, volume breakout
#   - Return a TA score (1–10) and signal: bullish / bearish / neutral
#   - Each indicator contributes to the score with defined weights
#
# Usage:
#   from analysis.technical_agent import TechnicalAgent
#   agent = TechnicalAgent()
#   result = agent.analyse(symbol, df)
# =============================================================================

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    RSI_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    BB_PERIOD, BB_STD, SMA_SHORT, SMA_MID, SMA_LONG,
    VOLUME_AVG_DAYS, MIN_TA_SCORE
)
from utils import get_logger

logger = get_logger("TechnicalAgent")


@dataclass
class TAResult:
    symbol:        str
    score:         float          # 1.0 – 10.0
    signal:        str            # bullish | bearish | neutral
    reasoning:     list[str]      # human-readable breakdown
    indicators:    dict           # raw indicator values for dashboard
    tradeable:     bool           # score >= MIN_TA_SCORE


class TechnicalAgent:
    """
    M2 — Computes technical indicators and scores each stock.

    Scoring breakdown (total = 10 pts):
      RSI                 → 2.0 pts
      MACD crossover      → 2.0 pts
      EMA trend alignment → 2.0 pts
      Bollinger Band pos  → 2.0 pts
      Volume breakout     → 2.0 pts
    """

    def analyse(self, symbol: str, df: pd.DataFrame) -> Optional[TAResult]:
        """
        Run full TA on a single stock's OHLCV DataFrame.
        Returns None if there's insufficient data.
        """
        if len(df) < SMA_LONG:
            logger.warning(f"{symbol}: need {SMA_LONG} rows, got {len(df)}")
            return None

        try:
            df = df.copy().sort_index()
            close  = df["close"]
            high   = df["high"]
            low    = df["low"]
            volume = df["volume"]

            score    = 0.0
            reasons  = []
            raw      = {}

            # ----------------------------------------------------------
            # 1. RSI  (max 2 pts)
            # ----------------------------------------------------------
            rsi = self._rsi(close, RSI_PERIOD)
            raw["rsi"] = round(rsi, 2)

            if rsi < 30:
                score += 2.0
                reasons.append(f"RSI oversold ({rsi:.1f}) — strong buy zone")
            elif rsi < 45:
                score += 1.5
                reasons.append(f"RSI low ({rsi:.1f}) — approaching buy zone")
            elif rsi < 55:
                score += 1.0
                reasons.append(f"RSI neutral ({rsi:.1f})")
            elif rsi < 70:
                score += 0.5
                reasons.append(f"RSI elevated ({rsi:.1f}) — caution")
            else:
                score += 0.0
                reasons.append(f"RSI overbought ({rsi:.1f}) — avoid")

            # ----------------------------------------------------------
            # 2. MACD crossover  (max 2 pts)
            # ----------------------------------------------------------
            macd_line, signal_line, histogram = self._macd(
                close, MACD_FAST, MACD_SLOW, MACD_SIGNAL
            )
            raw["macd"]        = round(macd_line, 4)
            raw["macd_signal"] = round(signal_line, 4)
            raw["macd_hist"]   = round(histogram, 4)

            prev_hist = self._macd(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL, lag=1)[2]

            if macd_line > signal_line and prev_hist <= 0 and histogram > 0:
                score += 2.0
                reasons.append("MACD fresh bullish crossover")
            elif macd_line > signal_line and histogram > 0:
                score += 1.5
                reasons.append("MACD above signal (bullish)")
            elif macd_line > signal_line:
                score += 1.0
                reasons.append("MACD above signal (mild bullish)")
            elif macd_line < signal_line and prev_hist >= 0 and histogram < 0:
                score += 0.0
                reasons.append("MACD fresh bearish crossover")
            else:
                score += 0.25
                reasons.append("MACD below signal (bearish)")

            # ----------------------------------------------------------
            # 3. EMA trend alignment  (max 2 pts)
            # ----------------------------------------------------------
            ema20  = close.ewm(span=SMA_SHORT, adjust=False).mean().iloc[-1]
            ema50  = close.ewm(span=SMA_MID,   adjust=False).mean().iloc[-1]
            ema200 = close.ewm(span=SMA_LONG,  adjust=False).mean().iloc[-1]
            last   = close.iloc[-1]

            raw["ema20"]  = round(ema20, 2)
            raw["ema50"]  = round(ema50, 2)
            raw["ema200"] = round(ema200, 2)

            aligned_bull = last > ema20 > ema50 > ema200
            aligned_bear = last < ema20 < ema50 < ema200

            if aligned_bull:
                score += 2.0
                reasons.append("Price above EMA20 > EMA50 > EMA200 (perfect uptrend)")
            elif last > ema20 and ema20 > ema50:
                score += 1.5
                reasons.append("Price above EMA20 & EMA50 (uptrend)")
            elif last > ema200:
                score += 1.0
                reasons.append("Price above EMA200 (long-term bull)")
            elif aligned_bear:
                score += 0.0
                reasons.append("Perfect downtrend — all EMAs bearish")
            else:
                score += 0.5
                reasons.append("Mixed EMA alignment")

            # ----------------------------------------------------------
            # 4. Bollinger Band position  (max 2 pts)
            # ----------------------------------------------------------
            bb_mid  = close.rolling(BB_PERIOD).mean()
            bb_std  = close.rolling(BB_PERIOD).std()
            bb_up   = (bb_mid + BB_STD * bb_std).iloc[-1]
            bb_lo   = (bb_mid - BB_STD * bb_std).iloc[-1]
            bb_mid_val = bb_mid.iloc[-1]
            bb_pct  = (last - bb_lo) / (bb_up - bb_lo) if (bb_up - bb_lo) > 0 else 0.5

            raw["bb_upper"] = round(bb_up, 2)
            raw["bb_lower"] = round(bb_lo, 2)
            raw["bb_pct"]   = round(bb_pct, 3)

            if bb_pct < 0.1:
                score += 2.0
                reasons.append(f"Near lower Bollinger Band ({bb_pct:.0%}) — oversold")
            elif bb_pct < 0.35:
                score += 1.5
                reasons.append(f"Lower half of Bollinger Band ({bb_pct:.0%})")
            elif bb_pct < 0.65:
                score += 1.0
                reasons.append(f"Mid Bollinger Band ({bb_pct:.0%})")
            elif bb_pct < 0.9:
                score += 0.5
                reasons.append(f"Upper half of Bollinger Band ({bb_pct:.0%})")
            else:
                score += 0.0
                reasons.append(f"Near upper Bollinger Band ({bb_pct:.0%}) — overbought")

            # ----------------------------------------------------------
            # 5. Volume breakout  (max 2 pts)
            # ----------------------------------------------------------
            vol_today = volume.iloc[-1]
            vol_avg   = volume.rolling(VOLUME_AVG_DAYS).mean().iloc[-1]
            vol_ratio = vol_today / vol_avg if vol_avg > 0 else 1.0

            raw["volume"]      = int(vol_today)
            raw["vol_avg_20"]  = int(vol_avg)
            raw["vol_ratio"]   = round(vol_ratio, 2)

            if vol_ratio >= 2.5:
                score += 2.0
                reasons.append(f"Massive volume spike ({vol_ratio:.1f}×  avg)")
            elif vol_ratio >= 1.5:
                score += 1.5
                reasons.append(f"High volume ({vol_ratio:.1f}× avg) — confirms move")
            elif vol_ratio >= 1.0:
                score += 1.0
                reasons.append(f"Average volume ({vol_ratio:.1f}× avg)")
            else:
                score += 0.5
                reasons.append(f"Below-average volume ({vol_ratio:.1f}× avg)")

            # ----------------------------------------------------------
            # Final score and signal
            # ----------------------------------------------------------
            score = round(min(score, 10.0), 2)

            if score >= 7.0:
                signal = "bullish"
            elif score <= 4.0:
                signal = "bearish"
            else:
                signal = "neutral"

            return TAResult(
                symbol    = symbol,
                score     = score,
                signal    = signal,
                reasoning = reasons,
                indicators= raw,
                tradeable = score >= MIN_TA_SCORE,
            )

        except Exception as e:
            logger.error(f"{symbol}: TA failed — {e}")
            return None

    def analyse_all(self, data: dict[str, pd.DataFrame]) -> dict[str, TAResult]:
        """Run analyse() on every stock. Returns only valid results."""
        results = {}
        for sym, df in data.items():
            result = self.analyse(sym, df)
            if result:
                results[sym] = result
        logger.info(
            f"TA complete: {len(results)} stocks scored. "
            f"Tradeable (score ≥ {MIN_TA_SCORE}): "
            f"{sum(1 for r in results.values() if r.tradeable)}"
        )
        return results

    # ------------------------------------------------------------------
    # Indicator calculations (pure pandas, no ta-lib dependency)
    # ------------------------------------------------------------------

    def _rsi(self, series: pd.Series, period: int) -> float:
        delta = series.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1])

    def _macd(
        self,
        series: pd.Series,
        fast: int,
        slow: int,
        signal: int,
        lag: int = 0
    ) -> tuple[float, float, float]:
        ema_fast   = series.ewm(span=fast,   adjust=False).mean()
        ema_slow   = series.ewm(span=slow,   adjust=False).mean()
        macd_line  = ema_fast - ema_slow
        signal_line= macd_line.ewm(span=signal, adjust=False).mean()
        histogram  = macd_line - signal_line
        idx = -(1 + lag)
        return float(macd_line.iloc[idx]), float(signal_line.iloc[idx]), float(histogram.iloc[idx])


# =============================================================================
# Standalone test — run: python -m analysis.technical_agent
# =============================================================================
if __name__ == "__main__":
    import yfinance as yf
    from datetime import datetime, timedelta

    print("\n" + "="*60)
    print("  M2 — Technical Analysis Agent Test")
    print("="*60 + "\n")

    sym = "RELIANCE"
    df  = yf.Ticker(f"{sym}.NS").history(
        start=(datetime.today() - timedelta(days=400)).strftime("%Y-%m-%d"),
        interval="1d", auto_adjust=True
    )
    df.columns = [c.lower() for c in df.columns]

    agent  = TechnicalAgent()
    result = agent.analyse(sym, df)

    if result:
        print(f"Symbol  : {result.symbol}")
        print(f"Score   : {result.score} / 10")
        print(f"Signal  : {result.signal.upper()}")
        print(f"Tradeable: {result.tradeable}")
        print("\nReasoning:")
        for r in result.reasoning:
            print(f"  • {r}")
        print("\nRaw indicators:")
        for k, v in result.indicators.items():
            print(f"  {k:15s} = {v}")
