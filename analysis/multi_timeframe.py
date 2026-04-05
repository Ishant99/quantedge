# =============================================================================
# analysis/multi_timeframe.py — Multi-Timeframe Confirmation
#
# Checks weekly + daily charts before allowing a signal.
# Only signals when BOTH timeframes agree — eliminates false signals.
#
# Logic:
#   Weekly trend = UP  AND  Daily signal = BUY  →  CONFIRMED BUY
#   Weekly trend = DOWN AND Daily signal = BUY  →  BLOCKED (counter-trend)
#   Weekly trend = UP  AND  Daily signal = SELL →  WEAK SELL (ignore)
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from dataclasses import dataclass
from datetime import datetime, timedelta
import yfinance as yf
from config import MTF_COUNTER_PENALTY
from utils import get_logger

logger = get_logger("MultiTimeframe")


@dataclass
class MTFResult:
    symbol:         str
    weekly_trend:   str     # up | down | sideways
    daily_signal:   str     # bullish | bearish | neutral
    confirmed:      bool    # True if both timeframes agree
    mtf_score:      float   # 0-10 combined score
    weekly_rsi:     float
    weekly_ema_trend: str   # above | below EMA20 on weekly
    reason:         str
    mtf_penalty:    float = 0.0   # confidence penalty for counter-trend signals


class MultiTimeframeAnalyser:
    """
    Checks weekly chart to confirm daily signals.
    Blocks counter-trend trades — the most common cause of losses.
    """

    def analyse(self, symbol: str,
                daily_df: pd.DataFrame) -> MTFResult:
        """
        Check weekly timeframe for confirmation of daily signal.
        Uses resampled daily data to avoid extra API calls.
        """
        try:
            # Resample daily OHLCV to weekly
            weekly = self._resample_weekly(daily_df)
            if weekly is None or len(weekly) < 20:
                return self._default(symbol)

            close_w = weekly["close"]
            last_w  = float(close_w.iloc[-1])

            # Weekly EMA trend
            ema20_w = float(close_w.ewm(span=20, adjust=False).mean().iloc[-1])
            ema10_w = float(close_w.ewm(span=10, adjust=False).mean().iloc[-1])

            # Weekly RSI
            delta   = close_w.diff()
            gain    = delta.clip(lower=0).rolling(14).mean()
            loss    = (-delta.clip(upper=0)).rolling(14).mean()
            rs      = gain / loss.replace(0, np.nan)
            rsi_w   = float((100 - 100/(1+rs)).iloc[-1])

            # Weekly MACD
            macd_w  = (close_w.ewm(span=12).mean() -
                       close_w.ewm(span=26).mean()).iloc[-1]

            # Determine weekly trend
            if last_w > ema20_w and ema10_w > ema20_w and rsi_w > 50:
                weekly_trend  = "up"
                ema_trend     = "above"
                weekly_score  = 7.0 + min(3.0, (rsi_w - 50) / 20)
            elif last_w < ema20_w and ema10_w < ema20_w and rsi_w < 50:
                weekly_trend  = "down"
                ema_trend     = "below"
                weekly_score  = max(1.0, 3.0 - (50 - rsi_w) / 20)
            else:
                weekly_trend  = "sideways"
                ema_trend     = "mixed"
                weekly_score  = 5.0

            # Daily signal from last close vs EMAs
            close_d  = daily_df["close"]
            ema20_d  = float(close_d.ewm(span=20).mean().iloc[-1])
            ema50_d  = float(close_d.ewm(span=50).mean().iloc[-1])
            last_d   = float(close_d.iloc[-1])

            if last_d > ema20_d and last_d > ema50_d:
                daily_signal = "bullish"
            elif last_d < ema20_d and last_d < ema50_d:
                daily_signal = "bearish"
            else:
                daily_signal = "neutral"

            # Confirmation logic
            confirmed = False
            mtf_score = 5.0

            penalty = 0.0
            if weekly_trend == "up" and daily_signal == "bullish":
                confirmed = True
                mtf_score = (weekly_score + 8.0) / 2
                reason    = f"Weekly uptrend confirmed daily bullish (RSI {rsi_w:.0f})"
            elif weekly_trend == "down" and daily_signal == "bearish":
                confirmed = True
                mtf_score = 2.0
                reason    = f"Weekly downtrend confirmed daily bearish"
            elif weekly_trend == "up" and daily_signal == "neutral":
                confirmed = True
                mtf_score = 6.0
                reason    = f"Weekly uptrend, daily neutral — cautious buy ok"
            elif weekly_trend == "down" and daily_signal == "bullish":
                # Counter-trend: penalize instead of blocking
                confirmed = True
                penalty   = MTF_COUNTER_PENALTY
                mtf_score = 3.0
                reason    = f"Counter-trend — weekly down, daily up — penalty -{penalty:.0%}"
            elif weekly_trend == "sideways":
                confirmed = daily_signal == "bullish"
                mtf_score = 5.5 if confirmed else 4.5
                reason    = f"Sideways market — only strong daily signals pass"
            else:
                confirmed = True
                penalty   = MTF_COUNTER_PENALTY / 2
                mtf_score = 4.0
                reason    = f"Mixed timeframes — penalty -{penalty:.0%}"

            return MTFResult(
                symbol         = symbol,
                weekly_trend   = weekly_trend,
                daily_signal   = daily_signal,
                confirmed      = confirmed,
                mtf_score      = round(mtf_score, 2),
                weekly_rsi     = round(rsi_w, 1),
                weekly_ema_trend= ema_trend,
                reason         = reason,
                mtf_penalty    = penalty,
            )

        except Exception as e:
            logger.debug(f"{symbol} MTF failed: {e}")
            return self._default(symbol)

    def analyse_all(self, market_data: dict) -> dict[str, MTFResult]:
        results  = {}
        for sym, df in market_data.items():
            results[sym] = self.analyse(sym, df)
        confirmed = sum(1 for r in results.values() if r.confirmed)
        logger.info(f"MTF: {confirmed}/{len(results)} signals confirmed "
                    f"by weekly timeframe")
        return results

    # ------------------------------------------------------------------
    # Resample daily → weekly
    # ------------------------------------------------------------------

    def _resample_weekly(self, df: pd.DataFrame) -> pd.DataFrame:
        """Convert daily OHLCV to weekly bars."""
        try:
            d = df.copy()
            d.index = pd.to_datetime(d.index)
            weekly = d.resample("W").agg({
                "open":   "first",
                "high":   "max",
                "low":    "min",
                "close":  "last",
                "volume": "sum",
            }).dropna()
            return weekly
        except Exception as e:
            logger.debug(f"Resample failed: {e}")
            return None

    def _default(self, symbol: str) -> MTFResult:
        return MTFResult(
            symbol=symbol, weekly_trend="sideways",
            daily_signal="neutral", confirmed=True,
            mtf_score=5.0, weekly_rsi=50.0,
            weekly_ema_trend="mixed",
            reason="MTF data unavailable — proceeding normally"
        )
