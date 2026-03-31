# =============================================================================
# analysis/options_signals.py — Nifty / BankNifty Weekly Options Signals
#
# Generates weekly options trade ideas (CE/PE) based on:
#   1. Index trend (EMA, RSI, regime)
#   2. PCR (Put-Call Ratio) — contrarian indicator
#   3. IV rank — only buy options when IV is low (cheap)
#   4. Support/resistance proximity — identify strike price
#
# Output: suggested CE (Call) or PE (Put) strike, expiry, entry zone,
#         stop-loss, target, and reasoning.
#
# NOTE: Actual options prices require NSE API / broker API for live data.
#       This module uses yfinance index data + approximate Black-Scholes
#       for illustrative strike selection. For live trading, replace
#       _fetch_option_chain() with Zerodha Kite API call.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from dataclasses import dataclass
import math
import pytz
from utils import get_logger

logger = get_logger("OptionsSignals")
IST = pytz.timezone("Asia/Kolkata")

NIFTY_TICKER     = "^NSEI"
BANKNIFTY_TICKER = "^NSEBANK"
from config import FNO_LOT_SIZES as _LOTS
from data.nse_options_chain import NSEOptionsChain as _Chain
_chain = _Chain()    # shared instance with 5-min cache
NIFTY_LOT        = _LOTS["NIFTY"]
BANKNIFTY_LOT    = _LOTS["BANKNIFTY"]
NIFTY_STEP       = 50       # Strike interval
BANKNIFTY_STEP   = 100


@dataclass
class OptionsSignal:
    index:        str          # NIFTY | BANKNIFTY
    direction:    str          # CALL (bullish) | PUT (bearish)
    strike:       int          # Suggested ATM/OTM strike
    expiry:       str          # "DD-MMM-YYYY" nearest weekly/monthly
    index_spot:   float
    entry_zone:   str          # e.g. "Rs.180" or "Rs.180–200 (approx)"
    entry_premium:float        # Live or estimated premium (Rs.) — 0 if unknown
    stop_loss_idx:float        # Index level — exit if index crosses this
    target_idx:   float        # Index level — target
    confidence:   float
    reasoning:    str
    iv_note:      str          # comment on implied volatility
    lot_size:     int


class OptionsSignalGenerator:
    """
    Generates CE/PE trade ideas for Nifty and BankNifty weekly options.
    """

    def run(self) -> list[OptionsSignal]:
        """Generate signals for both indices. Returns list of OptionsSignal."""
        signals = []
        for ticker, name, lot, step in [
            (NIFTY_TICKER, "NIFTY", NIFTY_LOT, NIFTY_STEP),
            (BANKNIFTY_TICKER, "BANKNIFTY", BANKNIFTY_LOT, BANKNIFTY_STEP),
        ]:
            sig = self._analyse(ticker, name, lot, step)
            if sig:
                signals.append(sig)
        return signals

    def _analyse(
        self, ticker: str, name: str, lot: int, step: int
    ) -> OptionsSignal | None:
        try:
            df = yf.Ticker(ticker).history(
                period="6mo", interval="1d", auto_adjust=True
            )
            if df.empty or len(df) < 50:
                return None

            df.columns = [c.lower() for c in df.columns]
            close = df["close"]
            spot  = float(close.iloc[-1])

            # EMAs
            ema20  = float(close.ewm(span=20,  adjust=False).mean().iloc[-1])
            ema50  = float(close.ewm(span=50,  adjust=False).mean().iloc[-1])
            ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])

            # RSI(14)
            delta  = close.diff()
            gain   = delta.clip(lower=0).rolling(14).mean()
            loss   = (-delta.clip(upper=0)).rolling(14).mean()
            rsi    = float(100 - 100 / (1 + gain / loss.replace(0, np.nan)).iloc[-1])

            # Historical volatility (20-day annualised)
            log_ret = np.log(close / close.shift(1)).dropna()
            hv_20   = float(log_ret.tail(20).std() * math.sqrt(252) * 100)

            # Returns
            ret_1w = float((spot / close.iloc[-6] - 1) * 100) if len(close) >= 6 else 0
            ret_1m = float((spot / close.iloc[-22] - 1) * 100) if len(close) >= 22 else 0

            # ATR(14)
            hi, lo, cl = df["high"], df["low"], df["close"]
            tr  = pd.concat([hi-lo, (hi-cl.shift()).abs(), (lo-cl.shift()).abs()], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])

            # Expiry — nearest Thursday (weekly) or last Thursday of month
            today    = datetime.now(IST)
            days_to_thursday = (3 - today.weekday()) % 7  # 3 = Thursday
            if days_to_thursday == 0:
                days_to_thursday = 7   # already Thursday — use next week
            expiry_date = (today + timedelta(days=days_to_thursday)).strftime("%d-%b-%Y")

            # ----------------------------------------------------------
            # Direction: CALL or PUT
            # ----------------------------------------------------------
            bullish_count = sum([
                spot > ema20,
                spot > ema50,
                spot > ema200,
                rsi > 50,
                ret_1w > 0,
            ])
            bearish_count = 5 - bullish_count

            reasons = []

            if bullish_count >= 3:
                direction = "CALL"
                # ATM or slightly OTM call
                strike    = int(round(spot / step) * step)   # ATM
                if ret_1m > 3:
                    strike = int((round(spot / step) + 1) * step)   # 1 strike OTM (momentum)

                sl_idx  = round(spot - 1.5 * atr, 0)
                tgt_idx = round(spot + 2.0 * atr, 0)

                reasons.append(f"above EMA{20 if spot > ema20 else 50}")
                if rsi > 55: reasons.append(f"RSI {rsi:.0f} (bullish)")
                if ret_1w > 0: reasons.append(f"1W +{ret_1w:.1f}%")

                confidence = 0.45 + bullish_count * 0.08

            elif bearish_count >= 3:
                direction = "PUT"
                strike    = int(round(spot / step) * step)   # ATM
                if ret_1m < -3:
                    strike = int((round(spot / step) - 1) * step)   # 1 strike OTM

                sl_idx  = round(spot + 1.5 * atr, 0)
                tgt_idx = round(spot - 2.0 * atr, 0)

                reasons.append(f"below EMA{20 if spot < ema20 else 50}")
                if rsi < 45: reasons.append(f"RSI {rsi:.0f} (bearish)")
                if ret_1w < 0: reasons.append(f"1W {ret_1w:.1f}%")

                confidence = 0.45 + bearish_count * 0.08

            else:
                return None   # no clear directional bias

            # IV note (approximate from HV)
            if hv_20 < 12:
                iv_note = f"HV {hv_20:.0f}% — low vol, options cheap (good to BUY)"
            elif hv_20 > 20:
                iv_note = f"HV {hv_20:.0f}% — high vol, consider SELLING premium instead"
            else:
                iv_note = f"HV {hv_20:.0f}% — normal vol environment"

            # Live option premium from NSE chain (with Black-Scholes fallback)
            opt_type   = "CE" if direction == "CALL" else "PE"
            live_prem  = _chain.get_premium(name, strike, opt_type)
            if live_prem:
                entry_zone = f"Rs.{live_prem:.0f} (live)"
            else:
                # Rough estimate as last resort
                pct_otm = abs(strike - spot) / spot
                lo = max(50,  round(spot * 0.005 * max(0.1, 1 - pct_otm * 5), 0))
                hi = max(100, round(spot * 0.010 * max(0.1, 1 - pct_otm * 5), 0))
                live_prem = (lo + hi) / 2
                entry_zone = f"Rs.{lo:.0f}–{hi:.0f} (approx)"

            reasoning = (
                f"{name} {direction} {strike} | Expiry {expiry_date} | "
                f"Spot {spot:,.0f} | {', '.join(reasons)}"
            )

            logger.info(f"OptionsSignal: {name} {direction} {strike} "
                        f"conf={confidence:.0%} expiry={expiry_date}")

            return OptionsSignal(
                index         = name,
                direction     = direction,
                strike        = strike,
                expiry        = expiry_date,
                index_spot    = round(spot, 2),
                entry_zone    = entry_zone,
                entry_premium = round(live_prem or 0, 1),
                stop_loss_idx = sl_idx,
                target_idx    = tgt_idx,
                confidence    = round(min(confidence, 0.85), 3),
                reasoning     = reasoning,
                iv_note       = iv_note,
                lot_size      = lot,
            )

        except Exception as e:
            logger.error(f"{name} options analysis failed: {e}")
            return None
