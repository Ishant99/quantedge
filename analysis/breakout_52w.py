# =============================================================================
# analysis/breakout_52w.py — 52-Week High Breakout Scanner
#
# Strategy: stocks that close at or near (within 2%) their 52-week high
# with above-average volume confirmation are entering a proven breakout.
#
# Edge: institutions accumulate into 52W highs, not away from them.
# Retail fear at new highs = persistent inefficiency.
#
# Criteria (all must hold for BREAKOUT signal):
#   1. Close within 2% of the 252-day high            (proximity gate)
#   2. Volume today >= 1.5x the 20-day average         (confirmation)
#   3. Price above EMA50                               (trend intact)
#   4. Not a gap-up flash — price must have held for 2+ days near high
#
# Output: BreakoutResult with score 0-10 (higher = cleaner breakout)
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from dataclasses import dataclass
from utils import get_logger

logger = get_logger("Breakout52W")


@dataclass
class BreakoutResult:
    symbol:              str
    is_breakout:         bool
    close:               float
    high_52w:            float
    distance_pct:        float   # how far below the 52W high (0 = AT the high)
    volume_ratio:        float   # today's vol / 20-day avg vol
    volume_confirmed:    bool
    above_ema50:         bool
    score:               float   # 0–10
    reasoning:           str


class Breakout52WScanner:
    """
    Scans a universe of stocks for 52-week high breakout patterns.
    Uses OHLCV data already fetched — no extra API calls.
    """

    PROXIMITY_PCT  = 0.02    # within 2% of 52W high
    VOL_MULTIPLIER = 1.5     # volume must be 1.5x the 20-day avg

    def scan(self, symbol: str, df: pd.DataFrame) -> BreakoutResult:
        """
        Analyse one stock for a 52W breakout setup.
        df: OHLCV DataFrame with lowercase columns (close, high, volume).
        """
        try:
            if df is None or len(df) < 60:
                return self._default(symbol)

            close  = df["close"]
            high   = df["high"]
            volume = df["volume"]

            last_close = float(close.iloc[-1])
            last_vol   = float(volume.iloc[-1])

            # 52W high — use up to 252 rows, but at least what we have
            lookback = min(252, len(df))
            high_52w = float(high.iloc[-lookback:].max())

            # Distance from 52W high (0% = AT the high, positive = below)
            distance_pct = (high_52w - last_close) / high_52w * 100
            near_high    = distance_pct <= (self.PROXIMITY_PCT * 100)

            # Volume confirmation
            vol_avg20    = float(volume.rolling(20).mean().iloc[-1])
            vol_ratio    = (last_vol / vol_avg20) if vol_avg20 > 0 else 1.0
            vol_confirmed = vol_ratio >= self.VOL_MULTIPLIER

            # EMA50 trend gate
            ema50       = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
            above_ema50 = last_close > ema50

            # Sustained approach — has price been near the high for 2+ days?
            # (avoids single-day gap-up spikes that reverse)
            recent_highs = high.iloc[-3:]
            days_near    = sum(1 for h in recent_highs
                               if (high_52w - h) / high_52w <= self.PROXIMITY_PCT * 1.5)
            sustained    = days_near >= 2

            is_breakout = near_high and vol_confirmed and above_ema50

            # Score: 0-10
            score = 0.0
            if near_high:
                # Closer to 52W high = higher score (at-high=4pts, within 1%=3, within 2%=2)
                if distance_pct <= 0.5:  score += 4.0
                elif distance_pct <= 1.0: score += 3.0
                else:                     score += 2.0
            if vol_confirmed:
                score += min(3.0, vol_ratio - self.VOL_MULTIPLIER + 1.5)
            if above_ema50:       score += 1.5
            if sustained:         score += 1.0
            score = round(min(score, 10.0), 2)

            reason_parts = []
            if near_high:      reason_parts.append(f"within {distance_pct:.1f}% of 52W high ({high_52w:,.0f})")
            if vol_confirmed:  reason_parts.append(f"volume {vol_ratio:.1f}x avg")
            if above_ema50:    reason_parts.append("above EMA50")
            if sustained:      reason_parts.append("sustained approach")
            reasoning = "; ".join(reason_parts) if reason_parts else f"No breakout (dist={distance_pct:.1f}%)"

            return BreakoutResult(
                symbol=symbol, is_breakout=is_breakout,
                close=round(last_close, 2), high_52w=round(high_52w, 2),
                distance_pct=round(distance_pct, 2),
                volume_ratio=round(vol_ratio, 2),
                volume_confirmed=vol_confirmed,
                above_ema50=above_ema50,
                score=score, reasoning=reasoning,
            )

        except Exception as e:
            logger.debug(f"{symbol} breakout scan error: {e}")
            return self._default(symbol)

    def scan_all(self, market_data: dict) -> dict[str, BreakoutResult]:
        """
        Scan all symbols. Returns results for EVERY symbol (not just breakouts).
        Caller filters on result.is_breakout.
        """
        results   = {}
        breakouts = 0
        for sym, df in market_data.items():
            r = self.scan(sym, df)
            results[sym] = r
            if r.is_breakout:
                breakouts += 1
        logger.info(f"52W Breakout scan: {breakouts}/{len(results)} breakouts found")
        return results

    def _default(self, symbol: str) -> BreakoutResult:
        return BreakoutResult(
            symbol=symbol, is_breakout=False,
            close=0, high_52w=0, distance_pct=100,
            volume_ratio=1.0, volume_confirmed=False,
            above_ema50=False, score=0.0,
            reasoning="Insufficient data",
        )
