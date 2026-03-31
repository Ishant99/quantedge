# =============================================================================
# execution/brokers/fno_paper_broker.py — F&O Paper Trading Broker
#
# Simulates buying/selling Nifty & BankNifty options (CE/PE).
# Positions stored in SQLite fno_trades table.
# Exit triggers: premium 2x (TP) | premium -50% (SL) | expiry date
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3
from datetime import datetime
from config import SQLITE_DB_FILE, FNO_LOT_SIZES, FNO_TP_MULT, FNO_SL_MULT, FNO_MAX_POSITIONS
from data.nse_options_chain import NSEOptionsChain
from utils import get_logger

logger   = get_logger("FNOPaperBroker")
TP_MULT  = FNO_TP_MULT
SL_MULT  = FNO_SL_MULT
LOT_SIZES = FNO_LOT_SIZES


class FNOPaperBroker:

    def __init__(self):
        self.db  = SQLITE_DB_FILE
        self.chain = NSEOptionsChain()
        self._init_table()

    # ------------------------------------------------------------------
    # Open a new paper position
    # ------------------------------------------------------------------
    def open_position(self, index: str, direction: str, strike: int,
                      expiry: str, lots: int = 1,
                      entry_premium: float = None,
                      reasoning: str = "") -> int | None:
        """
        Open a paper F&O position.
        direction: "CALL" or "PUT"  → maps to CE/PE
        Returns trade_id or None if premium unavailable or position limit reached.
        """
        # Enforce position limit
        open_count = self.get_stats()["open_positions"]
        if open_count >= FNO_MAX_POSITIONS:
            logger.warning(f"F&O position limit reached ({FNO_MAX_POSITIONS}) — skipping {index} {direction}")
            return None

        opt_type = "CE" if direction == "CALL" else "PE"
        lot_size = LOT_SIZES.get(index, 25)

        if entry_premium is None:
            step = 50 if index == "NIFTY" else 100
            entry_premium = self.chain.get_premium(index, strike, opt_type)
            if not entry_premium:
                logger.warning(f"Cannot fetch premium for {index} {strike} {opt_type} — position not opened")
                return None

        qty = lots * lot_size
        capital_used = round(entry_premium * qty, 2)

        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO fno_trades
                (instrument, option_type, strike, expiry, lot_size, lots, qty,
                 entry_premium, current_premium, entry_time, status, reasoning)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (index, opt_type, strike, expiry, lot_size, lots, qty,
                  entry_premium, entry_premium,
                  datetime.now().isoformat(), "open", reasoning))
            trade_id = cur.lastrowid

        logger.info(f"F&O OPEN: {index} {strike}{opt_type} | "
                    f"Entry Rs.{entry_premium} x {qty} = Rs.{capital_used:,.0f} | "
                    f"Expiry {expiry} | id={trade_id}")
        return trade_id

    # ------------------------------------------------------------------
    # Close a position
    # ------------------------------------------------------------------
    def close_position(self, trade_id: int, exit_premium: float = None,
                       reason: str = "MANUAL") -> dict | None:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT instrument, option_type, strike, expiry,
                       lot_size, lots, qty, entry_premium
                FROM fno_trades WHERE id=? AND status='open'
            """, (trade_id,)).fetchone()

        if not row:
            return None

        index, opt_type, strike, expiry, lot_size, lots, qty, entry_prem = row

        if exit_premium is None:
            exit_premium = self.chain.get_premium(index, strike, opt_type) or entry_prem * 0.5

        pnl     = round((exit_premium - entry_prem) * qty, 2)
        pnl_pct = round((exit_premium - entry_prem) / entry_prem * 100, 2)

        with self._conn() as conn:
            conn.execute("""
                UPDATE fno_trades
                SET exit_premium=?, current_premium=?, pnl=?, pnl_pct=?,
                    exit_time=?, status='closed', exit_reason=?
                WHERE id=?
            """, (exit_premium, exit_premium, pnl, pnl_pct,
                  datetime.now().isoformat(), reason, trade_id))

        result = {"trade_id": trade_id, "index": index, "strike": strike,
                  "option_type": opt_type, "entry": entry_prem,
                  "exit": exit_premium, "pnl": pnl, "pnl_pct": pnl_pct,
                  "reason": reason}
        logger.info(f"F&O CLOSE: {index} {strike}{opt_type} | "
                    f"Exit Rs.{exit_premium} | P&L Rs.{pnl:+,.0f} ({pnl_pct:+.1f}%) | {reason}")
        return result

    # ------------------------------------------------------------------
    # Monitor all open positions — called every 15 min by scheduler
    # ------------------------------------------------------------------
    def monitor_and_exit(self) -> list[dict]:
        """Check all BUY options positions for TP/SL/expiry. Returns list of closed trades."""
        open_pos = self.get_open_positions()
        closed   = []
        today    = datetime.now().strftime("%d-%b-%Y")

        for pos in open_pos:
            opt_type = pos["option_type"]
            # SELL and FUT positions are handled by their own monitors
            if opt_type.startswith("SELL") or opt_type.startswith("FUT"):
                continue

            trade_id   = pos["id"]
            index      = pos["instrument"]
            strike     = pos["strike"]
            expiry     = pos["expiry"]
            entry_prem = pos["entry_premium"]

            current = self.chain.get_premium(index, strike, opt_type)
            if current is None:
                continue

            with self._conn() as conn:
                pnl_live = round((current - entry_prem) * pos["qty"], 2)
                conn.execute(
                    "UPDATE fno_trades SET current_premium=?, pnl=? WHERE id=?",
                    (current, pnl_live, trade_id)
                )

            # Check exit conditions
            reason = None
            if current >= entry_prem * TP_MULT:
                reason = "TP_HIT"
            elif current <= entry_prem * SL_MULT:
                reason = "SL_HIT"
            elif expiry <= today:
                reason = "EXPIRY"

            if reason:
                result = self.close_position(trade_id, current, reason)
                if result:
                    closed.append(result)

        return closed

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def get_open_positions(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT id, instrument, option_type, strike, expiry,
                       lot_size, lots, qty, entry_premium, current_premium,
                       pnl, entry_time, reasoning
                FROM fno_trades WHERE status='open'
                ORDER BY entry_time DESC
            """).fetchall()
        cols = ["id","instrument","option_type","strike","expiry",
                "lot_size","lots","qty","entry_premium","current_premium",
                "pnl","entry_time","reasoning"]
        return [dict(zip(cols, r)) for r in rows]

    def get_closed_trades(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT id, instrument, option_type, strike, expiry,
                       lots, qty, entry_premium, exit_premium,
                       pnl, pnl_pct, entry_time, exit_time, exit_reason
                FROM fno_trades WHERE status='closed'
                ORDER BY exit_time DESC LIMIT ?
            """, (limit,)).fetchall()
        cols = ["id","instrument","option_type","strike","expiry",
                "lots","qty","entry_premium","exit_premium",
                "pnl","pnl_pct","entry_time","exit_time","exit_reason"]
        return [dict(zip(cols, r)) for r in rows]

    def get_stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM fno_trades WHERE status='closed'"
            ).fetchone()[0]
            wins = conn.execute(
                "SELECT COUNT(*) FROM fno_trades WHERE status='closed' AND pnl>0"
            ).fetchone()[0]
            total_pnl = conn.execute(
                "SELECT COALESCE(SUM(pnl),0) FROM fno_trades WHERE status='closed'"
            ).fetchone()[0]
            open_count = conn.execute(
                "SELECT COUNT(*) FROM fno_trades WHERE status='open'"
            ).fetchone()[0]
        return {
            "total": total, "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "total_pnl": round(total_pnl, 2),
            "open_positions": open_count,
        }

    # ------------------------------------------------------------------
    # Options selling (straddle / strangle)
    # ------------------------------------------------------------------
    def open_selling_position(self, index: str, ce_strike: int, pe_strike: int,
                              ce_premium: float, pe_premium: float,
                              expiry: str, lots: int = 1,
                              strategy: str = "STRADDLE",
                              reasoning: str = "") -> tuple[int | None, int | None]:
        """
        Open a paper options SELL position (straddle or strangle).
        Returns (ce_trade_id, pe_trade_id).
        P&L is inverted: premium decay = profit for seller.
        """
        lot_size = LOT_SIZES.get(index, 25)
        qty = lots * lot_size

        ce_id = pe_id = None
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO fno_trades
                (instrument, option_type, strike, expiry, lot_size, lots, qty,
                 entry_premium, current_premium, entry_time, status, reasoning)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (index, f"SELL-CE-{strategy}", ce_strike, expiry,
                  lot_size, lots, qty, ce_premium, ce_premium,
                  datetime.now().isoformat(), "open",
                  f"SELL {strategy} CE leg | {reasoning}"))
            ce_id = cur.lastrowid

            cur = conn.execute("""
                INSERT INTO fno_trades
                (instrument, option_type, strike, expiry, lot_size, lots, qty,
                 entry_premium, current_premium, entry_time, status, reasoning)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (index, f"SELL-PE-{strategy}", pe_strike, expiry,
                  lot_size, lots, qty, pe_premium, pe_premium,
                  datetime.now().isoformat(), "open",
                  f"SELL {strategy} PE leg | {reasoning}"))
            pe_id = cur.lastrowid

        logger.info(f"SELL OPEN: {index} {strategy} | "
                    f"CE {ce_strike} @ Rs.{ce_premium} + PE {pe_strike} @ Rs.{pe_premium} | "
                    f"Collect Rs.{(ce_premium+pe_premium)*qty:,.0f}")
        return ce_id, pe_id

    def monitor_selling(self) -> list[dict]:
        """
        Update P&L for open SELL positions.
        Seller profits when premium decays. SL = current premium doubles entry.
        """
        closed = []
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT id, instrument, option_type, strike, expiry,
                       lot_size, lots, qty, entry_premium
                FROM fno_trades
                WHERE status='open' AND option_type LIKE 'SELL-%'
            """).fetchall()

        today = datetime.now().strftime("%d-%b-%Y")
        for row in rows:
            trade_id, index, opt_type, strike, expiry, lot_size, lots, qty, entry_prem = row
            # Get option type from SELL-CE-STRADDLE format
            opt = "CE" if "-CE-" in opt_type else "PE"
            curr = self.chain.get_premium(index, strike, opt)
            if not curr:
                continue

            # For seller: pnl = (entry - current) * qty (profit when premium falls)
            pnl = round((entry_prem - curr) * qty, 2)
            with self._conn() as conn:
                conn.execute(
                    "UPDATE fno_trades SET current_premium=?, pnl=? WHERE id=?",
                    (curr, pnl, trade_id)
                )

            reason = None
            if curr >= entry_prem * 2.0:
                reason = "SL_HIT"     # premium doubled — buy back, take loss
            elif curr <= entry_prem * 0.20:
                reason = "TP_HIT"     # 80% premium decay — buy back, take profit
            elif expiry <= today:
                reason = "EXPIRY"

            if reason:
                result = self.close_position(trade_id, curr, reason)
                if result:
                    # Invert P&L for display (seller profits on decay)
                    result["pnl"]     = round((entry_prem - curr) * qty, 2)
                    result["pnl_pct"] = round((entry_prem - curr) / entry_prem * 100, 2)
                    closed.append(result)
        return closed

    # ------------------------------------------------------------------
    # Futures paper trading
    # ------------------------------------------------------------------
    def open_futures(self, index: str, direction: str, expiry: str,
                     lots: int = 1, reasoning: str = "") -> int | None:
        """
        Open a paper futures position.
        direction: "LONG" or "SHORT"
        Returns trade_id or None.
        """
        from data.nse_futures import get_futures_price, get_margin_required, LOT_SIZES
        price = get_futures_price(index)
        if not price:
            logger.warning(f"Cannot fetch futures price for {index}")
            return None

        lot_size = LOT_SIZES.get(index, 75)
        qty      = lots * lot_size
        margin   = get_margin_required(index, price, lots)

        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO fno_trades
                (instrument, option_type, strike, expiry, lot_size, lots, qty,
                 entry_premium, current_premium, entry_time, status, reasoning)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (index, f"FUT-{direction}", 0, expiry,
                  lot_size, lots, qty,
                  price, price,
                  datetime.now().isoformat(), "open", reasoning))
            trade_id = cur.lastrowid

        logger.info(f"FUT OPEN: {index} {direction} | "
                    f"Price {price:,.0f} × {qty} | Margin Rs.{margin:,.0f} | id={trade_id}")
        return trade_id

    def monitor_futures(self) -> list[dict]:
        """Update P&L for open futures positions. Exits on 2% adverse move (SL)."""
        closed = []
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT id, instrument, option_type, entry_premium, qty, expiry
                FROM fno_trades
                WHERE status='open' AND option_type LIKE 'FUT-%'
            """).fetchall()

        today = datetime.now().strftime("%d-%b-%Y")
        for row in rows:
            trade_id, index, opt_type, entry_price, qty, expiry = row
            direction = opt_type.replace("FUT-", "")

            from data.nse_futures import get_futures_price
            curr = get_futures_price(index)
            if not curr:
                continue

            if direction == "LONG":
                pnl = (curr - entry_price) * qty
                chg_pct = (curr - entry_price) / entry_price * 100
            else:
                pnl = (entry_price - curr) * qty
                chg_pct = (entry_price - curr) / entry_price * 100

            with self._conn() as conn:
                conn.execute(
                    "UPDATE fno_trades SET current_premium=?, pnl=? WHERE id=?",
                    (curr, round(pnl, 2), trade_id)
                )

            reason = None
            if chg_pct <= -2.0:
                reason = "SL_HIT"     # 2% adverse move
            elif chg_pct >= 3.0:
                reason = "TP_HIT"     # 3% favourable
            elif expiry <= today:
                reason = "EXPIRY"

            if reason:
                result = self.close_position(trade_id, curr, reason)
                if result:
                    closed.append(result)
        return closed

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _conn(self):
        return sqlite3.connect(self.db)

    def _init_table(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fno_trades (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    instrument       TEXT NOT NULL,   -- NIFTY | BANKNIFTY
                    option_type      TEXT NOT NULL,   -- CE | PE | FUT-LONG | FUT-SHORT
                    strike           INTEGER NOT NULL,
                    expiry           TEXT NOT NULL,
                    lot_size         INTEGER,
                    lots             INTEGER DEFAULT 1,
                    qty              INTEGER,         -- lots * lot_size
                    entry_premium    REAL,
                    current_premium  REAL,
                    exit_premium     REAL,
                    entry_time       TEXT,
                    exit_time        TEXT,
                    pnl              REAL DEFAULT 0,
                    pnl_pct          REAL DEFAULT 0,
                    status           TEXT DEFAULT 'open',  -- open | closed
                    exit_reason      TEXT,           -- TP_HIT | SL_HIT | EXPIRY | MANUAL
                    reasoning        TEXT
                )
            """)
