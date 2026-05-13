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
from contextlib import contextmanager
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
                    setup_type    TEXT DEFAULT '',
                    regime_tag    TEXT DEFAULT '',
                    quality_score REAL DEFAULT 0,
                    expectancy_score REAL DEFAULT 0,
                    symbol_edge   REAL DEFAULT 0,
                    setup_edge    REAL DEFAULT 0,
                    quality_flags TEXT DEFAULT '',
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

                CREATE TABLE IF NOT EXISTS fno_trades (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT NOT NULL,
                    symbol          TEXT NOT NULL,
                    action          TEXT,
                    instrument      TEXT,
                    strike          REAL,
                    expiry          TEXT,
                    qty             INTEGER,
                    entry_price     REAL,
                    exit_price      REAL,
                    entry_time      TEXT,
                    exit_time       TEXT,
                    pnl             REAL DEFAULT 0,
                    pnl_pct         REAL DEFAULT 0,
                    status          TEXT DEFAULT 'open',
                    mode            TEXT DEFAULT 'paper',
                    option_type     TEXT DEFAULT '',
                    current_premium REAL DEFAULT 0,
                    reasoning       TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS crypto_trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT NOT NULL,
                    symbol      TEXT NOT NULL,
                    action      TEXT,
                    qty         REAL,
                    entry_price REAL,
                    exit_price  REAL,
                    entry_time  TEXT,
                    exit_time   TEXT,
                    pnl         REAL DEFAULT 0,
                    pnl_pct     REAL DEFAULT 0,
                    status      TEXT DEFAULT 'open',
                    mode        TEXT DEFAULT 'paper'
                );

                CREATE TABLE IF NOT EXISTS us_trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT NOT NULL,
                    symbol      TEXT NOT NULL,
                    action      TEXT,
                    qty         INTEGER,
                    entry_price REAL,
                    exit_price  REAL,
                    entry_time  TEXT,
                    exit_time   TEXT,
                    pnl         REAL DEFAULT 0,
                    pnl_pct     REAL DEFAULT 0,
                    status      TEXT DEFAULT 'open',
                    mode        TEXT DEFAULT 'paper'
                );

                CREATE TABLE IF NOT EXISTS decision_journals (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id    INTEGER,
                    symbol       TEXT NOT NULL,
                    timestamp    TEXT NOT NULL,
                    regime       TEXT,
                    json_blob    TEXT NOT NULL,
                    outcome_1d   REAL,
                    outcome_3d   REAL,
                    outcome_5d   REAL,
                    outcome_exit REAL,
                    FOREIGN KEY (signal_id) REFERENCES signals(id)
                );
            """)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(signals)").fetchall()]
            alter_statements = {
                "setup_type":      "ALTER TABLE signals ADD COLUMN setup_type TEXT DEFAULT ''",
                "regime_tag":      "ALTER TABLE signals ADD COLUMN regime_tag TEXT DEFAULT ''",
                "quality_score":   "ALTER TABLE signals ADD COLUMN quality_score REAL DEFAULT 0",
                "expectancy_score":"ALTER TABLE signals ADD COLUMN expectancy_score REAL DEFAULT 0",
                "symbol_edge":     "ALTER TABLE signals ADD COLUMN symbol_edge REAL DEFAULT 0",
                "setup_edge":      "ALTER TABLE signals ADD COLUMN setup_edge REAL DEFAULT 0",
                "quality_flags":   "ALTER TABLE signals ADD COLUMN quality_flags TEXT DEFAULT ''",
            }
            for col, stmt in alter_statements.items():
                if col not in cols:
                    conn.execute(stmt)

            fno_cols = [r[1] for r in conn.execute("PRAGMA table_info(fno_trades)").fetchall()]
            fno_alters = {
                "option_type":     "ALTER TABLE fno_trades ADD COLUMN option_type TEXT DEFAULT ''",
                "current_premium": "ALTER TABLE fno_trades ADD COLUMN current_premium REAL DEFAULT 0",
                "reasoning":       "ALTER TABLE fno_trades ADD COLUMN reasoning TEXT DEFAULT ''",
            }
            for col, stmt in fno_alters.items():
                if col not in fno_cols:
                    conn.execute(stmt)

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
                     sentiment, setup_type, regime_tag, quality_score,
                     expectancy_score, symbol_edge, setup_edge, quality_flags,
                     reasoning, executed)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                """, (
                    datetime.now().isoformat(),
                    signal.symbol, signal.action, signal.confidence,
                    signal.entry_price, signal.stop_loss, signal.take_profit,
                    signal.position_size, signal.ta_score,
                    signal.sentiment,
                    getattr(signal, "setup_type", ""),
                    getattr(signal, "regime_tag", ""),
                    getattr(signal, "quality_score", 0.0),
                    getattr(signal, "expectancy_score", 0.0),
                    getattr(signal, "symbol_edge", 0.0),
                    getattr(signal, "setup_edge", 0.0),
                    ",".join(getattr(signal, "quality_flags", []) or []),
                    signal.reasoning,
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
                pnl_pct = ((exit_price - entry) / entry) * 100 if entry else 0
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

    def save_journal(self, journal, signal_id: int = -1) -> int:
        """Persist a DecisionJournal. Returns row id, or -1 on failure."""
        if not self.db_available:
            return -1
        try:
            blob = json.dumps(journal.to_dict())
            with self._conn() as conn:
                cur = conn.execute("""
                    INSERT INTO decision_journals
                    (signal_id, symbol, timestamp, regime, json_blob)
                    VALUES (?,?,?,?,?)
                """, (
                    signal_id,
                    journal.symbol,
                    journal.timestamp.isoformat(),
                    journal.regime,
                    blob,
                ))
                return cur.lastrowid
        except Exception as e:
            logger.warning(f"save_journal failed ({journal.symbol}): {e}")
            return -1

    def get_journal(self, signal_id: int):
        """Retrieve a DecisionJournal by signal_id. Returns None if not found."""
        if not self.db_available:
            return None
        try:
            from strategy.decision_journal import DecisionJournal
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT json_blob FROM decision_journals WHERE signal_id=? LIMIT 1",
                    (signal_id,)
                ).fetchone()
            if row:
                return DecisionJournal.from_dict(json.loads(row[0]))
        except Exception as e:
            logger.warning(f"get_journal failed (signal_id={signal_id}): {e}")
        return None

    def update_journal_outcome(self, signal_id: int, horizon: str, return_pct: float):
        """Update post-trade outcome for a journal row. horizon: '1d'|'3d'|'5d'|'exit'."""
        if not self.db_available:
            return
        col_map = {"1d": "outcome_1d", "3d": "outcome_3d",
                   "5d": "outcome_5d", "exit": "outcome_exit"}
        col = col_map.get(horizon)
        if not col:
            logger.warning(f"update_journal_outcome: unknown horizon '{horizon}'")
            return
        try:
            with self._conn() as conn:
                conn.execute(
                    f"UPDATE decision_journals SET {col}=? WHERE signal_id=?",
                    (round(return_pct, 4), signal_id)
                )
        except Exception as e:
            logger.warning(f"update_journal_outcome failed (signal_id={signal_id}): {e}")

    # ------------------------------------------------------------------
    # Read / analytics
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Full performance stats across all trades."""
        if not self.db_available:
            return {
                "total_trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
                "total_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "profit_factor": 0.0, "max_drawdown_pct": 0.0, "expectancy": 0.0,
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
                "profit_factor": 0.0, "max_drawdown_pct": 0.0, "expectancy": 0.0,
            }

        losses        = total - wins
        win_rate      = (wins / total * 100) if total > 0 else 0
        loss_rate     = (losses / total) if total > 0 else 0
        win_rate_frac = win_rate / 100
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        max_drawdown  = self._calc_drawdown([s[0] for s in snapshots])
        # Expectancy: average Rs. earned per trade
        expectancy    = (avg_win * win_rate_frac) + (avg_loss * loss_rate)

        return {
            "total_trades":     total,
            "wins":             wins,
            "losses":           losses,
            "win_rate_pct":     round(win_rate, 1),
            "total_pnl":        round(total_pnl, 2),
            "avg_win":          round(avg_win, 2),
            "avg_loss":         round(avg_loss, 2),
            "profit_factor":    round(profit_factor, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "expectancy":       round(expectancy, 2),
        }

    def get_segmented_stats(self) -> dict:
        """
        Returns performance stats broken down by:
          - regime_tag  (bull, bear, sideways, recovery, …)
          - setup_type  (technical_base, momentum, …)
          - confidence bucket (0.5-0.6, 0.6-0.7, 0.7-0.8, 0.8+)

        Joins signals → trades on signal_id to get pnl data.
        Only closed trades are considered.

        Returns:
            {
                "by_regime":     {"bull": {"trades": int, "win_rate": float, "avg_pnl": float}, …},
                "by_setup":      {"technical_base": {…}, …},
                "by_confidence": {"0.5-0.6": {…}, "0.6-0.7": {…}, "0.7-0.8": {…}, "0.8+": {…}},
            }
        """
        empty = {"by_regime": {}, "by_setup": {}, "by_confidence": {}}
        if not self.db_available:
            return empty

        try:
            with self._conn() as conn:
                rows = conn.execute("""
                    SELECT
                        s.regime_tag,
                        s.setup_type,
                        s.confidence,
                        t.pnl
                    FROM trades t
                    JOIN signals s ON s.id = t.signal_id
                    WHERE t.status = 'closed'
                      AND t.pnl IS NOT NULL
                """).fetchall()
        except sqlite3.Error as e:
            logger.warning(f"get_segmented_stats: query failed: {e}")
            return empty

        if not rows:
            return empty

        def _conf_bucket(conf) -> str:
            if conf is None:
                return "unknown"
            c = float(conf)
            if c < 0.6:
                return "0.5-0.6"
            if c < 0.7:
                return "0.6-0.7"
            if c < 0.8:
                return "0.7-0.8"
            return "0.8+"

        # Accumulators: {key: {"pnls": [float], "wins": int}}
        regime_acc:     dict[str, dict] = {}
        setup_acc:      dict[str, dict] = {}
        confidence_acc: dict[str, dict] = {}

        def _add(acc: dict, key: str, pnl: float):
            key = key.strip() if key else "unknown"
            if not key:
                key = "unknown"
            if key not in acc:
                acc[key] = {"pnls": [], "wins": 0}
            acc[key]["pnls"].append(pnl)
            if pnl > 0:
                acc[key]["wins"] += 1

        for regime_tag, setup_type, confidence, pnl in rows:
            _add(regime_acc,     regime_tag or "unknown", pnl)
            _add(setup_acc,      setup_type or "unknown", pnl)
            _add(confidence_acc, _conf_bucket(confidence), pnl)

        def _summarise(acc: dict) -> dict:
            result = {}
            for key, data in sorted(acc.items()):
                pnls = data["pnls"]
                n    = len(pnls)
                result[key] = {
                    "trades":   n,
                    "win_rate": round(data["wins"] / n, 4) if n > 0 else 0.0,
                    "avg_pnl":  round(sum(pnls) / n, 2)   if n > 0 else 0.0,
                }
            return result

        return {
            "by_regime":     _summarise(regime_acc),
            "by_setup":      _summarise(setup_acc),
            "by_confidence": _summarise(confidence_acc),
        }

    def get_recent_signals(self, limit: int = 20) -> list[dict]:
        if not self.db_available:
            return []
        try:
            with self._conn() as conn:
                rows = conn.execute("""
                    SELECT timestamp, symbol, action, confidence,
                           entry_price, stop_loss, take_profit, position_size,
                           ta_score, sentiment, setup_type, regime_tag,
                           quality_score, expectancy_score, symbol_edge, setup_edge,
                           quality_flags, reasoning, executed
                    FROM signals
                    ORDER BY id DESC LIMIT ?
                """, (limit,)).fetchall()
        except sqlite3.Error as e:
            logger.warning(f"get_recent_signals failed: {e}")
            self.db_available = False
            return []
        cols = ["timestamp","symbol","action","confidence",
                "entry_price","stop_loss","take_profit","position_size",
                "ta_score","sentiment","setup_type","regime_tag",
                "quality_score","expectancy_score","symbol_edge","setup_edge",
                "quality_flags","reasoning","executed"]
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

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

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
