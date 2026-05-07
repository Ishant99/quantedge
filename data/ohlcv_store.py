# =============================================================================
# data/ohlcv_store.py — Local OHLCV SQLite accumulator
#
# Saves daily candles for all watched symbols to market_data.db.
# This is the foundation for Sprint 3 dashboard charts, screener, and
# backtester — replacing live yfinance calls with stored history.
#
# Called daily by scheduler after market close.
# Schema: (symbol, date, open, high, low, close, volume, timeframe)
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from utils import get_logger

logger = get_logger("OHLCVStore")

MARKET_DATA_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "market_data.db"
)


class OHLCVStore:
    """
    Local accumulator for OHLCV candles.
    Reads from DB for TA — avoids hammering yfinance on every scan.
    """

    def __init__(self, db_path: str = MARKET_DATA_DB):
        self.db = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_table()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_symbol(self, symbol: str, df: pd.DataFrame,
                      timeframe: str = "1d") -> int:
        """
        Upsert OHLCV rows for a symbol. Returns number of rows inserted/updated.
        df must have columns: open, high, low, close, volume (lowercase).
        Index must be DatetimeIndex or date-string index.
        """
        if df is None or df.empty:
            return 0

        rows = []
        for idx, row in df.iterrows():
            try:
                date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
                rows.append((
                    symbol, date_str,
                    round(float(row.get("open",   row.get("Open",   0))), 4),
                    round(float(row.get("high",   row.get("High",   0))), 4),
                    round(float(row.get("low",    row.get("Low",    0))), 4),
                    round(float(row.get("close",  row.get("Close",  0))), 4),
                    int(  row.get("volume", row.get("Volume", 0))),
                    timeframe,
                ))
            except Exception:
                continue

        if not rows:
            return 0

        with sqlite3.connect(self.db) as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO ohlcv
                (symbol, date, open, high, low, close, volume, timeframe)
                VALUES (?,?,?,?,?,?,?,?)
            """, rows)

        return len(rows)

    def get_symbol(self, symbol: str, days: int = 400,
                   timeframe: str = "1d") -> pd.DataFrame:
        """
        Return stored OHLCV as DataFrame, newest rows last.
        Returns empty DataFrame if no data.
        """
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            with sqlite3.connect(self.db) as conn:
                df = pd.read_sql_query("""
                    SELECT date, open, high, low, close, volume
                    FROM ohlcv
                    WHERE symbol=? AND timeframe=? AND date >= ?
                    ORDER BY date ASC
                """, conn, params=(symbol, timeframe, cutoff))
            if df.empty:
                return pd.DataFrame()
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
            return df
        except Exception as e:
            logger.debug(f"get_symbol {symbol}: {e}")
            return pd.DataFrame()

    def update_all(self, symbols: list[str], timeframe: str = "1d",
                   lookback_days: int = 400) -> dict:
        """
        Fetch and store OHLCV for all symbols via yfinance.
        Returns {symbol: rows_written}.
        """
        period = f"{min(lookback_days, 700)}d"
        results = {}
        for sym in symbols:
            try:
                ticker_sym = sym if sym.endswith(".NS") else f"{sym}.NS"
                hist = yf.Ticker(ticker_sym).history(period=period, interval="1d")
                if hist.empty:
                    continue
                hist.columns = [c.lower() for c in hist.columns]
                n = self.update_symbol(sym, hist, timeframe)
                results[sym] = n
            except Exception as e:
                logger.debug(f"update_all {sym}: {e}")
                results[sym] = 0

        total = sum(results.values())
        ok    = sum(1 for v in results.values() if v > 0)
        logger.info(f"OHLCV store updated: {ok}/{len(symbols)} symbols, {total} rows")
        return results

    def get_latest_date(self, symbol: str, timeframe: str = "1d") -> str | None:
        """Return the most recent date stored for this symbol."""
        try:
            with sqlite3.connect(self.db) as conn:
                row = conn.execute(
                    "SELECT MAX(date) FROM ohlcv WHERE symbol=? AND timeframe=?",
                    (symbol, timeframe)
                ).fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def list_symbols(self, timeframe: str = "1d") -> list[str]:
        """Return all symbols that have stored data."""
        try:
            with sqlite3.connect(self.db) as conn:
                rows = conn.execute(
                    "SELECT DISTINCT symbol FROM ohlcv WHERE timeframe=? ORDER BY symbol",
                    (timeframe,)
                ).fetchall()
            return [r[0] for r in rows]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_table(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ohlcv (
                    symbol    TEXT    NOT NULL,
                    date      TEXT    NOT NULL,
                    open      REAL,
                    high      REAL,
                    low       REAL,
                    close     REAL,
                    volume    INTEGER,
                    timeframe TEXT    NOT NULL DEFAULT '1d',
                    PRIMARY KEY (symbol, date, timeframe)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_sym_date ON ohlcv (symbol, date)")


if __name__ == "__main__":
    # Quick test: store Nifty 50 index
    store = OHLCVStore()
    result = store.update_all(["RELIANCE", "HDFCBANK", "INFY"], lookback_days=30)
    print("Stored:", result)
    df = store.get_symbol("RELIANCE", days=30)
    print(f"RELIANCE: {len(df)} rows, latest={df.index[-1].date() if not df.empty else 'none'}")
