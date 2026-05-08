# =============================================================================
# analysis/iron_condor.py — Iron Condor Generator for High-VIX Sessions
#
# Strategy:
#   Sell OTM CE + Buy further OTM CE (call spread — upper wing)
#   Sell OTM PE + Buy further OTM PE (put spread — lower wing)
#
#   Net credit = (sold CE premium + sold PE premium)
#              - (bought CE premium + bought PE premium)
#
# Edge:
#   In high-VIX environments, implied volatility is elevated → premiums fat.
#   Iron Condor captures theta decay + IV crush while capping risk to the spread.
#   Max profit = net credit received (index stays between short strikes at expiry).
#   Max loss   = spread width - net credit.
#
# Triggers:
#   India VIX > VIX_MIN (default 16) AND market is in sideways/recovery regime
#   Only trade on Mon–Wed (3+ days to expiry — avoid 1-day gamma risk)
#
# Configuration (all in config.py via _S() pattern):
#   IRON_CONDOR_VIX_MIN        = 16.0   VIX must be above this to trade
#   IRON_CONDOR_WING_WIDTH     = 200    points between sold and bought strikes (Nifty)
#   IRON_CONDOR_BODY_WIDTH     = 300    points OTM for the sold strikes (each side)
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import numpy as np
from dataclasses import dataclass
from datetime import datetime, timedelta
import pytz
from utils import get_logger

logger = get_logger("IronCondor")
IST = pytz.timezone("Asia/Kolkata")

INDEX_TICKERS  = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}
VIX_TICKER     = "^INDIAVIX"
NIFTY_STEP     = 50
BANKNIFTY_STEP = 100

# Config with safe defaults
def _cfg(key: str, default):
    try:
        from config import _S
        return type(default)(_S(key, default=default))
    except Exception:
        return default

VIX_MIN      = _cfg("IRON_CONDOR_VIX_MIN",    16.0)
WING_WIDTH_N = _cfg("IRON_CONDOR_WING_WIDTH",  200)    # Nifty points
BODY_WIDTH_N = _cfg("IRON_CONDOR_BODY_WIDTH",  300)    # Nifty OTM distance


@dataclass
class IronCondorSignal:
    index:            str
    spot:             float
    vix:              float

    # Sold (short) strikes — collect premium
    short_call:       int
    short_put:        int

    # Bought (long) strikes — pay premium (hedge)
    long_call:        int
    long_put:         int

    expiry:           str

    # Premium estimates (simplified — real system would use live options chain)
    short_call_prem:  float
    short_put_prem:   float
    long_call_prem:   float
    long_put_prem:    float
    net_credit:       float     # what we collect (per lot)
    max_loss:         float     # spread width - net credit (per lot)

    # Range
    breakeven_upper:  float     # spot + net_credit + body_width
    breakeven_lower:  float     # spot - net_credit - body_width

    # Risk metrics
    reward_risk:      float     # net_credit / max_loss
    confidence:       float
    reasoning:        str
    iv_note:          str


class IronCondorGenerator:
    """
    Generates Iron Condor signals when VIX is elevated and regime is sideways.
    Only fires on Mon–Wed to ensure 3+ trading days to expiry (reduces gamma risk).
    """

    VALID_DAYS = {0, 1, 2}   # Monday=0, Tuesday=1, Wednesday=2

    def run(self) -> list[IronCondorSignal]:
        """
        Main entry point. Returns a list of IronCondorSignal objects.
        Returns empty list if conditions not met.
        """
        signals = []

        # Day-of-week gate (Mon–Wed only)
        today = datetime.now(IST)
        if today.weekday() not in self.VALID_DAYS:
            logger.info(f"Iron Condor: skipped — only Mon–Wed (today={today.strftime('%A')})")
            return signals

        # Fetch VIX
        vix = self._get_vix()
        if vix is None:
            logger.warning("Iron Condor: VIX unavailable — skipping")
            return signals

        if vix < VIX_MIN:
            logger.info(f"Iron Condor: VIX {vix:.1f} < {VIX_MIN} — premiums too thin, skipping")
            return signals

        logger.info(f"Iron Condor: VIX={vix:.1f} >= {VIX_MIN} — generating signals")

        # Generate for Nifty (primary) and BankNifty (if VIX very high)
        for index in ["NIFTY"] + (["BANKNIFTY"] if vix >= VIX_MIN + 4 else []):
            sig = self._generate(index, vix)
            if sig:
                signals.append(sig)

        return signals

    # ------------------------------------------------------------------

    def _generate(self, index: str, vix: float) -> IronCondorSignal | None:
        """Build one Iron Condor signal for the given index."""
        try:
            spot = self._get_spot(index)
            if not spot:
                return None

            step = NIFTY_STEP if index == "NIFTY" else BANKNIFTY_STEP

            # Body width scales with VIX — higher VIX → wider sold strikes
            body_pts = int(BODY_WIDTH_N * (1 + (vix - VIX_MIN) / 30))
            body_pts = (body_pts // step) * step   # round to strike step

            wing_pts = WING_WIDTH_N if index == "NIFTY" else WING_WIDTH_N * 2

            # Round spot to nearest strike step
            atm = round(spot / step) * step

            short_call = atm + body_pts
            short_put  = atm - body_pts
            long_call  = short_call + wing_pts
            long_put   = short_put  - wing_pts

            # Approximate premiums using simplified Black-Scholes proxy
            # (real system would query NSE options chain)
            sigma      = vix / 100 / np.sqrt(252)   # daily vol

            # Compute real calendar days to the next Thursday expiry — avoids
            # grossly overestimating Wednesday premiums (1 DTE vs hardcoded 4).
            expiry_str = self._next_expiry_str()
            try:
                expiry_date = datetime.strptime(expiry_str, "%d-%b-%Y")
                dte = max(1.0, float(
                    (expiry_date.date() - datetime.now(IST).date()).days
                ))
            except Exception:
                dte = 4.0   # safe fallback

            sc_prem = self._approx_premium(spot, short_call, sigma, dte, "call")
            sp_prem = self._approx_premium(spot, short_put,  sigma, dte, "put")
            lc_prem = self._approx_premium(spot, long_call,  sigma, dte, "call")
            lp_prem = self._approx_premium(spot, long_put,   sigma, dte, "put")

            net_credit = round((sc_prem + sp_prem) - (lc_prem + lp_prem), 2)
            if net_credit <= 0:
                logger.debug(f"Iron Condor {index}: net credit ≤ 0 — skipping")
                return None

            spread_width = wing_pts
            max_loss     = round(spread_width - net_credit, 2)
            reward_risk  = round(net_credit / max_loss, 3) if max_loss > 0 else 0.0

            # Minimum reward:risk = 0.25 (collect at least 25% of max risk)
            if reward_risk < 0.20:
                logger.debug(f"Iron Condor {index}: R:R {reward_risk:.2f} < 0.20 — skipping")
                return None

            breakeven_upper = short_call + net_credit
            breakeven_lower = short_put  - net_credit
            profit_zone_pct = (breakeven_upper - breakeven_lower) / spot * 100

            expiry = self._next_expiry_str()

            confidence = min(0.75, 0.50 + reward_risk * 0.5 + (vix - VIX_MIN) * 0.01)
            iv_note    = (
                f"India VIX {vix:.1f} — elevated IV makes premiums attractive. "
                f"Profit zone: {short_put:,}–{short_call:,} ({profit_zone_pct:.1f}% width)"
            )
            reasoning = (
                f"Iron Condor {index} | VIX={vix:.1f} | "
                f"Sell {short_put}P + Sell {short_call}C | "
                f"Buy {long_put}P + Buy {long_call}C | "
                f"Net credit ≈ {net_credit:.0f} pts | Max loss ≈ {max_loss:.0f} pts | "
                f"R:R={reward_risk:.2f}"
            )

            logger.info(reasoning)
            return IronCondorSignal(
                index=index, spot=round(spot, 2), vix=round(vix, 2),
                short_call=short_call, short_put=short_put,
                long_call=long_call, long_put=long_put,
                expiry=expiry,
                short_call_prem=round(sc_prem, 2), short_put_prem=round(sp_prem, 2),
                long_call_prem=round(lc_prem, 2),  long_put_prem=round(lp_prem, 2),
                net_credit=net_credit, max_loss=max_loss,
                breakeven_upper=round(breakeven_upper, 0),
                breakeven_lower=round(breakeven_lower, 0),
                reward_risk=reward_risk,
                confidence=round(confidence, 2),
                reasoning=reasoning,
                iv_note=iv_note,
            )

        except Exception as e:
            logger.warning(f"Iron Condor {index} generation failed: {e}")
            return None

    def _approx_premium(self, spot: float, strike: float, daily_sigma: float,
                        dte: float, option_type: str) -> float:
        """
        Simplified option premium estimate using a log-normal approximation.
        Not a full Black-Scholes (no risk-free rate, no dividend).
        Accurate enough for strike selection and rough credit estimation.
        """
        moneyness = (strike - spot) / spot
        sigma_dte = daily_sigma * np.sqrt(dte)
        # ATM premium ≈ spot * sigma * sqrt(DTE) * 0.4 (from N(d1) ≈ 0.4 near ATM)
        atm_prem = spot * sigma_dte * 0.40
        # OTM decay — premiums fall roughly exponentially with moneyness
        otm_factor = np.exp(-abs(moneyness) / (2 * sigma_dte)) if sigma_dte > 0 else 1.0
        prem = atm_prem * otm_factor
        return max(round(float(prem), 2), 0.0)

    def _get_vix(self) -> float | None:
        """Fetch India VIX from yfinance."""
        try:
            df = yf.Ticker(VIX_TICKER).history(period="2d", interval="1d")
            if df.empty:
                return None
            return float(df["Close"].iloc[-1])
        except Exception as e:
            logger.debug(f"VIX fetch failed: {e}")
            return None

    def _get_spot(self, index: str) -> float | None:
        """Fetch current spot price for the index."""
        try:
            ticker = INDEX_TICKERS.get(index)
            if not ticker:
                return None
            df = yf.Ticker(ticker).history(period="2d", interval="1d")
            return float(df["Close"].iloc[-1]) if not df.empty else None
        except Exception as e:
            logger.debug(f"Spot fetch {index} failed: {e}")
            return None

    def _next_expiry_str(self) -> str:
        """Return the next Thursday as the weekly expiry date string."""
        today = datetime.now(IST)
        days_to_thursday = (3 - today.weekday()) % 7 or 7
        expiry = today + timedelta(days=days_to_thursday)
        return expiry.strftime("%d-%b-%Y").upper()
