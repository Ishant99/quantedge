# =============================================================================
# execution/brokers/us_paper_broker.py — US Stocks Paper Trading Broker
#
# Simulates US stock trades using Yahoo Finance live prices.
# Positions stored in SQLite us_trades table.
# TP=+6%, SL=-3% (2:1 RR), fractional shares supported.
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3
from datetime import datetime
from config import SQLITE_DB_FILE
from data.us_scanner import USScanner
from utils import get_logger

logger = get_logger("USPaperBroker")

from config import US_TP_PCT as TP_PCT, US_SL_PCT as SL_PCT


class USPaperBroker:

    def __init__(self):
        self.db      = SQLITE_DB_FILE
        self.scanner = USScanner()
        self._init_table()

    def open_position(self, symbol: str, direction: str,
                      entry_price: float = None,
                      usd_amount: float = 500.0,
                      reasoning: str = "") -> int | None:
        if entry_price is None:
            entry_price = self.scanner.get_current_price(symbol)
        if not entry_price or entry_price <= 0:
            logger.warning(f"Cannot fetch price for {symbol} — position skipped")
            return None

        qty  = round(usd_amount / entry_price, 4)   # fractional shares
        sl   = round(entry_price * (1 - SL_PCT) if direction == "LONG"
                     else entry_price * (1 + SL_PCT), 4)
        tp   = round(entry_price * (1 + TP_PCT) if direction == "LONG"
                     else entry_price * (1 - TP_PCT), 4)

        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO us_trades
                (symbol, direction, entry_price, current_price, qty,
                 usd_amount, sl_price, tp_price, entry_time, status, reasoning)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (symbol, direction, entry_price, entry_price, qty,
                  usd_amount, sl, tp,
                  datetime.now().isoformat(), "open", reasoning))
            trade_id = cur.lastrowid

        logger.info(f"US OPEN: {symbol} {direction} @ ${entry_price:.2f} | "
                    f"Qty {qty:.4f} | SL ${sl:.2f} | TP ${tp:.2f} | id={trade_id}")
        return trade_id

    def close_position(self, trade_id: int, exit_price: float = None,
                       reason: str = "MANUAL") -> dict | None:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT symbol, direction, entry_price, qty
                FROM us_trades WHERE id=? AND status='open'
            """, (trade_id,)).fetchone()
        if not row:
            return None
        symbol, direction, entry, qty = row
        if exit_price is None:
            exit_price = self.scanner.get_current_price(symbol) or entry

        if direction == "LONG":
            pnl_usd = round((exit_price - entry) * qty, 4)
            pnl_pct = round((exit_price - entry) / entry * 100, 2)
        else:
            pnl_usd = round((entry - exit_price) * qty, 4)
            pnl_pct = round((entry - exit_price) / entry * 100, 2)

        with self._conn() as conn:
            conn.execute("""
                UPDATE us_trades
                SET exit_price=?, current_price=?, pnl_usd=?, pnl_pct=?,
                    exit_time=?, status='closed', exit_reason=?
                WHERE id=?
            """, (exit_price, exit_price, pnl_usd, pnl_pct,
                  datetime.now().isoformat(), reason, trade_id))

        logger.info(f"US CLOSE: {symbol} | Exit ${exit_price:.2f} | "
                    f"P&L ${pnl_usd:+.2f} ({pnl_pct:+.2f}%) | {reason}")
        return {"trade_id": trade_id, "symbol": symbol, "direction": direction,
                "entry": entry, "exit": exit_price,
                "pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "reason": reason}

    def monitor_and_exit(self) -> list[dict]:
        open_pos = self.get_open_positions()
        closed   = []
        for pos in open_pos:
            curr = self.scanner.get_current_price(pos["symbol"])
            if not curr:
                continue
            if pos["direction"] == "LONG":
                pnl = round((curr - pos["entry_price"]) * pos["qty"], 4)
            else:
                pnl = round((pos["entry_price"] - curr) * pos["qty"], 4)
            with self._conn() as conn:
                conn.execute(
                    "UPDATE us_trades SET current_price=?, pnl_usd=? WHERE id=?",
                    (curr, pnl, pos["id"])
                )
            reason = None
            if pos["direction"] == "LONG":
                if curr >= pos["tp_price"]:   reason = "TP_HIT"
                elif curr <= pos["sl_price"]: reason = "SL_HIT"
            else:
                if curr <= pos["tp_price"]:   reason = "TP_HIT"
                elif curr >= pos["sl_price"]: reason = "SL_HIT"
            if reason:
                result = self.close_position(pos["id"], curr, reason)
                if result:
                    closed.append(result)
        return closed

    def get_open_positions(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT id, symbol, direction, entry_price, current_price,
                       qty, usd_amount, sl_price, tp_price,
                       pnl_usd, entry_time, reasoning
                FROM us_trades WHERE status='open'
                ORDER BY entry_time DESC
            """).fetchall()
        cols = ["id","symbol","direction","entry_price","current_price",
                "qty","usd_amount","sl_price","tp_price",
                "pnl_usd","entry_time","reasoning"]
        return [dict(zip(cols, r)) for r in rows]

    def get_closed_trades(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT id, symbol, direction, entry_price, exit_price,
                       qty, usd_amount, pnl_usd, pnl_pct,
                       entry_time, exit_time, exit_reason
                FROM us_trades WHERE status='closed'
                ORDER BY exit_time DESC LIMIT ?
            """, (limit,)).fetchall()
        cols = ["id","symbol","direction","entry_price","exit_price",
                "qty","usd_amount","pnl_usd","pnl_pct",
                "entry_time","exit_time","exit_reason"]
        return [dict(zip(cols, r)) for r in rows]

    def get_stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM us_trades WHERE status='closed'"
            ).fetchone()[0]
            wins = conn.execute(
                "SELECT COUNT(*) FROM us_trades WHERE status='closed' AND pnl_usd>0"
            ).fetchone()[0]
            total_pnl = conn.execute(
                "SELECT COALESCE(SUM(pnl_usd),0) FROM us_trades WHERE status='closed'"
            ).fetchone()[0]
            open_count = conn.execute(
                "SELECT COUNT(*) FROM us_trades WHERE status='open'"
            ).fetchone()[0]
        return {
            "total": total, "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "total_pnl_usd": round(total_pnl, 4),
            "open_positions": open_count,
        }

    def _conn(self):
        return sqlite3.connect(self.db)

    def _init_table(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS us_trades (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol        TEXT NOT NULL,
                    direction     TEXT NOT NULL,
                    entry_price   REAL,
                    current_price REAL,
                    exit_price    REAL,
                    qty           REAL,
                    usd_amount    REAL,
                    sl_price      REAL,
                    tp_price      REAL,
                    pnl_usd       REAL DEFAULT 0,
                    pnl_pct       REAL DEFAULT 0,
                    entry_time    TEXT,
                    exit_time     TEXT,
                    status        TEXT DEFAULT 'open',
                    exit_reason   TEXT,
                    reasoning     TEXT
                )
            """)
