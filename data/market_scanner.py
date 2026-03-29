# =============================================================================
# data/market_scanner.py — M1: Market Scanner
#
# Responsibilities:
#   - Load the NSE top-200 watchlist from CSV
#   - Fetch daily OHLCV + volume data via yfinance
#   - Enrich with basic metadata (sector, market cap)
#   - Filter out stocks with bad/missing data
#   - Return a clean DataFrame ready for M2 (Technical Analysis)
#
# Usage:
#   from data.market_scanner import MarketScanner
#   scanner = MarketScanner()
#   df = scanner.run()          # returns dict of {symbol: DataFrame}
# =============================================================================

import os
import time
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    NSE_TOP_200_FILE, MARKET_DATA_DIR,
    EXCHANGE, SMA_LONG
)
from utils import get_logger

logger = get_logger("MarketScanner")


class MarketScanner:
    """
    M1 — Fetches and prepares market data for the top 200 NSE stocks.

    Paper mode:  yfinance (free, 15-min delayed, enough for daily swing)
    Live mode:   Same yfinance for EOD data; Kite WebSocket used only
                 for real-time tick during market hours (handled in M7)
    """

    def __init__(self, lookback_days: int = 365):
        """
        Args:
            lookback_days: How many calendar days of history to fetch.
                           Default 365 = enough for SMA200 + indicators.
        """
        self.lookback_days = lookback_days
        self.symbols_df    = self._load_symbols()
        os.makedirs(MARKET_DATA_DIR, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, max_workers: int = 10) -> dict[str, pd.DataFrame]:
        """
        Main entry point. Fetches data for all symbols concurrently.

        Returns:
            Dict mapping symbol → cleaned OHLCV DataFrame.
            Symbols with bad data are silently dropped (logged as WARNING).
        """
        symbols = self.symbols_df["symbol"].tolist()
        logger.info(f"Starting market scan for {len(symbols)} symbols...")

        results: dict[str, pd.DataFrame] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_sym = {
                executor.submit(self._fetch_one, sym): sym
                for sym in symbols
            }
            for future in as_completed(future_to_sym):
                sym = future_to_sym[future]
                try:
                    df = future.result()
                    if df is not None and len(df) >= SMA_LONG:
                        results[sym] = df
                    else:
                        logger.warning(f"{sym}: insufficient data, skipped")
                except Exception as e:
                    logger.warning(f"{sym}: fetch error — {e}")

        logger.info(f"Scan complete. {len(results)}/{len(symbols)} stocks fetched successfully.")
        return results

    def get_symbol_info(self, symbol: str) -> dict:
        """Return sector and name metadata for a symbol."""
        row = self.symbols_df[self.symbols_df["symbol"] == symbol]
        if row.empty:
            return {"symbol": symbol, "name": symbol, "sector": "Unknown"}
        return row.iloc[0].to_dict()

    def get_all_symbols(self) -> list[str]:
        return self.symbols_df["symbol"].tolist()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_symbols(self) -> pd.DataFrame:
        """Load watchlist CSV. Exits loudly if file is missing."""
        if not os.path.exists(NSE_TOP_200_FILE):
            raise FileNotFoundError(
                f"Symbols file not found: {NSE_TOP_200_FILE}\n"
                f"Expected columns: symbol, name, sector"
            )
        df = pd.read_csv(NSE_TOP_200_FILE)
        required = {"symbol", "name", "sector"}
        if not required.issubset(df.columns):
            raise ValueError(f"CSV must have columns: {required}")
        logger.info(f"Loaded {len(df)} symbols from {NSE_TOP_200_FILE}")
        return df

    def _fetch_one(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV history for a single NSE symbol via yfinance.
        NSE symbols need '.NS' suffix for yfinance.
        """
        yf_symbol = f"{symbol}.NS"
        end_date   = datetime.today()
        start_date = end_date - timedelta(days=self.lookback_days)

        try:
            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(
                start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
                interval="1d",
                auto_adjust=True,
            )

            if df.empty:
                return None

            df = self._clean(df, symbol)
            self._cache(df, symbol)
            return df

        except Exception as e:
            logger.debug(f"{symbol}: {e}")
            return None

    def _clean(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """
        Standardise column names, drop nulls, add metadata columns.
        Output columns: open, high, low, close, volume, symbol, date
        """
        df = df.copy()
        df.index = pd.to_datetime(df.index)
        df.index.name = "date"

        # Standardise column names to lowercase
        df.columns = [c.lower() for c in df.columns]

        # Keep only OHLCV
        keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[keep]

        # Drop rows where close is null or zero
        df = df[df["close"].notna() & (df["close"] > 0)]

        # Add symbol column for easy groupby later
        df["symbol"] = symbol

        # Add daily return column
        df["daily_return"] = df["close"].pct_change()

        # Add avg volume (20-day) for volume breakout detection in M2
        df["vol_avg_20"] = df["volume"].rolling(20).mean()

        return df.sort_index()

    def _cache(self, df: pd.DataFrame, symbol: str) -> None:
        """Save to CSV so dashboard and backtester can read without refetching."""
        path = os.path.join(MARKET_DATA_DIR, f"{symbol}.csv")
        df.to_csv(path)

    # ------------------------------------------------------------------
    # Quick summary for logging / dashboard
    # ------------------------------------------------------------------

    def summary(self, data: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        Build a one-row-per-stock summary DataFrame.
        Columns: symbol, sector, last_close, daily_return_pct, volume, data_points
        """
        rows = []
        for sym, df in data.items():
            info = self.get_symbol_info(sym)
            latest = df.iloc[-1]
            rows.append({
                "symbol":            sym,
                "name":              info.get("name", sym),
                "sector":            info.get("sector", "Unknown"),
                "last_close":        round(latest["close"], 2),
                "daily_return_pct":  round(latest["daily_return"] * 100, 2),
                "volume":            int(latest["volume"]),
                "vol_avg_20":        int(latest.get("vol_avg_20", 0)),
                "data_points":       len(df),
                "last_updated":      df.index[-1].strftime("%Y-%m-%d"),
            })
        return pd.DataFrame(rows).sort_values("symbol")


# =============================================================================
# Standalone test — run: python -m data.market_scanner
# =============================================================================

if __name__ == "__main__":
    print("\n" + "="*60)
    print("  M1 — Market Scanner Test Run")
    print("="*60 + "\n")

    scanner = MarketScanner(lookback_days=400)

    # Test with a small subset first (comment out to run all 200)
    test_symbols = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "SBIN"]
    scanner.symbols_df = scanner.symbols_df[
        scanner.symbols_df["symbol"].isin(test_symbols)
    ]

    data = scanner.run(max_workers=5)

    summary = scanner.summary(data)
    print("\nScan Summary:")
    print(summary.to_string(index=False))

    if data:
        sample_sym = list(data.keys())[0]
        print(f"\nSample data for {sample_sym} (last 5 rows):")
        print(data[sample_sym].tail())
