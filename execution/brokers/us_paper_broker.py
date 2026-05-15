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
from contextlib import contextmanager
from datetime import datetime
from config import SQLITE_DB_FILE, US_DEDUP_HOURS, US_DEDUP_PRICE_PCT, US_MAX_POSITIONS
from data.us_scanner import USScanner
from services.paper_treasury import (
    can_allocate,
    log_treasury_event,
    reserve_for_us_order,
    write_treasury_snapshot,
)
from utils import get_logger

logger = get_logger("USPaperBroker")

from config import US_TP_PCT as TP_PCT, US_SL_PCT as SL_PCT


class USPaperBroker:

    def __init__(self):
        self.db      = SQLITE_DB_FILE
        self.scanner = USScanner()
        self._init_table()


    def _check_duplicate_position(self, symbol, direction, entry_price):
        """
        Block entry if the same symbol+direction was entered within US_DEDUP_HOURS
        at a price within +/-US_DEDUP_PRICE_PCT of current entry_price.
        Prevents same-signal re-fires on consecutive days.
        """
        try:
            hours = int(US_DEDUP_HOURS)
            pct   = float(US_DEDUP_PRICE_PCT)
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
            lo = entry_price * (1 - pct)
            hi = entry_price * (1 + pct)
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT id, entry_price, entry_time FROM us_trades "
                    "WHERE symbol=? AND direction=? AND entry_time >= ? "
                    "AND status IN ('open','closed')",
                    (symbol.upper(), direction.upper(), cutoff)
                ).fetchall()
            for (tid, ep, et) in rows:
                if ep is not None and lo <= float(ep) <= hi:
                    return False, (
                        f"US dedup: {symbol} {direction} already entered at ${ep:.2f} "
                        f"(within {pct*100:.0f}% of ${entry_price:.2f}) at {et}"
                    )
        except Exception as e:
            logger.warning(f"US dedup check failed ({symbol}), allowing: {e}")
        return True, ""

    def open_position(self, symbol: str, direction: str,
                      entry_price: float = None,
                      usd_amount: float = 500.0,
                      reasoning: str = "") -> int | None:
        # Asset class gate (Phase 7)
        try:
            from config import ASSET_CLASS_GATES
            if not ASSET_CLASS_GATES.get("us_equities", {}).get("enabled", False):
                logger.warning("USPaperBroker.open_position blocked: us_equities disabled in ASSET_CLASS_GATES")
                return None
        except Exception:
            pass

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
        open_count = len(self.get_open_positions())
        if open_count >= US_MAX_POSITIONS:
            logger.warning(f"US position cap reached ({US_MAX_POSITIONS}) — skipping {symbol}")
            return None
        ok_dup, reason_dup = self._check_duplicate_position(symbol, direction, entry_price)
        if not ok_dup:
            logger.warning(f"{reason_dup} -- skipping")
            return None
        reserve_inr = reserve_for_us_order(usd_amount)
        ok, reason, _ = can_allocate("us", reserve_inr)
        if not ok:
            logger.warning(f"US treasury block for {symbol}: {reason}")
            return None

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
        log_treasury_event("reserve_open", "us", reserve_inr, f"{symbol} {direction}", {"trade_id": trade_id, "symbol": symbol})
        write_treasury_snapshot()

        logger.info(f"US OPEN: {symbol} {direction} @ ${entry_price:.2f} | "
                    f"Qty {qty:.4f} | SL ${sl:.2f} | TP ${tp:.2f} | id={trade_id}")
        return trade_id

    def close_position(self, trade_id: int, exit_price: float = None,
                       reason: str = "MANUAL") -> dict | None:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT symbol, direction, entry_price, qty, usd_amount
                FROM us_trades WHERE id=? AND status='open'
            """, (trade_id,)).fetchone()
        if not row:
            return None
        symbol, direction, entry, qty, usd_amount = row
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
        log_treasury_event(
            "release_close",
            "us",
            reserve_for_us_order(usd_amount),
            f"{symbol} {direction}",
            {"trade_id": trade_id, "symbol": symbol, "pnl_usd": pnl_usd},
        )
        write_treasury_snapshot()

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

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

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
