# =============================================================================
# analysis/support_resistance.py — Support & Resistance Level Detection
#
# Auto-detects key price levels from historical data using:
#   - Pivot points (swing highs and lows)
#   - Volume-weighted price zones
#   - Round number levels
#
# Only buy near support, avoid buying near resistance.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from dataclasses import dataclass
from utils import get_logger

logger = get_logger("SupportResistance")


@dataclass
class SRResult:
    symbol:          str
    supports:        list[float]    # key support levels below current price
    resistances:     list[float]    # key resistance levels above current price
    nearest_support: float          # closest support below price
    nearest_resist:  float          # closest resistance above price
    sr_score:        float          # 0-10: how good is current position vs S/R
    near_support:    bool           # True if price is within 2% of support
    near_resistance: bool           # True if price is within 2% of resistance
    recommendation:  str            # buy_zone | sell_zone | neutral


class SupportResistanceAnalyser:
    """
    Detects support and resistance levels from price history.
    Used to filter signals — only buy near support, avoid near resistance.
    """

    PROXIMITY_PCT = 0.02   # within 2% = "near" a level
    MIN_TOUCHES   = 2      # level needs at least 2 touches to be valid

    def analyse(self, symbol: str, df: pd.DataFrame) -> SRResult:
        """Detect S/R levels and score current price position."""
        try:
            close  = df["close"]
            high   = df["high"]
            low    = df["low"]
            last   = float(close.iloc[-1])

            # Find pivot points (swing highs and lows)
            pivots_high = self._find_pivots(high, is_high=True)
            pivots_low  = self._find_pivots(low,  is_high=False)

            # Cluster nearby levels together
            resistances = self._cluster_levels(
                [h for h in pivots_high if h > last], last
            )
            supports    = self._cluster_levels(
                [l for l in pivots_low if l < last], last
            )

            # Add round number levels (psychological levels)
            round_levels = self._round_number_levels(last)
            supports    += [r for r in round_levels if r < last * 0.99]
            resistances += [r for r in round_levels if r > last * 1.01]

            # Sort
            supports    = sorted(set([round(s, 2) for s in supports]),    reverse=True)[:5]
            resistances = sorted(set([round(r, 2) for r in resistances]))[:5]

            nearest_sup = supports[0]    if supports    else last * 0.95
            nearest_res = resistances[0] if resistances else last * 1.05

            # Distance checks
            sup_dist = (last - nearest_sup) / last
            res_dist = (nearest_res - last) / last
            near_sup = sup_dist <= self.PROXIMITY_PCT
            near_res = res_dist <= self.PROXIMITY_PCT

            # SR Score — higher = better position to buy
            # Best: near support, far from resistance
            # Worst: near resistance, far from support
            if near_sup and not near_res:
                sr_score      = 8.5
                recommendation = "buy_zone"
            elif near_res:
                sr_score      = 2.5
                recommendation = "sell_zone"
            elif sup_dist < 0.05:
                sr_score      = 7.0
                recommendation = "buy_zone"
            elif res_dist < 0.05:
                sr_score      = 3.5
                recommendation = "neutral"
            else:
                sr_score      = 5.5
                recommendation = "neutral"

            return SRResult(
                symbol          = symbol,
                supports        = supports,
                resistances     = resistances,
                nearest_support = nearest_sup,
                nearest_resist  = nearest_res,
                sr_score        = sr_score,
                near_support    = near_sup,
                near_resistance = near_res,
                recommendation  = recommendation,
            )

        except Exception as e:
            logger.debug(f"{symbol} S/R failed: {e}")
            try:
                price = float(df["close"].dropna().iloc[-1])
            except Exception:
                price = 0.0
            return self._default(symbol, price)

    def analyse_all(self, market_data: dict) -> dict[str, SRResult]:
        results = {}
        for sym, df in market_data.items():
            results[sym] = self.analyse(sym, df)
        buy_zone = sum(1 for r in results.values() if r.recommendation == "buy_zone")
        logger.info(f"S/R analysis: {buy_zone}/{len(results)} stocks in buy zone")
        return results

    # ------------------------------------------------------------------
    # Pivot detection
    # ------------------------------------------------------------------

    def _find_pivots(self, series: pd.Series,
                     is_high: bool, window: int = 10) -> list[float]:
        """Find swing highs or lows using rolling window."""
        pivots = []
        for i in range(window, len(series) - window):
            window_slice = series.iloc[i-window:i+window+1]
            val = series.iloc[i]
            if is_high and val == window_slice.max():
                pivots.append(float(val))
            elif not is_high and val == window_slice.min():
                pivots.append(float(val))
        return pivots

    def _cluster_levels(self, levels: list[float],
                        current: float, tolerance: float = 0.015) -> list[float]:
        """
        Cluster nearby price levels together.
        Levels within 1.5% of each other are merged into one.
        """
        if not levels:
            return []

        levels = sorted(levels)
        clusters = []
        cluster  = [levels[0]]

        for level in levels[1:]:
            if (level - cluster[-1]) / cluster[-1] <= tolerance:
                cluster.append(level)
            else:
                clusters.append(np.mean(cluster))
                cluster = [level]
        clusters.append(np.mean(cluster))

        return [round(c, 2) for c in clusters]

    def _round_number_levels(self, price: float) -> list[float]:
        """Generate round number levels near current price (psychological S/R)."""
        levels = []
        # Find appropriate rounding based on price range
        if price > 5000:
            step = 500
        elif price > 1000:
            step = 100
        elif price > 500:
            step = 50
        elif price > 100:
            step = 25
        else:
            step = 10

        base  = int(price / step) * step
        for mult in range(-5, 6):
            levels.append(base + mult * step)
        return [l for l in levels if l > 0]

    def _default(self, symbol: str, price: float) -> SRResult:
        return SRResult(
            symbol=symbol, supports=[round(price*0.95, 2)],
            resistances=[round(price*1.05, 2)],
            nearest_support=round(price*0.95, 2),
            nearest_resist=round(price*1.05, 2),
            sr_score=5.0, near_support=False,
            near_resistance=False, recommendation="neutral"
        )
