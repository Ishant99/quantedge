# =============================================================================
# analysis/market_breadth.py — NSE Market Breadth (Advance/Decline Ratio)
#
# Measures how broadly the market is moving — not just the index.
# A rising Nifty on poor breadth is a warning sign (narrow rally).
# Strong breadth confirms a healthy bull move.
#
# Returns:
#   A/D ratio:  advances / declines (>1.5 = broad up, <0.67 = broad down)
#   breadth_signal: strong_bull | bull | neutral | bear | strong_bear
#   position_size_mult: 1.1 (strong bull) / 1.0 (neutral) / 0.7 (bear)
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import json
from dataclasses import dataclass
from utils import get_logger

logger = get_logger("MarketBreadth")

_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "market_breadth_cache.json"
)
_CACHE_TTL = 3600   # 1-hour cache

# Nifty 50 constituents (stable, updated manually when index rebalanced)
NIFTY50_TICKERS = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "BHARTIARTL.NS",
    "SBIN.NS", "INFY.NS", "LICI.NS", "ITC.NS", "HINDUNILVR.NS",
    "LT.NS", "BAJFINANCE.NS", "HCLTECH.NS", "MARUTI.NS", "SUNPHARMA.NS",
    "ADANIENT.NS", "KOTAKBANK.NS", "TITAN.NS", "ONGC.NS", "NTPC.NS",
    "ASIANPAINT.NS", "POWERGRID.NS", "ULTRACEMCO.NS", "WIPRO.NS", "AXISBANK.NS",
    "NESTLEIND.NS", "JSWSTEEL.NS", "M&M.NS", "TATAMOTORS.NS", "HDFCLIFE.NS",
    "COALINDIA.NS", "BAJAJ-AUTO.NS", "SBILIFE.NS", "TATACONSUM.NS", "DRREDDY.NS",
    "GRASIM.NS", "ADANIPORTS.NS", "DIVISLAB.NS", "TECHM.NS", "CIPLA.NS",
    "BPCL.NS", "EICHERMOT.NS", "HEROMOTOCO.NS", "BRITANNIA.NS", "INDUSINDBK.NS",
    "APOLLOHOSP.NS", "TRENT.NS", "BAJAJFINSV.NS", "SHRIRAMFIN.NS", "BEL.NS",
]


@dataclass
class BreadthResult:
    advances:           int
    declines:           int
    unchanged:          int
    ad_ratio:           float        # advances / max(declines, 1)
    breadth_signal:     str          # strong_bull | bull | neutral | bear | strong_bear
    position_size_mult: float        # multiply position sizes by this
    nifty_breadth_pct:  float        # % of Nifty50 stocks advancing
    message:            str


class MarketBreadthAnalyser:
    """
    Computes Nifty 50 advance/decline ratio as a market health indicator.
    Integrates into MarketRegimeFilter to refine regime classification.
    """

    def get_breadth(self) -> BreadthResult:
        """Compute advance/decline from last 2 days of Nifty 50 constituent prices."""
        # Check cache first
        cached = self._load_cache()
        if cached:
            return cached

        try:
            import yfinance as yf
            hist = yf.download(
                NIFTY50_TICKERS,
                period="3d",
                interval="1d",
                auto_adjust=True,
                progress=False,
            )
            closes = hist["Close"] if "Close" in hist.columns else hist.xs("Close", axis=1, level=0)

            if closes.empty or len(closes) < 2:
                return self._neutral("Insufficient data")

            prev  = closes.iloc[-2]
            today = closes.iloc[-1]
            changes = today - prev

            advances  = int((changes >  0).sum())
            declines  = int((changes <  0).sum())
            unchanged = int((changes == 0).sum())
            total     = advances + declines + unchanged

            ad_ratio          = round(advances / max(declines, 1), 2)
            nifty_breadth_pct = round(advances / max(total, 1) * 100, 1)

            if ad_ratio >= 3.0:
                signal = "strong_bull"
                ps_mult = 1.15
                msg = (f"Breadth very strong: {advances}/{total} stocks up "
                       f"(A/D {ad_ratio:.1f}). Broad rally — full position sizes.")
            elif ad_ratio >= 1.5:
                signal = "bull"
                ps_mult = 1.05
                msg = (f"Breadth healthy: {advances}/{total} stocks up "
                       f"(A/D {ad_ratio:.1f}). Normal bull conditions.")
            elif ad_ratio >= 0.8:
                signal = "neutral"
                ps_mult = 1.0
                msg = (f"Breadth neutral: {advances} up, {declines} down "
                       f"(A/D {ad_ratio:.1f}). Mixed market.")
            elif ad_ratio >= 0.4:
                signal = "bear"
                ps_mult = 0.8
                msg = (f"Breadth weak: {declines}/{total} stocks falling "
                       f"(A/D {ad_ratio:.1f}). Reduce position sizes.")
            else:
                signal = "strong_bear"
                ps_mult = 0.6
                msg = (f"Breadth very weak: only {advances}/{total} stocks advancing "
                       f"(A/D {ad_ratio:.1f}). Broad selloff — cut sizes significantly.")

            result = BreadthResult(
                advances           = advances,
                declines           = declines,
                unchanged          = unchanged,
                ad_ratio           = ad_ratio,
                breadth_signal     = signal,
                position_size_mult = ps_mult,
                nifty_breadth_pct  = nifty_breadth_pct,
                message            = msg,
            )
            logger.info(f"[BREADTH] {msg}")
            self._save_cache(result)
            return result

        except Exception as e:
            logger.warning(f"Market breadth failed: {e}")
            return self._neutral(f"Error: {e}")

    def _neutral(self, reason: str) -> BreadthResult:
        return BreadthResult(
            advances=0, declines=0, unchanged=0,
            ad_ratio=1.0, breadth_signal="neutral",
            position_size_mult=1.0, nifty_breadth_pct=50.0,
            message=f"Breadth unavailable — {reason}. Proceeding normally."
        )

    def _load_cache(self) -> BreadthResult | None:
        try:
            with open(_CACHE_FILE) as f:
                obj = json.load(f)
            if time.time() - obj.get("ts", 0) > _CACHE_TTL:
                return None
            d = obj["data"]
            return BreadthResult(**d)
        except Exception:
            return None

    def _save_cache(self, result: BreadthResult):
        try:
            os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
            import dataclasses
            with open(_CACHE_FILE, "w") as f:
                json.dump({"ts": time.time(), "data": dataclasses.asdict(result)}, f)
        except Exception:
            pass
