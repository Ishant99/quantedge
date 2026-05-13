# =============================================================================
# analysis/futures_signals.py — Nifty / BankNifty Futures Signals
#
# LONG when index is in confirmed uptrend (EMA + momentum)
# SHORT when index is in confirmed downtrend
# Used for paper trading futures alongside options
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
from config import FUTURES_RISK_FREE_RATE, FUTURES_DEFAULT_DTE
import numpy as np
import pandas as pd
from dataclasses import dataclass
from datetime import datetime, timedelta
import pytz
from utils import get_logger

logger = get_logger("FuturesSignals")
IST = pytz.timezone("Asia/Kolkata")

INDEX_TICKERS = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}
from config import FNO_LOT_SIZES as LOT_SIZES


@dataclass
class FuturesSignal:
    index:     str          # NIFTY | BANKNIFTY
    direction: str          # LONG | SHORT
    entry_price: float      # futures price
    sl_price:  float        # 2% adverse move
    target_price: float     # 3% favourable
    expiry:    str
    lot_size:  int
    confidence: float
    reasoning: str


class FuturesSignalGenerator:

    def run(self) -> list[FuturesSignal]:
        signals = []
        for index, ticker in INDEX_TICKERS.items():
            sig = self._analyse(index, ticker)
            if sig:
                signals.append(sig)
        return signals

    def _analyse(self, index: str, ticker: str) -> FuturesSignal | None:
        try:
            df = yf.Ticker(ticker).history(period="3mo", interval="1d", auto_adjust=True)
            if df.empty or len(df) < 50:
                return None
            df.columns = [c.lower() for c in df.columns]
            close = df["close"]
            spot  = float(close.iloc[-1])

            ema20  = float(close.ewm(span=20,  adjust=False).mean().iloc[-1])
            ema50  = float(close.ewm(span=50,  adjust=False).mean().iloc[-1])
            ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])

            delta = close.diff()
            gain  = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
            loss  = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
            last_loss_f = float(loss.iloc[-1])
            if last_loss_f == 0:
                rsi = 100.0
            else:
                rsi = float(100 - 100 / (1 + gain / loss).iloc[-1])
                rsi = rsi if not np.isnan(rsi) else 50.0

            ret_1w = float((spot / close.iloc[-6]  - 1) * 100) if len(close) >= 6  else 0
            ret_1m = float((spot / close.iloc[-22] - 1) * 100) if len(close) >= 22 else 0

            fut_price = round(spot * (1 + FUTURES_RISK_FREE_RATE * FUTURES_DEFAULT_DTE / 365), 2)

            bull = sum([spot > ema20, spot > ema50, spot > ema200, rsi > 55, ret_1w > 0])
            bear = sum([spot < ema20, spot < ema50, spot < ema200, rsi < 45, ret_1w < 0])

            if bull >= 4:
                direction = "LONG"
                sl     = round(fut_price * 0.98, 0)
                target = round(fut_price * 1.03, 0)
                conf   = 0.50 + bull * 0.06
                reasons = f"above EMA20/50, RSI {rsi:.0f}, 1W {ret_1w:+.1f}%"
            elif bear >= 4:
                direction = "SHORT"
                sl     = round(fut_price * 1.02, 0)
                target = round(fut_price * 0.97, 0)
                conf   = 0.50 + bear * 0.06
                reasons = f"below EMA20/50, RSI {rsi:.0f}, 1W {ret_1w:+.1f}%"
            else:
                return None

            # Nearest last-Thursday expiry
            today = datetime.now(IST)
            # Find last Thursday of current month
            year, month = today.year, today.month
            last_day = (datetime(year, month % 12 + 1, 1) - timedelta(days=1)
                        if month < 12 else datetime(year + 1, 1, 1) - timedelta(days=1))
            while last_day.weekday() != 3:
                last_day -= timedelta(days=1)
            if last_day.date() <= today.date():
                # Move to next month
                month = month % 12 + 1
                year  = year + 1 if month == 1 else year
                last_day = (datetime(year, month % 12 + 1, 1) - timedelta(days=1)
                            if month < 12 else datetime(year + 1, 1, 1) - timedelta(days=1))
                while last_day.weekday() != 3:
                    last_day -= timedelta(days=1)
            expiry = last_day.strftime("%d-%b-%Y")

            return FuturesSignal(
                index       = index,
                direction   = direction,
                entry_price = fut_price,
                sl_price    = sl,
                target_price= target,
                expiry      = expiry,
                lot_size    = LOT_SIZES[index],
                confidence  = round(min(conf, 0.85), 3),
                reasoning   = f"{index} FUT {direction} | {reasons} | Expiry {expiry}",
            )
        except Exception as e:
            logger.error(f"Futures signal error ({index}): {e}")
            return None
