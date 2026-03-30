# =============================================================================
# analysis/momentum_filter.py — Stock Universe Momentum Filter
#
# Before any signal is generated, this filter screens out stocks that are in
# a persistent downtrend. Only stocks passing ALL gates enter the pipeline.
#
# Gates (all must pass for a BUY signal to be allowed):
#   1. Price above EMA50  — intermediate uptrend intact
#   2. Price above EMA200 — long-term uptrend intact
#   3. 3-month return > -5% — not in freefall
#   4. RSI > 35 — not deeply oversold/broken
#   5. Volume trend up — accumulation, not distribution
#
# For BEAR-mode SHORT watchlist the gates are inverted.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from dataclasses import dataclass
from utils import get_logger

logger = get_logger("MomentumFilter")


@dataclass
class MomentumResult:
    symbol:          str
    passes:          bool        # True = allow BUY signals
    passes_short:    bool        # True = allow SHORT signals
    price:           float
    ema50:           float
    ema200:          float
    ret_3m:          float       # 3-month return %
    rsi:             float
    volume_trend:    str         # up | down | flat
    reason:          str         # why it passed or failed


class MomentumFilter:
    """
    Screens stocks for trend health before allowing signals.
    Eliminates the biggest source of false positives: buying into downtrends.
    """

    def filter(self, symbol: str, df: pd.DataFrame) -> MomentumResult:
        """
        Run momentum gates on one stock.
        df must have columns: open, high, low, close, volume (lowercase).
        """
        try:
            close  = df["close"]
            volume = df["volume"]
            last   = float(close.iloc[-1])

            ema50  = float(close.ewm(span=50,  adjust=False).mean().iloc[-1])
            ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])

            # 3-month return (≈63 trading days)
            ret_3m = float((last / close.iloc[-63] - 1) * 100) if len(close) >= 63 else 0.0

            # RSI(14)
            delta  = close.diff()
            gain   = delta.clip(lower=0).rolling(14).mean()
            loss   = (-delta.clip(upper=0)).rolling(14).mean()
            rs     = gain / loss.replace(0, np.nan)
            rsi    = float(100 - 100 / (1 + rs.iloc[-1]))

            # Volume trend — 10-day avg vs 30-day avg
            vol10  = float(volume.rolling(10).mean().iloc[-1])
            vol30  = float(volume.rolling(30).mean().iloc[-1])
            if vol30 > 0:
                vol_ratio = vol10 / vol30
                if vol_ratio >= 1.1:
                    volume_trend = "up"
                elif vol_ratio <= 0.85:
                    volume_trend = "down"
                else:
                    volume_trend = "flat"
            else:
                volume_trend = "flat"

            # ----------------------------------------------------------
            # BUY gates — ALL must pass
            # ----------------------------------------------------------
            buy_gates = {
                "above_ema50":  last > ema50,
                "above_ema200": last > ema200,
                "ret3m_ok":     ret_3m > -5.0,
                "rsi_ok":       rsi > 35,
            }
            passes_buy = all(buy_gates.values())

            failed = [k for k, v in buy_gates.items() if not v]
            reason = (
                "All gates passed — trend healthy"
                if passes_buy
                else "Failed: " + ", ".join(failed)
            )

            # ----------------------------------------------------------
            # SHORT gates — inverted (bearish momentum)
            # ----------------------------------------------------------
            short_gates = {
                "below_ema50":  last < ema50,
                "below_ema200": last < ema200,
                "ret3m_weak":   ret_3m < 5.0,
                "rsi_weak":     rsi < 65,
            }
            passes_short = all(short_gates.values())

            return MomentumResult(
                symbol       = symbol,
                passes       = passes_buy,
                passes_short = passes_short,
                price        = round(last, 2),
                ema50        = round(ema50, 2),
                ema200       = round(ema200, 2),
                ret_3m       = round(ret_3m, 2),
                rsi          = round(rsi, 1),
                volume_trend = volume_trend,
                reason       = reason,
            )

        except Exception as e:
            logger.debug(f"{symbol} momentum filter error: {e}")
            # On error — allow through (don't silently block)
            return MomentumResult(
                symbol=symbol, passes=True, passes_short=False,
                price=0, ema50=0, ema200=0, ret_3m=0, rsi=50,
                volume_trend="flat", reason=f"Filter error: {e}"
            )

    def filter_all(
        self,
        market_data: dict,
        mode: str = "buy",    # "buy" | "short" | "both"
    ) -> dict:
        """
        Filter a dict of {symbol: DataFrame}.
        Returns {symbol: MomentumResult} for passing stocks only.
        """
        results  = {}
        passed   = 0
        blocked  = 0

        for symbol, df in market_data.items():
            if df is None or df.empty:
                continue
            result = self.filter(symbol, df)

            keep = False
            if mode == "buy"   and result.passes:       keep = True
            if mode == "short" and result.passes_short: keep = True
            if mode == "both"  and (result.passes or result.passes_short):
                keep = True

            if keep:
                results[symbol] = result
                passed += 1
            else:
                blocked += 1

        logger.info(f"MomentumFilter ({mode}): {passed} passed, {blocked} blocked")
        return results
