# =============================================================================
# analysis/options_selling.py — Options Selling Strategy
#
# Straddle: Sell ATM CE + ATM PE (same strike, same expiry)
# Strangle: Sell OTM CE + OTM PE (different strikes)
#
# Edge: Theta decay. Premium collected upfront decays to zero at expiry.
# Win condition: index stays within the breakeven range.
# Exit: Buy back when premium doubles (2x) — that's the SL.
# Target: Premium decays to 20% of entry (80% profit).
#
# Only trade on Tue/Wed/Thu of expiry week (max theta, minimum time risk).
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import pytz
from utils import get_logger

logger = get_logger("OptionsSelling")
IST = pytz.timezone("Asia/Kolkata")

INDEX_TICKERS = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}
from config import (FNO_LOT_SIZES as LOT_SIZES,
                    FNO_HV_STRADDLE, FNO_HV_STRANGLE, FNO_SELL_DAYS, IV_RANK_MIN)
NIFTY_STEP    = 50
BANKNIFTY_STEP= 100

_SELL_WEEKDAYS = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6
}


@dataclass
class SellingSignal:
    index:       str
    strategy:    str       # STRADDLE | STRANGLE
    ce_strike:   int
    pe_strike:   int
    expiry:      str
    spot:        float
    ce_premium:  float     # approx entry premium per lot
    pe_premium:  float
    total_premium: float   # ce + pe (what we collect)
    breakeven_upper: float
    breakeven_lower: float
    sl_premium:  float     # exit if either leg doubles
    tp_premium:  float     # target: 80% decay (keep 80% of premium)
    lot_size:    int
    confidence:  float
    reasoning:   str
    iv_note:     str


class OptionsSellingGenerator:
    """
    Generates straddle/strangle sell ideas for Nifty and BankNifty.
    Only active Tue/Wed/Thu of expiry week.
    """

    def run(self) -> list[SellingSignal]:
        today = datetime.now(IST)
        allowed = {_SELL_WEEKDAYS[d.strip().lower()]
                   for d in FNO_SELL_DAYS.split(",") if d.strip().lower() in _SELL_WEEKDAYS}
        if today.weekday() not in allowed:
            logger.info(f"Options selling: only runs on {FNO_SELL_DAYS} — skipping today")
            return []

        signals = []
        for index, ticker in INDEX_TICKERS.items():
            sig = self._analyse(index, ticker)
            if sig:
                signals.append(sig)
        return signals

    def _analyse(self, index: str, ticker: str) -> SellingSignal | None:
        try:
            df = yf.Ticker(ticker).history(period="3mo", interval="1d", auto_adjust=True)
            if df.empty or len(df) < 30:
                return None
            df.columns = [c.lower() for c in df.columns]
            close = df["close"]
            spot  = float(close.iloc[-1])
            step  = NIFTY_STEP if index == "NIFTY" else BANKNIFTY_STEP
            lot   = LOT_SIZES[index]

            # Historical volatility (20-day)
            log_ret = np.log(close / close.shift(1)).dropna()
            hv_20   = float(log_ret.tail(20).std() * np.sqrt(252) * 100)

            # IV Rank: what percentile is current HV vs. 1-year rolling HV?
            # Only sell when IV is elevated (rank > IV_RANK_MIN) — expensive premiums.
            hv_series = [
                float(log_ret.iloc[i:i + 20].std() * np.sqrt(252) * 100)
                for i in range(max(0, len(log_ret) - 252), len(log_ret) - 20)
                if len(log_ret.iloc[i:i + 20]) == 20
            ]
            if hv_series:
                iv_rank = sum(1 for h in hv_series if h < hv_20) / len(hv_series)
            else:
                iv_rank = 0.5  # not enough history — assume neutral

            if iv_rank < IV_RANK_MIN:
                logger.info(
                    f"{index}: IV rank {iv_rank:.0%} below {IV_RANK_MIN:.0%} — "
                    f"HV {hv_20:.1f}% not elevated enough, skip selling"
                )
                return None

            # Also reject if HV too low in absolute terms
            if hv_20 < FNO_HV_STRANGLE:
                logger.info(f"{index}: HV {hv_20:.1f}% too low — premiums cheap, skip selling")
                return None

            # ATM strike
            atm = int(round(spot / step) * step)
            # OTM strikes for strangle (1 step away)
            otm_ce = atm + step
            otm_pe = atm - step

            # Approx premium using simplified Black-Scholes proxy
            # Premium ~ spot × HV/100 × sqrt(DTE/252) × 0.4 (ATM delta ~0.4)
            today    = datetime.now(IST)
            dte      = self._days_to_expiry(today)
            if dte <= 0:
                return None

            # Try to get real ATM premium from NSE options chain
            atm_prem = None
            try:
                from data.nse_options_chain import NSEOptionsChain
                chain = NSEOptionsChain()
                atm_prem = chain.get_atm_premium(index, atm)
            except Exception:
                pass

            if not atm_prem or atm_prem <= 0:
                # Fallback: simplified Black-Scholes proxy
                time_factor = np.sqrt(dte / 252)
                atm_prem = round(spot * (hv_20 / 100) * time_factor * 0.4, 1)
            else:
                atm_prem = round(float(atm_prem), 1)
                logger.info(f"{index}: using live NSE ATM premium ₹{atm_prem}")

            time_factor = np.sqrt(dte / 252)
            otm_prem    = round(atm_prem * 0.6, 1)   # OTM ~60% of ATM

            # Expiry
            expiry = self._nearest_thursday(today)

            # Strategy selection
            # Straddle: high vol, expect reversion. Strangle: lower vol, wider range
            if hv_20 > FNO_HV_STRADDLE:
                strategy   = "STRADDLE"
                ce_strike  = atm
                pe_strike  = atm
                ce_prem    = atm_prem
                pe_prem    = atm_prem
            else:
                strategy   = "STRANGLE"
                ce_strike  = otm_ce
                pe_strike  = otm_pe
                ce_prem    = otm_prem
                pe_prem    = otm_prem

            total_prem       = round(ce_prem + pe_prem, 1)
            breakeven_upper  = round(ce_strike + total_prem, 0)
            breakeven_lower  = round(pe_strike - total_prem, 0)
            sl_premium       = round(total_prem * 2.0, 1)   # exit if premium doubles
            tp_premium       = round(total_prem * 0.20, 1)  # keep 80%

            confidence = 0.60 if hv_20 > 15 else 0.50
            if dte <= 2:
                confidence += 0.10   # last 2 days = max theta

            iv_note = (
                f"HV {hv_20:.1f}% | IV Rank {iv_rank:.0%} | DTE {dte}d | "
                f"Collect Rs.{total_prem:.0f}/lot ({lot} shares)"
            )
            reasoning = (
                f"{index} {strategy} | Sell {ce_strike}CE @ Rs.{ce_prem:.0f} + "
                f"{pe_strike}PE @ Rs.{pe_prem:.0f} | "
                f"BE: {breakeven_lower:.0f}–{breakeven_upper:.0f} | "
                f"SL if premium > Rs.{sl_premium:.0f} | Expiry {expiry}"
            )

            logger.info(f"Selling signal: {index} {strategy} conf={confidence:.0%}")

            return SellingSignal(
                index=index, strategy=strategy,
                ce_strike=ce_strike, pe_strike=pe_strike,
                expiry=expiry, spot=round(spot, 0),
                ce_premium=ce_prem, pe_premium=pe_prem,
                total_premium=total_prem,
                breakeven_upper=breakeven_upper,
                breakeven_lower=breakeven_lower,
                sl_premium=sl_premium, tp_premium=tp_premium,
                lot_size=lot, confidence=round(confidence, 2),
                reasoning=reasoning, iv_note=iv_note,
            )

        except Exception as e:
            logger.error(f"Options selling error ({index}): {e}")
            return None

    def _nearest_thursday(self, today: datetime) -> str:
        days = (3 - today.weekday()) % 7
        if days == 0:
            days = 7
        return (today + timedelta(days=days)).strftime("%d-%b-%Y")

    def _days_to_expiry(self, today: datetime) -> int:
        days = (3 - today.weekday()) % 7
        if days == 0:
            days = 7
        return days
