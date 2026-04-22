# =============================================================================
# data/market_scanner.py — M1: Market Scanner
#
# NSE 500 edition — batch yfinance fetching with smart caching,
# retry logic, and 24-hour stale detection.
#
# Usage:
#   from data.market_scanner import MarketScanner
#   scanner = MarketScanner()
#   data = scanner.run()   # dict of {symbol: DataFrame}
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
    NSE_500_FILE, NSE_TOP_200_FILE, NSE_WATCHLIST_ADDITIONS_FILE, MARKET_DATA_DIR,
    EXCHANGE, SMA_LONG,
    SCANNER_BATCH_SIZE, SCANNER_WORKERS,
    SCANNER_RETRY_MAX, SCANNER_RETRY_DELAY,
    CACHE_STALE_HOURS,
)
from utils import get_logger

logger = get_logger("MarketScanner")


class MarketScanner:
    """
    M1 — Fetches and prepares market data for NSE 500 stocks.

    Improvements over v1:
      - Loads from nse500_symbols.csv (500 stocks, 22 sectors)
      - Batch yfinance fetching (50 symbols per HTTP call, ~10x faster)
      - 24h smart cache — only re-fetches stale data
      - Retry logic with exponential backoff
      - Preserves index_membership + market_cap_rank for dashboard filters
    """

    def __init__(self, lookback_days: int = 400):
        self.lookback_days = lookback_days
        self.symbols_df    = self._load_symbols()
        os.makedirs(MARKET_DATA_DIR, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, max_workers: int = None,
            regime: str = "bull") -> dict[str, pd.DataFrame]:
        """
        Fetch data for all symbols using smart cache + batch fetching.

        Strategy:
          1. Check each symbol's cached CSV — if fresh (< CACHE_STALE_HOURS),
             load from disk.
          2. Batch-fetch all stale symbols via yf.download (50 per call).
          3. Retry any batch failures individually with exponential backoff.

        Returns:
            Dict mapping symbol → cleaned OHLCV DataFrame.
        """
        workers  = max_workers or SCANNER_WORKERS

        # B4: Regime-aware watchlist shrinking
        # In bear/sideways markets scan only the top 100 (Nifty 100) to reduce noise
        if regime in ("bear", "sideways") and "index_membership" in self.symbols_df.columns:
            valid_memberships = {"NIFTY50", "NIFTY100"}
            mask    = self.symbols_df["index_membership"].isin(valid_memberships)
            symbols = self.symbols_df[mask]["symbol"].dropna().unique().tolist()
            logger.info(f"[B4] {regime.upper()} market — scanning top {len(symbols)} (Nifty 100 only)")
        else:
            symbols = self.symbols_df["symbol"].dropna().unique().tolist()
        logger.info(f"Starting NSE scan — {len(symbols)} symbols...")

        results:  dict[str, pd.DataFrame] = {}
        to_fetch: list[str] = []

        # --- Phase 1: load from cache where fresh ---
        for sym in symbols:
            cached = self._load_from_cache(sym)
            if cached is not None:
                results[sym] = cached
            else:
                to_fetch.append(sym)

        cache_hits = len(results)
        logger.info(f"Cache hits: {cache_hits} | Fetching fresh: {len(to_fetch)}")

        if not to_fetch:
            logger.info("All data served from cache.")
            return results

        # --- Phase 2: batch-fetch stale symbols ---
        batches      = [to_fetch[i:i+SCANNER_BATCH_SIZE]
                        for i in range(0, len(to_fetch), SCANNER_BATCH_SIZE)]
        failed_syms: list[str] = []

        for idx, batch in enumerate(batches, 1):
            logger.info(f"Batch {idx}/{len(batches)}: fetching {len(batch)} symbols...")
            batch_results = self._fetch_batch(batch)
            for sym, df in batch_results.items():
                if df is not None and len(df) >= SMA_LONG:
                    results[sym] = df
                else:
                    failed_syms.append(sym)
            # Be polite to Yahoo Finance between batches
            if idx < len(batches):
                time.sleep(1.0)

        # --- Phase 3: retry individual failures ---
        if failed_syms:
            logger.info(f"Retrying {len(failed_syms)} failed symbols individually...")
            with ThreadPoolExecutor(max_workers=min(workers, 10)) as executor:
                futures = {
                    executor.submit(self._fetch_one_with_retry, sym): sym
                    for sym in failed_syms
                }
                for future in as_completed(futures):
                    sym = futures[future]
                    try:
                        df = future.result()
                        if df is not None and len(df) >= SMA_LONG:
                            results[sym] = df
                        else:
                            logger.warning(f"{sym}: insufficient data after retry, skipped")
                    except Exception as e:
                        logger.warning(f"{sym}: failed after all retries — {e}")

        logger.info(
            f"Scan complete. {len(results)}/{len(symbols)} stocks loaded "
            f"({cache_hits} from cache, {len(results)-cache_hits} freshly fetched)."
        )
        return results

    def get_symbol_info(self, symbol: str) -> dict:
        row = self.symbols_df[self.symbols_df["symbol"] == symbol]
        if row.empty:
            return {"symbol": symbol, "name": symbol, "sector": "Unknown",
                    "market_cap_rank": 999, "index_membership": "NIFTY500"}
        return row.iloc[0].to_dict()

    def get_all_symbols(self) -> list[str]:
        return self.symbols_df["symbol"].tolist()

    def get_symbols_by_index(self, index: str) -> list[str]:
        """Filter by index_membership: NIFTY50 | NIFTY100 | NIFTY200 | NIFTY500"""
        memberships = {
            "NIFTY50":  {"NIFTY50"},
            "NIFTY100": {"NIFTY50", "NIFTY100"},
            "NIFTY200": {"NIFTY50", "NIFTY100", "NIFTY200"},
            "NIFTY500": {"NIFTY50", "NIFTY100", "NIFTY200", "NIFTY500"},
        }
        valid = memberships.get(index, {"NIFTY50", "NIFTY100", "NIFTY200", "NIFTY500"})
        if "index_membership" not in self.symbols_df.columns:
            return self.get_all_symbols()
        mask = self.symbols_df["index_membership"].isin(valid)
        return self.symbols_df[mask]["symbol"].tolist()

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def _is_stale(self, symbol: str) -> bool:
        path = os.path.join(MARKET_DATA_DIR, f"{symbol}.csv")
        if not os.path.exists(path):
            return True
        age_hours = (time.time() - os.path.getmtime(path)) / 3600
        return age_hours > CACHE_STALE_HOURS

    def _load_from_cache(self, symbol: str) -> Optional[pd.DataFrame]:
        if self._is_stale(symbol):
            return None
        path = os.path.join(MARKET_DATA_DIR, f"{symbol}.csv")
        try:
            df = pd.read_csv(path, index_col="date", parse_dates=True)
            if len(df) >= SMA_LONG and "close" in df.columns:
                return df
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def _fetch_batch(self, symbols: list[str]) -> dict[str, Optional[pd.DataFrame]]:
        """
        Fetch multiple symbols in one yf.download call.
        Significantly faster than individual Ticker.history() calls.
        """
        end_date   = datetime.today()
        start_date = end_date - timedelta(days=self.lookback_days)
        tickers    = [f"{s}.NS" for s in symbols]

        results: dict[str, Optional[pd.DataFrame]] = {s: None for s in symbols}

        try:
            raw = yf.download(
                tickers    = tickers,
                start      = start_date.strftime("%Y-%m-%d"),
                end        = end_date.strftime("%Y-%m-%d"),
                interval   = "1d",
                auto_adjust= True,
                group_by   = "ticker",
                threads    = True,
                progress   = False,
            )

            if raw.empty:
                return results

            # When multiple tickers downloaded, columns are MultiIndex (field, ticker)
            for sym, yf_sym in zip(symbols, tickers):
                try:
                    if len(tickers) == 1:
                        df_sym = raw.copy()
                    else:
                        # MultiIndex: (Price, Ticker) or (Ticker, Price)
                        if yf_sym in raw.columns.get_level_values(0):
                            df_sym = raw[yf_sym]
                        elif yf_sym in raw.columns.get_level_values(1):
                            df_sym = raw.xs(yf_sym, axis=1, level=1)
                        else:
                            continue

                    if df_sym.empty or df_sym.get("Close", pd.Series()).isna().all():
                        continue

                    df_clean = self._clean(df_sym, sym)
                    self._cache(df_clean, sym)
                    results[sym] = df_clean

                except Exception as e:
                    logger.debug(f"{sym}: batch parse error — {e}")

        except Exception as e:
            logger.warning(f"Batch fetch error: {e}")

        return results

    def _fetch_one_with_retry(self, symbol: str) -> Optional[pd.DataFrame]:
        """Individual fetch with exponential backoff retry."""
        for attempt in range(SCANNER_RETRY_MAX):
            try:
                df = self._fetch_one(symbol)
                if df is not None:
                    return df
            except Exception as e:
                logger.debug(f"{symbol}: attempt {attempt+1} failed — {e}")
            if attempt < SCANNER_RETRY_MAX - 1:
                sleep_time = SCANNER_RETRY_DELAY * (2 ** attempt)
                time.sleep(sleep_time)
        return None

    def _fetch_one(self, symbol: str) -> Optional[pd.DataFrame]:
        yf_symbol  = f"{symbol}.NS"
        end_date   = datetime.today()
        start_date = end_date - timedelta(days=self.lookback_days)
        try:
            df = yf.Ticker(yf_symbol).history(
                start       = start_date.strftime("%Y-%m-%d"),
                end         = end_date.strftime("%Y-%m-%d"),
                interval    = "1d",
                auto_adjust = True,
            )
            if df.empty:
                return None
            df = self._clean(df, symbol)
            self._cache(df, symbol)
            return df
        except Exception as e:
            logger.debug(f"{symbol}: {e}")
            return None

    # ------------------------------------------------------------------
    # Data cleaning
    # ------------------------------------------------------------------

    def _clean(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        df.index = pd.to_datetime(df.index)
        df.index.name = "date"

        # Flatten MultiIndex columns (new yfinance returns (Price, Ticker) tuples)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]

        df.columns = [c.lower() if isinstance(c, str) else str(c).lower()
                      for c in df.columns]

        keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[keep]

        # Drop rows with missing or zero close price
        df = df[df["close"].notna() & (df["close"] > 0)]

        # Volume validation: drop zero-volume bars — they indicate halts or
        # bad data and would corrupt RSI/MACD calculations.
        if "volume" in df.columns:
            zero_vol = (df["volume"] == 0) | df["volume"].isna()
            if zero_vol.any():
                logger.debug(f"{symbol}: dropping {zero_vol.sum()} zero-volume bars")
            df = df[~zero_vol]

        # Data gap detection: flag and drop bars where close jumps >15% in one
        # day — almost always a corporate action not reflected in yfinance or
        # stale/bad data rather than a genuine price move.
        if len(df) > 1:
            pct_chg = df["close"].pct_change().abs()
            bad_bars = pct_chg > 0.15
            if bad_bars.any():
                logger.warning(
                    f"{symbol}: {bad_bars.sum()} bar(s) with >15% single-day move "
                    f"removed (possible split/bad data): "
                    f"{df.index[bad_bars].strftime('%Y-%m-%d').tolist()}"
                )
                df = df[~bad_bars]

        df["symbol"]       = symbol
        df["daily_return"] = df["close"].pct_change()
        df["vol_avg_20"]   = df["volume"].rolling(20).mean() if "volume" in df.columns else 0.0

        return df.sort_index()

    def _cache(self, df: pd.DataFrame, symbol: str) -> None:
        path = os.path.join(MARKET_DATA_DIR, f"{symbol}.csv")
        try:
            df.to_csv(path, index=True)   # index=True preserves "date" column for reload
        except Exception as e:
            logger.debug(f"{symbol}: cache write failed — {e}")

    # ------------------------------------------------------------------
    # Symbol loading
    # ------------------------------------------------------------------

    def _load_symbols(self) -> pd.DataFrame:
        # Prefer NSE 500 file; fall back to legacy 200 file
        symbol_file = NSE_500_FILE if os.path.exists(NSE_500_FILE) else NSE_TOP_200_FILE
        if not os.path.exists(symbol_file):
            raise FileNotFoundError(f"Symbol file not found: {symbol_file}")

        df = pd.read_csv(symbol_file)
        required = {"symbol", "name", "sector"}
        if not required.issubset(df.columns):
            raise ValueError(f"CSV must have columns: {required}")

        if os.path.exists(NSE_WATCHLIST_ADDITIONS_FILE):
            extra = pd.read_csv(NSE_WATCHLIST_ADDITIONS_FILE)
            if required.issubset(extra.columns):
                for col in df.columns:
                    if col not in extra.columns:
                        extra[col] = "NIFTY500" if col == "index_membership" else (
                            999 if col == "market_cap_rank" else ""
                        )
                extra = extra[df.columns]
                df = pd.concat([df, extra], ignore_index=True)
                logger.info(f"Merged {len(extra)} supplemental watchlist symbols from {NSE_WATCHLIST_ADDITIONS_FILE}")
            else:
                logger.warning(f"Supplemental watchlist missing required columns: {NSE_WATCHLIST_ADDITIONS_FILE}")

        # Drop duplicates and empty symbols
        df = df.dropna(subset=["symbol"])
        df = df.drop_duplicates(subset=["symbol"])
        df["symbol"] = df["symbol"].str.strip()

        logger.info(f"Loaded {len(df)} symbols from {symbol_file}")
        return df

    # ------------------------------------------------------------------
    # Summary (for logging / dashboard)
    # ------------------------------------------------------------------

    def summary(self, data: dict[str, pd.DataFrame]) -> pd.DataFrame:
        rows = []
        for sym, df in data.items():
            info   = self.get_symbol_info(sym)
            latest = df.iloc[-1]
            rows.append({
                "symbol":           sym,
                "name":             info.get("name", sym),
                "sector":           info.get("sector", "Unknown"),
                "market_cap_rank":  info.get("market_cap_rank", 999),
                "index_membership": info.get("index_membership", "NIFTY500"),
                "last_close":       round(latest["close"], 2),
                "daily_return_pct": round(latest.get("daily_return", 0) * 100, 2),
                "volume":           int(latest.get("volume", 0)),
                "vol_avg_20":       int(latest.get("vol_avg_20", 0) or 0),
                "data_points":      len(df),
                "last_updated":     df.index[-1].strftime("%Y-%m-%d"),
            })
        return pd.DataFrame(rows).sort_values("market_cap_rank")


# =============================================================================
# Standalone test
# =============================================================================

if __name__ == "__main__":
    print("\n" + "="*60)
    print("  M1 — Market Scanner (NSE 500 Edition)")
    print("="*60 + "\n")

    scanner = MarketScanner(lookback_days=400)
    print(f"Total symbols loaded: {len(scanner.symbols_df)}")
    print(f"Nifty 50 symbols: {len(scanner.get_symbols_by_index('NIFTY50'))}")

    # Quick test on 10 symbols
    scanner.symbols_df = scanner.symbols_df.head(10)
    data    = scanner.run()
    summary = scanner.summary(data)
    print("\nScan Summary:")
    print(summary[["symbol","sector","last_close","daily_return_pct"]].to_string(index=False))
