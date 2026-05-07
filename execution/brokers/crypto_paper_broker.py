# =============================================================================
# execution/brokers/crypto_paper_broker.py — Crypto Paper Trading Broker
#
# Simulates crypto trades using Binance live prices.
# No API key needed (uses public ticker endpoint).
# Positions stored in SQLite crypto_trades table.
# Exit: TP=+8%, SL=-4% (2:1 RR), or manual
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3
from datetime import datetime
from config import SQLITE_DB_FILE, CRYPTO_MAX_POSITIONS
from data.crypto_scanner import CryptoScanner
from services.paper_treasury import (
    can_allocate,
    log_treasury_event,
    reserve_for_crypto_order,
    write_treasury_snapshot,
)
from utils import get_logger

logger = get_logger("CryptoPaperBroker")

from config import CRYPTO_TP_PCT as TP_PCT, CRYPTO_SL_PCT as SL_PCT


class CryptoPaperBroker:

    def __init__(self):
        self.db      = SQLITE_DB_FILE
        self.scanner = CryptoScanner()
        self._init_table()

    def open_position(self, symbol: str, direction: str,
                      entry_price: float = None,
                      usdt_amount: float = 100.0,
                      reasoning: str = "") -> int | None:
        """
        Open a paper crypto position.
        direction: "LONG" | "SHORT"
        usdt_amount: capital in USDT to allocate
        """
        if entry_price is None:
            entry_price = self.scanner.get_current_price(symbol)
        if not entry_price or entry_price <= 0:
            logger.warning(f"Cannot fetch price for {symbol} — position skipped")
            return None

        qty    = round(usdt_amount / entry_price, 6)
        sl     = round(entry_price * (1 - SL_PCT) if direction == "LONG"
                       else entry_price * (1 + SL_PCT), 6)
        tp     = round(entry_price * (1 + TP_PCT) if direction == "LONG"
                       else entry_price * (1 - TP_PCT), 6)
        open_count = len(self.get_open_positions())
        if open_count >= CRYPTO_MAX_POSITIONS:
            logger.warning(f"Crypto position cap reached ({CRYPTO_MAX_POSITIONS}) — skipping {symbol}")
            return None
        reserve_inr = reserve_for_crypto_order(usdt_amount)
        ok, reason, _ = can_allocate("crypto", reserve_inr)
        if not ok:
            logger.warning(f"Crypto treasury block for {symbol}: {reason}")
            return None

        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO crypto_trades
                (symbol, direction, entry_price, current_price, qty,
                 usdt_amount, sl_price, tp_price, entry_time, status, reasoning)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (symbol, direction, entry_price, entry_price, qty,
                  usdt_amount, sl, tp,
                  datetime.now().isoformat(), "open", reasoning))
            trade_id = cur.lastrowid
        log_treasury_event("reserve_open", "crypto", reserve_inr, f"{symbol} {direction}", {"trade_id": trade_id, "symbol": symbol})
        write_treasury_snapshot()

        logger.info(f"CRYPTO OPEN: {symbol} {direction} @ {entry_price:.4f} | "
                    f"Qty {qty:.4f} | SL {sl:.4f} | TP {tp:.4f} | id={trade_id}")
        return trade_id

    def close_position(self, trade_id: int, exit_price: float = None,
                       reason: str = "MANUAL") -> dict | None:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT symbol, direction, entry_price, qty, usdt_amount
                FROM crypto_trades WHERE id=? AND status='open'
            """, (trade_id,)).fetchone()
        if not row:
            return None

        symbol, direction, entry, qty, usdt_in = row
        if exit_price is None:
            exit_price = self.scanner.get_current_price(symbol) or entry

        if direction == "LONG":
            pnl_usdt = round((exit_price - entry) * qty, 4)
            pnl_pct  = round((exit_price - entry) / entry * 100, 2)
        else:
            pnl_usdt = round((entry - exit_price) * qty, 4)
            pnl_pct  = round((entry - exit_price) / entry * 100, 2)

        with self._conn() as conn:
            conn.execute("""
                UPDATE crypto_trades
                SET exit_price=?, current_price=?, pnl_usdt=?, pnl_pct=?,
                    exit_time=?, status='closed', exit_reason=?
                WHERE id=?
            """, (exit_price, exit_price, pnl_usdt, pnl_pct,
                  datetime.now().isoformat(), reason, trade_id))
        log_treasury_event(
            "release_close",
            "crypto",
            reserve_for_crypto_order(usdt_in),
            f"{symbol} {direction}",
            {"trade_id": trade_id, "symbol": symbol, "pnl_usdt": pnl_usdt},
        )
        write_treasury_snapshot()

        result = {"trade_id": trade_id, "symbol": symbol, "direction": direction,
                  "entry": entry, "exit": exit_price,
                  "pnl_usdt": pnl_usdt, "pnl_pct": pnl_pct, "reason": reason}
        logger.info(f"CRYPTO CLOSE: {symbol} {direction} | "
                    f"Exit {exit_price:.4f} | P&L {pnl_usdt:+.4f} USDT ({pnl_pct:+.2f}%) | {reason}")
        return result

    def monitor_and_exit(self) -> list[dict]:
        """Check all open crypto positions for TP/SL hits."""
        open_pos = self.get_open_positions()
        closed   = []
        for pos in open_pos:
            curr = self.scanner.get_current_price(pos["symbol"])
            if not curr:
                continue

            # Update current price + unrealized P&L
            if pos["direction"] == "LONG":
                pnl = round((curr - pos["entry_price"]) * pos["qty"], 4)
            else:
                pnl = round((pos["entry_price"] - curr) * pos["qty"], 4)

            with self._conn() as conn:
                conn.execute(
                    "UPDATE crypto_trades SET current_price=?, pnl_usdt=? WHERE id=?",
                    (curr, pnl, pos["id"])
                )

            reason = None
            if pos["direction"] == "LONG":
                if curr >= pos["tp_price"]:  reason = "TP_HIT"
                elif curr <= pos["sl_price"]: reason = "SL_HIT"
            else:
                if curr <= pos["tp_price"]:  reason = "TP_HIT"
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
                       qty, usdt_amount, sl_price, tp_price,
                       pnl_usdt, entry_time, reasoning
                FROM crypto_trades WHERE status='open'
                ORDER BY entry_time DESC
            """).fetchall()
        cols = ["id","symbol","direction","entry_price","current_price",
                "qty","usdt_amount","sl_price","tp_price",
                "pnl_usdt","entry_time","reasoning"]
        return [dict(zip(cols, r)) for r in rows]

    def get_closed_trades(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT id, symbol, direction, entry_price, exit_price,
                       qty, usdt_amount, pnl_usdt, pnl_pct,
                       entry_time, exit_time, exit_reason
                FROM crypto_trades WHERE status='closed'
                ORDER BY exit_time DESC LIMIT ?
            """, (limit,)).fetchall()
        cols = ["id","symbol","direction","entry_price","exit_price",
                "qty","usdt_amount","pnl_usdt","pnl_pct",
                "entry_time","exit_time","exit_reason"]
        return [dict(zip(cols, r)) for r in rows]

    def get_stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM crypto_trades WHERE status='closed'"
            ).fetchone()[0]
            wins = conn.execute(
                "SELECT COUNT(*) FROM crypto_trades WHERE status='closed' AND pnl_usdt>0"
            ).fetchone()[0]
            total_pnl = conn.execute(
                "SELECT COALESCE(SUM(pnl_usdt),0) FROM crypto_trades WHERE status='closed'"
            ).fetchone()[0]
            open_count = conn.execute(
                "SELECT COUNT(*) FROM crypto_trades WHERE status='open'"
            ).fetchone()[0]
        return {
            "total": total, "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "total_pnl_usdt": round(total_pnl, 4),
            "open_positions": open_count,
        }

    def _conn(self):
        return sqlite3.connect(self.db)

    def _init_table(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS crypto_trades (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol        TEXT NOT NULL,
                    direction     TEXT NOT NULL,    -- LONG | SHORT
                    entry_price   REAL,
                    current_price REAL,
                    exit_price    REAL,
                    qty           REAL,             -- in crypto units
                    usdt_amount   REAL,             -- capital allocated
                    sl_price      REAL,
                    tp_price      REAL,
                    pnl_usdt      REAL DEFAULT 0,
                    pnl_pct       REAL DEFAULT 0,
                    entry_time    TEXT,
                    exit_time     TEXT,
                    status        TEXT DEFAULT 'open',
                    exit_reason   TEXT,
                    reasoning     TEXT
                )
            """)
