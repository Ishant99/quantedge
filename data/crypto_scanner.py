# =============================================================================
# data/crypto_scanner.py — Crypto market scanner using Binance public API
#
# No API key required for market data (public endpoints).
# Returns OHLCV DataFrames in same format as market_scanner.py
# so the existing TA engine works unchanged.
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import pandas as pd
import numpy as np
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils import get_logger

logger = get_logger("CryptoScanner")

BINANCE_BASE = "https://api.binance.com/api/v3"

# Top 30 crypto pairs by volume (USDT quoted)
TOP_PAIRS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "LTCUSDT", "UNIUSDT", "ATOMUSDT", "ETCUSDT",
    "XLMUSDT", "ALGOUSDT", "VETUSDT", "FILUSDT", "AAVEUSDT",
    "NEARUSDT", "FTMUSDT", "SANDUSDT", "MANAUSDT", "SHIBUSDT",
    "TRXUSDT", "OPUSDT", "ARBUSDT", "APTUSDT", "INJUSDT",
]


class CryptoScanner:
    """
    Fetches daily OHLCV from Binance for top crypto pairs.
    Returns dict: {symbol: pd.DataFrame} — same schema as MarketScanner.
    """

    def run(self, pairs: list[str] = None, lookback_days: int = 300,
            max_workers: int = 10) -> dict[str, pd.DataFrame]:
        pairs = pairs or TOP_PAIRS
        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(self._fetch, p, lookback_days): p for p in pairs}
            for fut in as_completed(futures):
                symbol = futures[fut]
                try:
                    df = fut.result()
                    if df is not None and len(df) >= 50:
                        results[symbol] = df
                except Exception as e:
                    logger.debug(f"{symbol} fetch error: {e}")
        logger.info(f"CryptoScanner: {len(results)}/{len(pairs)} pairs loaded")
        return results

    def _fetch(self, symbol: str, lookback_days: int) -> pd.DataFrame | None:
        """Fetch daily klines from Binance public API."""
        try:
            limit = min(lookback_days, 1000)
            url   = f"{BINANCE_BASE}/klines"
            params = {"symbol": symbol, "interval": "1d", "limit": limit}
            resp  = requests.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                return None
            raw = resp.json()
            if not raw:
                return None

            df = pd.DataFrame(raw, columns=[
                "open_time","open","high","low","close","volume",
                "close_time","quote_vol","trades","taker_buy_base",
                "taker_buy_quote","ignore"
            ])
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
            df = df.set_index("open_time")
            for col in ["open","high","low","close","volume"]:
                df[col] = pd.to_numeric(df[col])
            return df[["open","high","low","close","volume"]]
        except Exception as e:
            logger.debug(f"{symbol} error: {e}")
            return None

    def get_current_price(self, symbol: str) -> float | None:
        """Fetch latest price for a symbol."""
        try:
            resp = requests.get(f"{BINANCE_BASE}/ticker/price",
                                params={"symbol": symbol}, timeout=5)
            if resp.status_code == 200:
                return float(resp.json()["price"])
        except Exception:
            pass
        return None
