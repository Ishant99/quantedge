# =============================================================================
# data/nse_options_chain.py — Fetch live NSE option chain premiums
# Falls back to Black-Scholes approximation when NSE API is unavailable.
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests, time, math
from datetime import datetime
from utils import get_logger
from config import FNO_CHAIN_CACHE_MIN, FUTURES_RISK_FREE_RATE

logger = get_logger("NSEOptionsChain")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
    "Accept-Language": "en-US,en;q=0.9",
}
_CACHE = {}   # {index: (timestamp, data)}
CACHE_TTL = FNO_CHAIN_CACHE_MIN * 60   # seconds

# Index tickers for Black-Scholes fallback
_YF_TICKERS = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}


class NSEOptionsChain:
    """Fetch live option chain from NSE. Returns LTP for any strike/type.
    Falls back to Black-Scholes approximation if NSE API is unreachable."""

    def get_premium(self, index: str, strike: int, option_type: str) -> float | None:
        """
        Fetch last traded price for a specific strike.
        index: "NIFTY" or "BANKNIFTY"
        option_type: "CE" or "PE"
        Returns premium in Rs, or None if all sources fail.
        """
        # Try NSE live chain first
        data = self._get_chain(index)
        if data:
            for record in data:
                if record.get("strikePrice") == strike:
                    opt = record.get(option_type, {})
                    ltp = opt.get("lastPrice", 0)
                    if ltp and ltp > 0:
                        return float(ltp)

        # Fallback: Black-Scholes approximation
        logger.debug(f"NSE chain miss for {index} {strike}{option_type} — using BS fallback")
        return self._bs_fallback(index, strike, option_type)

    def get_atm_premium(self, index: str, spot: float, option_type: str,
                        step: int = 50) -> tuple[int, float] | tuple[None, None]:
        """Returns (ATM strike, premium) for given index."""
        atm = int(round(spot / step) * step)
        prem = self.get_premium(index, atm, option_type)
        if prem:
            return atm, prem
        return None, None

    def _get_chain(self, index: str) -> list:
        now = time.time()
        if index in _CACHE:
            ts, data = _CACHE[index]
            if now - ts < CACHE_TTL:
                return data

        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={index}"
        try:
            session = requests.Session()
            session.get("https://www.nseindia.com", headers=HEADERS, timeout=8)
            resp = session.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                data = resp.json().get("records", {}).get("data", [])
                _CACHE[index] = (now, data)
                logger.debug(f"NSE chain fetched: {index} — {len(data)} strikes")
                return data
        except Exception as e:
            logger.warning(f"NSE options chain fetch failed ({index}): {e}")
        return []

    def _bs_fallback(self, index: str, strike: int, option_type: str) -> float | None:
        """
        Black-Scholes approximation when NSE API fails.
        Uses yfinance for spot price and historical volatility.
        """
        try:
            import yfinance as yf
            import numpy as np

            ticker = _YF_TICKERS.get(index)
            if not ticker:
                return None

            hist = yf.Ticker(ticker).history(period="30d", interval="1d", auto_adjust=True)
            if hist.empty or len(hist) < 5:
                return None

            close = hist["Close"]
            spot  = float(close.iloc[-1])

            # Historical volatility (20-day annualised)
            log_ret = np.log(close / close.shift(1)).dropna()
            sigma   = float(log_ret.std() * np.sqrt(252))

            # Days to nearest Thursday expiry
            from config import FUTURES_DEFAULT_DTE
            T = FUTURES_DEFAULT_DTE / 365.0
            r = FUTURES_RISK_FREE_RATE

            S, K = spot, float(strike)
            if T <= 0 or sigma <= 0:
                return None

            d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
            d2 = d1 - sigma * math.sqrt(T)

            def _N(x):
                return (1.0 + math.erf(x / math.sqrt(2))) / 2.0

            if option_type == "CE":
                price = S * _N(d1) - K * math.exp(-r * T) * _N(d2)
            else:
                price = K * math.exp(-r * T) * _N(-d2) - S * _N(-d1)

            price = max(round(price, 1), 0.1)
            logger.debug(f"BS fallback: {index} {strike}{option_type} = Rs.{price}")
            return price
        except Exception as e:
            logger.warning(f"BS fallback failed ({index} {strike}{option_type}): {e}")
            return None
