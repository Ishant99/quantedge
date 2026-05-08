# =============================================================================
# analysis/rsi2_strategy.py — RSI(2) Short-Term Mean Reversion Scanner
#
# Strategy (Larry Connors / Cesar Alvarez):
#   - RSI(2) < 10: stock is deeply oversold on a short-term basis
#   - BUT price must be above EMA200 (only buy dips in long-term uptrends)
#   - Entry: next day open after RSI(2) < 10 trigger
#   - Exit: price closes above EMA5, OR RSI(2) crosses above 50, OR 5-day timeout
#
# Edge: systematic short-term mean reversion in trending stocks.
# 60-70% historical win-rate on 2-5 day holds in trending markets.
#
# RSI(2) thresholds:
#   < 5  → STRONG BUY  (score 9+)
#   < 10 → BUY         (score 7+)
#   > 90 → STRONG SELL (short only, only in bear regime)
#   > 85 → SELL SHORT  (bear regime only)
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from dataclasses import dataclass
from utils import get_logger

logger = get_logger("RSI2Strategy")


@dataclass
class RSI2Result:
    symbol:         str
    rsi2:           float       # RSI(2) value
    rsi14:          float       # RSI(14) for context
    action:         str         # BUY | SELL | HOLD
    signal_strength: str        # STRONG | MODERATE | WEAK
    above_ema200:   bool        # long-term trend gate
    above_ema50:    bool        # intermediate trend gate
    ema5:           float       # exit target for longs
    score:          float       # 0–10
    reasoning:      str


class RSI2Scanner:
    """
    Scans for RSI(2) oversold/overbought extremes in trending stocks.
    Only generates BUY signals in long-term uptrends (price > EMA200).
    SELL/SHORT signals require explicit bear regime flag.
    """

    BUY_STRONG  = 5.0    # RSI(2) below this → STRONG BUY
    BUY_THRESH  = 10.0   # RSI(2) below this → BUY
    SELL_STRONG = 95.0   # RSI(2) above this → STRONG SELL (bear regime)
    SELL_THRESH = 90.0   # RSI(2) above this → SELL (bear regime only)

    def scan(self, symbol: str, df: pd.DataFrame,
             allow_shorts: bool = False) -> RSI2Result:
        """
        Analyse one stock for RSI(2) mean reversion signal.
        allow_shorts=True only in bear regime.
        """
        try:
            if df is None or len(df) < 30:
                return self._default(symbol)

            close = df["close"]
            last  = float(close.iloc[-1])

            # RSI(2)
            rsi2 = self._rsi(close, 2)

            # RSI(14) — for context / confirmation
            rsi14 = self._rsi(close, 14)

            # EMAs
            ema5   = float(close.ewm(span=5,   adjust=False).mean().iloc[-1])
            ema50  = float(close.ewm(span=50,  adjust=False).mean().iloc[-1])
            ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])

            above_ema200 = last > ema200
            above_ema50  = last > ema50

            # Pullback depth for scoring
            ema50_gap = (ema50 - last) / ema50 * 100   # positive = below EMA50

            action  = "HOLD"
            strength = "WEAK"
            score    = 5.0
            parts    = []

            # ── BUY signals (only in uptrend) ────────────────────────────────
            if rsi2 <= self.BUY_THRESH and above_ema200:
                action = "BUY"
                if rsi2 <= self.BUY_STRONG:
                    strength = "STRONG"
                    score    = 9.0 - rsi2 * 0.4      # e.g. RSI2=2 → score 8.2
                else:
                    strength = "MODERATE"
                    score    = 7.0 - (rsi2 - self.BUY_STRONG) * 0.15

                # Bonus: more oversold = higher score
                if above_ema50: score += 0.5           # still above EMA50 = recovering

                parts.append(f"RSI(2)={rsi2:.1f} — deeply oversold")
                if above_ema200: parts.append("above EMA200 (uptrend)")
                parts.append(f"EMA5 exit target: {ema5:,.0f}")

            # ── SELL/SHORT signals (bear regime only) ────────────────────────
            elif rsi2 >= self.SELL_THRESH and allow_shorts and not above_ema200:
                action = "SELL"
                strength = "STRONG" if rsi2 >= self.SELL_STRONG else "MODERATE"
                score    = 8.0 if rsi2 >= self.SELL_STRONG else 6.5
                parts.append(f"RSI(2)={rsi2:.1f} — deeply overbought")
                parts.append("below EMA200 (downtrend) — mean reversion SHORT")

            else:
                parts.append(f"RSI(2)={rsi2:.1f} — no extreme")
                if not above_ema200 and rsi2 < self.BUY_THRESH:
                    parts.append("skipped: below EMA200 (not in uptrend)")

            score   = round(min(max(score, 0.0), 10.0), 2)
            reasoning = "; ".join(parts)

            return RSI2Result(
                symbol=symbol, rsi2=round(rsi2, 1), rsi14=round(rsi14, 1),
                action=action, signal_strength=strength,
                above_ema200=above_ema200, above_ema50=above_ema50,
                ema5=round(ema5, 2), score=score, reasoning=reasoning,
            )

        except Exception as e:
            logger.debug(f"{symbol} RSI2 scan error: {e}")
            return self._default(symbol)

    def scan_all(self, market_data: dict,
                 allow_shorts: bool = False) -> dict[str, RSI2Result]:
        """
        Scan all symbols. Returns only actionable (BUY or SELL) results.
        """
        actionable = {}
        for sym, df in market_data.items():
            r = self.scan(sym, df, allow_shorts=allow_shorts)
            if r.action in ("BUY", "SELL"):
                actionable[sym] = r
        buys  = sum(1 for r in actionable.values() if r.action == "BUY")
        sells = sum(1 for r in actionable.values() if r.action == "SELL")
        logger.info(f"RSI2 scan: {buys} buys, {sells} sells (of {len(market_data)} scanned)")
        return actionable

    # ------------------------------------------------------------------

    @staticmethod
    def _rsi(series: pd.Series, period: int) -> float:
        """Compute RSI for the given period."""
        delta = series.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / loss.replace(0, float("nan"))
        rsi   = 100 - 100 / (1 + rs)
        val   = float(rsi.iloc[-1])
        return val if not (val != val) else 50.0   # NaN guard

    def _default(self, symbol: str) -> RSI2Result:
        return RSI2Result(
            symbol=symbol, rsi2=50.0, rsi14=50.0,
            action="HOLD", signal_strength="WEAK",
            above_ema200=True, above_ema50=True,
            ema5=0.0, score=5.0, reasoning="Insufficient data",
        )
