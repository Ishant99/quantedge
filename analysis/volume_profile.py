# =============================================================================
# analysis/volume_profile.py — Volume Profile Analysis
#
# Finds high-volume price zones (Point of Control).
# Prices gravitate toward these levels — great for entries/exits.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from dataclasses import dataclass
from utils import get_logger

logger = get_logger("VolumeProfile")


@dataclass
class VolumeProfileResult:
    symbol:          str
    poc:             float     # Point of Control — highest volume price
    value_area_high: float     # 70% of volume traded below this
    value_area_low:  float     # 70% of volume traded above this
    current_vs_poc:  float     # % distance from current price to POC
    in_value_area:   bool      # True if price inside value area
    vp_score:        float     # 0-10
    signal:          str       # buy | sell | neutral
    message:         str


class VolumeProfileAnalyser:
    """
    Builds a volume profile from historical OHLCV data.
    Identifies where most trading has occurred — strong S/R zones.
    """

    NUM_BINS = 50   # price bins for the profile

    def analyse(self, symbol: str, df: pd.DataFrame,
                lookback: int = 60) -> VolumeProfileResult:
        """Build volume profile from last N days."""
        try:
            data  = df.tail(lookback).copy()
            close = data["close"]
            high  = data["high"]
            low   = data["low"]
            vol   = data["volume"]
            last  = float(close.iloc[-1])

            # Price range
            price_min = float(low.min())
            price_max = float(high.max())
            bin_size  = (price_max - price_min) / self.NUM_BINS

            # Build volume histogram
            bins    = np.linspace(price_min, price_max, self.NUM_BINS + 1)
            vp      = np.zeros(self.NUM_BINS)

            for i in range(len(data)):
                # Distribute volume across price range for each bar
                bar_low  = float(low.iloc[i])
                bar_high = float(high.iloc[i])
                bar_vol  = float(vol.iloc[i])
                bar_close= float(close.iloc[i])

                # Weight volume toward close price (price discovery)
                for b in range(self.NUM_BINS):
                    bin_low  = bins[b]
                    bin_high = bins[b + 1]
                    # Overlap between bar range and bin
                    overlap = max(0, min(bar_high, bin_high) -
                                     max(bar_low, bin_low))
                    bar_range = max(bar_high - bar_low, 0.01)
                    if overlap > 0:
                        vp[b] += bar_vol * (overlap / bar_range)

            # Point of Control = bin with highest volume
            poc_idx = np.argmax(vp)
            poc     = float((bins[poc_idx] + bins[poc_idx + 1]) / 2)

            # Value Area = 70% of total volume
            total_vol  = vp.sum()
            target_vol = total_vol * 0.70
            vp_sorted  = sorted(enumerate(vp), key=lambda x: x[1], reverse=True)

            va_indices = set()
            accum      = 0
            for idx, v in vp_sorted:
                va_indices.add(idx)
                accum += v
                if accum >= target_vol:
                    break

            va_bins = sorted(va_indices)
            vah     = float((bins[va_bins[-1]] + bins[va_bins[-1]+1]) / 2)
            val     = float((bins[va_bins[0]]  + bins[va_bins[0]+1])  / 2)

            # Current price position
            in_va   = val <= last <= vah
            poc_dist= (last - poc) / poc * 100

            # Score and signal
            if val <= last <= poc * 1.02:
                # Price near POC from below — strong support
                vp_score = 8.0
                signal   = "buy"
                msg      = f"Price near POC Rs.{poc:,.0f} — high volume support"
            elif last < val:
                # Below value area — potential mean reversion buy
                vp_score = 7.5
                signal   = "buy"
                msg      = f"Below value area — mean reversion opportunity"
            elif last > vah * 1.02:
                # Extended above value area — overbought
                vp_score = 3.5
                signal   = "sell"
                msg      = f"Extended above value area Rs.{vah:,.0f}"
            else:
                vp_score = 5.5
                signal   = "neutral"
                msg      = f"Inside value area (Rs.{val:,.0f} - Rs.{vah:,.0f})"

            return VolumeProfileResult(
                symbol          = symbol,
                poc             = round(poc, 2),
                value_area_high = round(vah, 2),
                value_area_low  = round(val, 2),
                current_vs_poc  = round(poc_dist, 2),
                in_value_area   = in_va,
                vp_score        = round(vp_score, 2),
                signal          = signal,
                message         = msg,
            )

        except Exception as e:
            logger.debug(f"{symbol} volume profile failed: {e}")
            return self._default(symbol, float(df["close"].iloc[-1]))

    def analyse_all(self, market_data: dict) -> dict[str, VolumeProfileResult]:
        results = {}
        for sym, df in market_data.items():
            results[sym] = self.analyse(sym, df)
        buy_zone = sum(1 for r in results.values() if r.signal == "buy")
        logger.info(f"Volume profile: {buy_zone}/{len(results)} in buy zone")
        return results

    def _default(self, symbol: str, price: float) -> VolumeProfileResult:
        return VolumeProfileResult(
            symbol=symbol, poc=price,
            value_area_high=price*1.03,
            value_area_low=price*0.97,
            current_vs_poc=0.0, in_value_area=True,
            vp_score=5.0, signal="neutral",
            message="Volume profile unavailable"
        )
