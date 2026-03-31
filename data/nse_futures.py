# =============================================================================
# data/nse_futures.py — Fetch Nifty / BankNifty futures price
# Uses yfinance near-month continuous contract as proxy
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
from datetime import datetime, timedelta
from utils import get_logger

logger = get_logger("NSEFutures")

# yfinance tickers for NSE indices (spot — futures price ≈ spot + cost of carry)
INDEX_TICKERS = {
    "NIFTY":     "^NSEI",
    "BANKNIFTY": "^NSEBANK",
}
from config import FNO_LOT_SIZES as LOT_SIZES, FUTURES_RISK_FREE_RATE, FUTURES_DEFAULT_DTE
MARGIN_PCT = 0.10   # 10% margin (SPAN approx)


def get_futures_price(index: str) -> float | None:
    """
    Returns current futures price for index.
    Approximated as spot + (spot × risk_free × days_to_expiry/365).
    risk_free = 6.5% (RBI repo rate approx).
    """
    ticker = INDEX_TICKERS.get(index)
    if not ticker:
        return None
    try:
        hist = yf.Ticker(ticker).history(period="2d", interval="1d")
        if hist.empty:
            return None
        if hist.empty:
            return None
        spot = float(hist["Close"].iloc[-1])
        fut_price = round(spot * (1 + FUTURES_RISK_FREE_RATE * FUTURES_DEFAULT_DTE / 365), 2)
        return fut_price
    except Exception as e:
        logger.warning(f"Futures price fetch failed ({index}): {e}")
        return None


def get_margin_required(index: str, price: float, lots: int = 1) -> float:
    """Approx SPAN margin for futures position."""
    lot_size = LOT_SIZES.get(index, 75)
    notional = price * lot_size * lots
    return round(notional * MARGIN_PCT, 2)
