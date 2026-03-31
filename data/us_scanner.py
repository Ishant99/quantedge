# =============================================================================
# data/us_scanner.py — US Stocks scanner using yfinance
#
# Scans S&P 500 top 100 stocks + NASDAQ top 50.
# Returns OHLCV DataFrames in same format as market_scanner.py
# so the existing TA engine works unchanged.
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils import get_logger

logger = get_logger("USScanner")

# Top 80 liquid US stocks (S&P 500 + NASDAQ leaders)
US_SYMBOLS = [
    # Tech
    "AAPL","MSFT","NVDA","GOOGL","META","AMZN","TSLA","AMD","INTC","ORCL",
    "CRM","ADBE","QCOM","TXN","AVGO","MU","AMAT","LRCX","KLAC","SNPS",
    # Finance
    "JPM","BAC","GS","MS","WFC","BRK-B","V","MA","AXP","BLK",
    # Healthcare
    "UNH","JNJ","PFE","ABBV","MRK","TMO","ABT","DHR","BMY","AMGN",
    # Consumer
    "HD","MCD","SBUX","NKE","TGT","WMT","COST","PG","KO","PEP",
    # Energy / Industrial
    "XOM","CVX","COP","SLB","BA","CAT","HON","MMM","GE","RTX",
    # ETFs (market proxies)
    "SPY","QQQ","IWM","DIA","XLF","XLK","XLE","XLV","XLI","GLD",
]


class USScanner:
    """
    Fetches daily OHLCV from Yahoo Finance for US stocks.
    Returns dict: {symbol: pd.DataFrame} — same schema as MarketScanner.
    """

    def run(self, symbols: list[str] = None, lookback_days: int = 300,
            max_workers: int = 15) -> dict[str, pd.DataFrame]:
        symbols = symbols or US_SYMBOLS
        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(self._fetch, s, lookback_days): s for s in symbols}
            for fut in as_completed(futures):
                symbol = futures[fut]
                try:
                    df = fut.result()
                    if df is not None and len(df) >= 50:
                        results[symbol] = df
                except Exception as e:
                    logger.debug(f"{symbol} fetch error: {e}")
        logger.info(f"USScanner: {len(results)}/{len(symbols)} symbols loaded")
        return results

    def _fetch(self, symbol: str, lookback_days: int) -> pd.DataFrame | None:
        try:
            period = f"{lookback_days}d"
            df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
            if df.empty:
                return None
            df.columns = [c.lower() for c in df.columns]
            # Strip timezone for consistency with NSE data
            if hasattr(df.index, "tzinfo") and df.index.tzinfo is not None:
                df.index = df.index.tz_localize(None)
            return df[["open","high","low","close","volume"]]
        except Exception as e:
            logger.debug(f"{symbol} error: {e}")
            return None

    def get_current_price(self, symbol: str) -> float | None:
        try:
            hist = yf.Ticker(symbol).history(period="2d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass
        return None
