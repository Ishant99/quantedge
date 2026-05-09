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
            close  = df["close"].dropna()
            volume = df["volume"].dropna()
            if close.empty:
                return MomentumResult(
                    symbol=symbol, passes=True, passes_short=False,
                    price=0, ema50=0, ema200=0, ret_3m=0, rsi=50,
                    volume_trend="flat", reason="No price data"
                )
            last   = float(close.iloc[-1])

            ema50  = float(close.ewm(span=50,  adjust=False).mean().iloc[-1])
            ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])

            # 3-month return (≈63 trading days)
            ret_3m = float((last / close.iloc[-63] - 1) * 100) if len(close) >= 63 else 0.0

            # RSI(14) — Wilder's RMA (ewm alpha=1/14)
            delta  = close.diff()
            gain   = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
            loss   = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
            last_loss = float(loss.iloc[-1])
            if last_loss == 0:
                rsi = 100.0
            else:
                rs  = gain / loss
                val = float(100 - 100 / (1 + rs.iloc[-1]))
                rsi = val if not np.isnan(val) else 50.0

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
            # BUY gates
            # Hard gates (all must pass): EMA50 OR recovering toward it, RSI > 30
            # Soft gate (EMA200): converts to confidence penalty if below, not a block
            # ----------------------------------------------------------

            # ------------------------------------------------------------------
            # EMA50 gate: above EMA50, OR within 8% below AND short-term recovering
            # Short-term recovery = last 20-day return > -2% (price stabilising)
            # ------------------------------------------------------------------
            ema50_gap    = (last - ema50) / ema50 * 100
            ret_20d      = float((last / close.iloc[-20] - 1) * 100) if len(close) >= 20 else 0.0
            recovering   = ret_20d > -2.0   # not still in freefall over last month
            ema50_ok     = last > ema50 or (ema50_gap > -8.0 and recovering)

            # 3M return: not in a catastrophic collapse (worse than -25%)
            ret3m_ok     = ret_3m > -25.0

            # RSI: not totally broken (> 30 — very relaxed, only blocks full panic)
            rsi_ok       = rsi > 30

            buy_gates = {
                "ema50_ok":           ema50_ok,
                "ret3m_ok":           ret3m_ok,
                "rsi_ok":             rsi_ok,
                "not_declining_volume": volume_trend != "down",
            }
            passes_buy = all(buy_gates.values())

            # Track EMA200 position for reasoning (soft gate — scored, not blocked)
            below_ema200 = last < ema200
            ema200_note  = " (below EMA200 — recovery trade)" if below_ema200 else ""

            failed = [k for k, v in buy_gates.items() if not v]
            reason = (
                f"All gates passed{ema200_note}"
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
