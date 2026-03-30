# =============================================================================
# analysis/market_regime.py — Market Regime Filter
#
# Checks if the overall market (Nifty 50) is in a bull, bear, or sideways
# regime BEFORE scanning individual stocks.
#
# In a bear regime  → block all BUY signals, only allow SELL
# In a sideways     → raise confidence threshold, reduce position size
# In a bull regime  → full normal operation
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from dataclasses import dataclass
from datetime import datetime, timedelta
import yfinance as yf
from utils import get_logger

logger = get_logger("MarketRegime")


@dataclass
class RegimeResult:
    regime:         str     # bull | bear | sideways
    nifty_trend:    str     # up | down | flat
    nifty_rsi:      float
    nifty_above_200ema: bool
    nifty_1m_return:float   # 1 month return %
    nifty_3m_return:float   # 3 month return %
    allow_buys:     bool
    allow_shorts:   bool    # True in bear/sideways — scan for bearish setups
    position_size_multiplier: float   # 1.0 = normal, 0.5 = cautious, 0.0 = blocked
    message:        str


class MarketRegimeFilter:
    """
    Checks Nifty 50 trend before allowing buy signals.
    Protects portfolio from buying into falling markets.
    """

    NIFTY_TICKER = "^NSEI"   # Nifty 50 on yfinance

    def get_regime(self) -> RegimeResult:
        """Fetch Nifty 50 data and determine current market regime."""
        try:
            df = yf.Ticker(self.NIFTY_TICKER).history(
                period="1y", interval="1d", auto_adjust=True
            )
            if df.empty or len(df) < 50:
                logger.warning("Could not fetch Nifty data — assuming bull regime")
                return self._default_bull()

            close = df["Close"]
            last  = float(close.iloc[-1])

            # EMA 200
            ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
            ema50  = float(close.ewm(span=50,  adjust=False).mean().iloc[-1])
            ema20  = float(close.ewm(span=20,  adjust=False).mean().iloc[-1])

            # RSI
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rsi   = float(100 - (100 / (1 + gain / loss.replace(0, np.nan))).iloc[-1])

            # Returns
            ret_1m = float((last / close.iloc[-22] - 1) * 100) if len(close) >= 22 else 0
            ret_3m = float((last / close.iloc[-66] - 1) * 100) if len(close) >= 66 else 0

            above_200 = last > ema200

            # ----------------------------------------------------------
            # Regime classification
            # ----------------------------------------------------------
            if (last > ema200 and last > ema50 and ema20 > ema50
                    and ret_1m > -3 and rsi > 40):
                regime       = "bull"
                trend        = "up"
                allow        = True
                allow_shorts = False
                ps_mult      = 1.0
                message      = "Bull market — Nifty above all EMAs. Normal trading."

            elif (last < ema200 and ret_1m < -5) or rsi < 35:
                regime       = "bear"
                trend        = "down"
                allow        = False
                allow_shorts = True
                ps_mult      = 0.0
                message      = (f"Bear market — Nifty below EMA200, RSI {rsi:.0f}. "
                                f"BUY signals BLOCKED. Scanning for SELL/SHORT setups.")

            elif last < ema200 or (ret_1m < -2 and ret_3m < -5):
                regime       = "sideways"
                trend        = "flat"
                allow        = True
                allow_shorts = True
                ps_mult      = 0.5
                message      = ("Sideways/weak market — position sizes halved, "
                                "only high-confidence signals allowed.")
            else:
                regime       = "bull"
                trend        = "up"
                allow        = True
                allow_shorts = False
                ps_mult      = 0.8
                message      = "Mild bull — slightly cautious, 80% position sizes."

            result = RegimeResult(
                regime                   = regime,
                nifty_trend              = trend,
                nifty_rsi                = round(rsi, 1),
                nifty_above_200ema       = above_200,
                nifty_1m_return          = round(ret_1m, 2),
                nifty_3m_return          = round(ret_3m, 2),
                allow_buys               = allow,
                allow_shorts             = allow_shorts,
                position_size_multiplier = ps_mult,
                message                  = message,
            )

            logger.info(f"Market regime: {regime.upper()} | "
                        f"Nifty RSI: {rsi:.0f} | "
                        f"1M: {ret_1m:+.1f}% | "
                        f"Above EMA200: {above_200} | "
                        f"Allow buys: {allow}")
            return result

        except Exception as e:
            logger.warning(f"Regime check failed: {e} — assuming bull")
            return self._default_bull()

    def _default_bull(self) -> RegimeResult:
        return RegimeResult(
            regime="bull", nifty_trend="up", nifty_rsi=50.0,
            nifty_above_200ema=True, nifty_1m_return=0.0,
            nifty_3m_return=0.0, allow_buys=True, allow_shorts=False,
            position_size_multiplier=1.0,
            message="Market data unavailable — proceeding with normal operation."
        )
