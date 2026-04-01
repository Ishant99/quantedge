# =============================================================================
# memory/portfolio_memory.py — M6: Portfolio Memory
#
# Stores every trade signal and execution in SQLite.
# Tracks win rate, drawdown, profit factor across all sessions.
# ChromaDB used for semantic search of past trade reasoning.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import json
from datetime import datetime
from dataclasses import asdict
from typing import Optional
from config import SQLITE_DB_FILE, CHROMA_PERSIST_DIR, CHROMA_COLLECTION
from utils import get_logger

logger = get_logger("PortfolioMemory")


class PortfolioMemory:
    """
    M6 — Persistent trade memory using SQLite.

    Tables:
      signals  — every signal generated (BUY/SELL/HOLD)
      trades   — executed trades with entry/exit details
      metrics  — daily portfolio snapshots
    """

    def __init__(self):
        os.makedirs(os.path.dirname(SQLITE_DB_FILE) or "logs", exist_ok=True)
        self.db_path = SQLITE_DB_FILE
        self.db_available = True
        try:
            self._init_db()
        except sqlite3.Error as e:
            self.db_available = False
            logger.warning(f"SQLite init failed: {e}")
        self.chroma = self._init_chroma()
        logger.info(f"Portfolio memory ready — db: {self.db_path}")

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS signals (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp     TEXT NOT NULL,
                    symbol        TEXT NOT NULL,
                    action        TEXT NOT NULL,
                    confidence    REAL,
                    entry_price   REAL,
                    stop_loss     REAL,
                    take_profit   REAL,
                    position_size INTEGER,
                    ta_score      REAL,
                    sentiment     TEXT,
                    reasoning     TEXT,
                    executed      INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS trades (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id     INTEGER,
                    symbol        TEXT NOT NULL,
                    action        TEXT NOT NULL,
                    qty           INTEGER,
                    entry_price   REAL,
                    exit_price    REAL,
                    entry_time    TEXT,
                    exit_time     TEXT,
                    pnl           REAL,
                    pnl_pct       REAL,
                    status        TEXT,
                    mode          TEXT DEFAULT 'paper',
                    FOREIGN KEY (signal_id) REFERENCES signals(id)
                );

                CREATE TABLE IF NOT EXISTS snapshots (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT NOT NULL,
                    portfolio_value REAL,
                    cash            REAL,
                    pnl             REAL,
                    pnl_pct         REAL,
                    open_positions  INTEGER,
                    total_trades    INTEGER,
                    win_rate        REAL
                );
            """)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def save_signal(self, signal) -> int:
        """Save a TradeSignal. Returns row id, or -1 if duplicate (same symbol+action today)."""
        if not self.db_available:
            return -1
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            with self._conn() as conn:
                # Dedup: skip if same symbol + action already saved today
                existing = conn.execute("""
                    SELECT id FROM signals
                    WHERE symbol=? AND action=? AND DATE(timestamp)=?
                    LIMIT 1
                """, (signal.symbol, signal.action, today)).fetchone()
                if existing:
                    logger.debug(f"Dedup: {signal.symbol} {signal.action} already saved today — skipped")
                    return existing[0]

                cur = conn.execute("""
                    INSERT INTO signals
                    (timestamp, symbol, action, confidence, entry_price,
                     stop_loss, take_profit, position_size, ta_score,
                     sentiment, reasoning, executed)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,0)
                """, (
                    datetime.now().isoformat(),
                    signal.symbol, signal.action, signal.confidence,
                    signal.entry_price, signal.stop_loss, signal.take_profit,
                    signal.position_size, signal.ta_score,
                    signal.sentiment, signal.reasoning,
                ))
                signal_id = cur.lastrowid
        except sqlite3.Error as e:
            logger.warning(f"save_signal failed ({signal.symbol}): {e}")
            self.db_available = False
            return -1

        # Store reasoning in ChromaDB for semantic search
        if self.chroma:
            try:
                self.chroma.add(
                    documents=[signal.reasoning],
                    ids=[f"signal_{signal_id}"],
                    metadatas=[{
                        "symbol": signal.symbol,
                        "action": signal.action,
                        "date":   datetime.now().strftime("%Y-%m-%d"),
                    }]
                )
            except Exception as e:
                logger.debug(f"ChromaDB add failed: {e}")

        return signal_id

    def save_trade(self, signal_id: int, symbol: str, action: str,
                   qty: int, entry_price: float, mode: str = "paper") -> int:
        if not self.db_available:
            return -1
        try:
            with self._conn() as conn:
                cur = conn.execute("""
                    INSERT INTO trades
                    (signal_id, symbol, action, qty, entry_price, entry_time, status, mode)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (signal_id, symbol, action, qty, entry_price,
                      datetime.now().isoformat(), "open", mode))
                conn.execute("UPDATE signals SET executed=1 WHERE id=?", (signal_id,))
                return cur.lastrowid
        except sqlite3.Error as e:
            logger.warning(f"save_trade failed ({symbol}): {e}")
            self.db_available = False
            return -1

    def mark_signal_executed(self, signal_id: int):
        """Mark a saved signal as executed without inserting a duplicate trade row."""
        if not self.db_available or signal_id < 0:
            return
        try:
            with self._conn() as conn:
                conn.execute("UPDATE signals SET executed=1 WHERE id=?", (signal_id,))
        except sqlite3.Error as e:
            logger.warning(f"mark_signal_executed failed ({signal_id}): {e}")
            self.db_available = False

    def close_trade(self, trade_id: int, exit_price: float):
        """Mark a trade as closed and calculate P&L."""
        if not self.db_available:
            return
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT entry_price, qty FROM trades WHERE id=?", (trade_id,)
                ).fetchone()
                if not row:
                    return
                entry, qty = row
                pnl     = (exit_price - entry) * qty
                pnl_pct = ((exit_price - entry) / entry) * 100
                conn.execute("""
                    UPDATE trades
                    SET exit_price=?, exit_time=?, pnl=?, pnl_pct=?, status='closed'
                    WHERE id=?
                """, (exit_price, datetime.now().isoformat(),
                      round(pnl, 2), round(pnl_pct, 2), trade_id))
        except sqlite3.Error as e:
            logger.warning(f"close_trade failed ({trade_id}): {e}")
            self.db_available = False

    def save_snapshot(self, summary: dict):
        """Save daily portfolio snapshot."""
        if not self.db_available:
            return
        try:
            with self._conn() as conn:
                conn.execute("""
                    INSERT INTO snapshots
                    (timestamp, portfolio_value, cash, pnl, pnl_pct,
                     open_positions, total_trades, win_rate)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (
                    datetime.now().isoformat(),
                    summary.get("portfolio_value", 0),
                    summary.get("cash", 0),
                    summary.get("pnl", 0),
                    summary.get("pnl_pct", 0),
                    summary.get("open_positions", 0),
                    summary.get("total_trades", 0),
                    summary.get("win_rate", 0),
                ))
        except sqlite3.Error as e:
            logger.warning(f"save_snapshot failed: {e}")
            self.db_available = False

    # ------------------------------------------------------------------
    # Read / analytics
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Full performance stats across all trades."""
        if not self.db_available:
            return {
                "total_trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
                "total_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "profit_factor": 0.0, "max_drawdown_pct": 0.0,
            }
        try:
            with self._conn() as conn:
                total = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE status='closed'"
                ).fetchone()[0]

                wins = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl>0"
                ).fetchone()[0]

                avg_win = conn.execute(
                    "SELECT AVG(pnl) FROM trades WHERE status='closed' AND pnl>0"
                ).fetchone()[0] or 0

                avg_loss = conn.execute(
                    "SELECT AVG(pnl) FROM trades WHERE status='closed' AND pnl<=0"
                ).fetchone()[0] or 0

                total_pnl = conn.execute(
                    "SELECT SUM(pnl) FROM trades WHERE status='closed'"
                ).fetchone()[0] or 0

                snapshots = conn.execute(
                    "SELECT portfolio_value FROM snapshots ORDER BY id"
                ).fetchall()
        except sqlite3.Error as e:
            logger.warning(f"get_stats failed: {e}")
            self.db_available = False
            return {
                "total_trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
                "total_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "profit_factor": 0.0, "max_drawdown_pct": 0.0,
            }

        win_rate     = (wins / total * 100) if total > 0 else 0
        profit_factor= abs(avg_win / avg_loss) if avg_loss != 0 else 0
        max_drawdown = self._calc_drawdown([s[0] for s in snapshots])

        return {
            "total_trades":   total,
            "wins":           wins,
            "losses":         total - wins,
            "win_rate_pct":   round(win_rate, 1),
            "total_pnl":      round(total_pnl, 2),
            "avg_win":        round(avg_win, 2),
            "avg_loss":       round(avg_loss, 2),
            "profit_factor":  round(profit_factor, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
        }

    def get_recent_signals(self, limit: int = 20) -> list[dict]:
        if not self.db_available:
            return []
        try:
            with self._conn() as conn:
                rows = conn.execute("""
                    SELECT timestamp, symbol, action, confidence,
                           entry_price, stop_loss, take_profit, position_size,
                           ta_score, sentiment, reasoning, executed
                    FROM signals
                    ORDER BY id DESC LIMIT ?
                """, (limit,)).fetchall()
        except sqlite3.Error as e:
            logger.warning(f"get_recent_signals failed: {e}")
            self.db_available = False
            return []
        cols = ["timestamp","symbol","action","confidence",
                "entry_price","stop_loss","take_profit","position_size",
                "ta_score","sentiment","reasoning","executed"]
        return [dict(zip(cols, r)) for r in rows]

    def get_recent_trades(self, limit: int = 20) -> list[dict]:
        if not self.db_available:
            return []
        try:
            with self._conn() as conn:
                rows = conn.execute("""
                    SELECT symbol, action, qty, entry_price, exit_price,
                           entry_time, exit_time, pnl, pnl_pct, status, mode
                    FROM trades ORDER BY id DESC LIMIT ?
                """, (limit,)).fetchall()
        except sqlite3.Error as e:
            logger.warning(f"get_recent_trades failed: {e}")
            self.db_available = False
            return []
        cols = ["symbol","action","qty","entry_price","exit_price",
                "entry_time","exit_time","pnl","pnl_pct","status","mode"]
        return [dict(zip(cols, r)) for r in rows]

    def get_snapshots(self) -> list[dict]:
        if not self.db_available:
            return []
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT timestamp, portfolio_value, pnl_pct, win_rate FROM snapshots ORDER BY id"
                ).fetchall()
        except sqlite3.Error as e:
            logger.warning(f"get_snapshots failed: {e}")
            self.db_available = False
            return []
        return [{"timestamp": r[0], "portfolio_value": r[1],
                 "pnl_pct": r[2], "win_rate": r[3]} for r in rows]

    def search_similar_trades(self, query: str, n: int = 5) -> list[dict]:
        """Semantic search of past trade reasoning using ChromaDB."""
        if not self.chroma:
            return []
        try:
            results = self.chroma.query(query_texts=[query], n_results=n)
            return [
                {"reasoning": doc, "metadata": meta}
                for doc, meta in zip(
                    results["documents"][0],
                    results["metadatas"][0]
                )
            ]
        except Exception as e:
            logger.debug(f"ChromaDB query failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _calc_drawdown(self, values: list) -> float:
        if len(values) < 2:
            return 0.0
        peak = values[0]
        max_dd = 0.0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        return max_dd

    def _init_chroma(self):
        """Try to init ChromaDB — silently skip if not installed."""
        try:
            import chromadb
            os.makedirs(CHROMA_PERSIST_DIR, exist_ok=True)
            client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
            col = client.get_or_create_collection(CHROMA_COLLECTION)
            logger.info("ChromaDB connected")
            return col
        except ImportError:
            logger.info("ChromaDB not installed — semantic search disabled (pip install chromadb)")
            return None
        except Exception as e:
            logger.warning(f"ChromaDB init failed: {e}")
            return None
