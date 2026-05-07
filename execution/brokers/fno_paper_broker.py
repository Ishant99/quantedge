# =============================================================================
# execution/brokers/fno_paper_broker.py — F&O Paper Trading Broker
#
# Simulates buying/selling Nifty & BankNifty options (CE/PE).
# Positions stored in SQLite fno_trades table.
# Exit triggers (long): premium 2x (TP) | premium -30% (SL) | expiry
# Exit triggers (sell): premium decay 80% (TP) | premium +50% (SL) | expiry
# Guards: position cap, daily loss circuit breaker, same-day index dup, min DTE
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3
from datetime import datetime
from config import (SQLITE_DB_FILE, FNO_LOT_SIZES, FNO_TP_MULT, FNO_SL_MULT, FNO_MAX_POSITIONS,
    FNO_HV_CIRCUIT_BREAKER_PCT, FNO_SL_COOLDOWN_HOURS,
    FNO_SELL_SL_MULT, FNO_SELL_SL_MULT_HIGH_VOL, FNO_SELL_SL_HV_THRESHOLD,
    FUTURES_SL_PCT, FUTURES_TP_PCT)
from data.nse_options_chain import NSEOptionsChain
from services.paper_treasury import (
    can_allocate,
    log_treasury_event,
    reserve_for_fno_order,
    write_treasury_snapshot,
)
from utils import get_logger
import settings.manager as S

logger   = get_logger("FNOPaperBroker")
TP_MULT  = FNO_TP_MULT
SL_MULT  = FNO_SL_MULT
LOT_SIZES = FNO_LOT_SIZES


class FNOPaperBroker:

    def _cfg(self, key: str, default=None):
        return S.get(key, default)

    def _structure_key(self, row: dict) -> str:
        option_type = str(row.get("option_type", "")).upper()
        instrument = str(row.get("instrument", "")).upper()
        strike = int(row.get("strike", 0) or 0)
        expiry = str(row.get("expiry", "") or "")
        if option_type.startswith("SELL-"):
            return f"{instrument}:{option_type.split('-')[-1]}:{strike}:{expiry}"
        if option_type.startswith("FUT-"):
            return f"{instrument}:{option_type}:{expiry}"
        return f"{instrument}:{option_type}:{strike}:{expiry}"

    def _open_rows(self, index: str | None = None) -> list[dict]:
        rows = self.get_open_positions()
        if index:
            rows = [row for row in rows if str(row.get("instrument", "")).upper() == str(index).upper()]
        return rows

    def _structure_count(self, index: str) -> int:
        return len({self._structure_key(row) for row in self._open_rows(index=index)})

    def _has_open_short_structure(self, index: str) -> bool:
        return any(str(row.get("option_type", "")).upper().startswith("SELL-") for row in self._open_rows(index=index))

    def _underlying_limit_pct(self, index: str) -> float:
        return float(self._cfg(f"FNO_MAX_UNDERLYING_EXPOSURE_{str(index).upper()}_PCT", 0.15) or 0.15)

    def _check_underlying_risk(self, index: str, reserve_inr: float) -> tuple[bool, str]:
        from config import VIRTUAL_CAPITAL

        max_structures = int(self._cfg("FNO_MAX_STRUCTURES_PER_UNDERLYING", 2) or 2)
        if self._structure_count(index) >= max_structures:
            return False, f"{index} structure cap reached ({max_structures})"

        existing_reserve = sum(
            reserve_for_fno_order(
                index=index,
                option_type=str(row.get("option_type", "")),
                entry_price=float(row.get("entry_premium", 0) or 0),
                qty=float(row.get("qty", 0) or 0),
            )
            for row in self._open_rows(index=index)
        )
        max_allowed = float(VIRTUAL_CAPITAL) * self._underlying_limit_pct(index)
        if existing_reserve + reserve_inr > max_allowed:
            return False, f"{index} exposure cap exceeded (limit Rs.{max_allowed:,.0f})"
        return True, ""

    def _within_position_cap(self, additional_positions: int = 1) -> tuple[bool, str]:
        open_count = self.get_stats()["open_positions"]
        if open_count + additional_positions > FNO_MAX_POSITIONS:
            return False, f"F&O position limit reached ({FNO_MAX_POSITIONS})"
        return True, ""

    def _check_same_day_index_duplicate(self, index: str, direction_type: str) -> tuple[bool, str]:
        """
        Block opening a second directional position on the same index on the
        same day.  direction_type is one of: "LONG_OPT" (buy CE/PE),
        "SELL_VOL" (straddle/strangle), "FUT" (futures).

        Same-day is based on entry_time date.  Only same direction_type is
        blocked (e.g. two LONG_OPT entries on NIFTY in one day → blocked,
        but LONG_OPT + SELL_VOL → allowed).

        Controlled by FNO_BLOCK_SAME_DAY_INDEX (default True).
        """
        if not bool(self._cfg("FNO_BLOCK_SAME_DAY_INDEX", True)):
            return True, ""
        today_iso = datetime.now().strftime("%Y-%m-%d")
        try:
            with self._conn() as conn:
                rows = conn.execute("""
                    SELECT option_type FROM fno_trades
                    WHERE instrument=? AND entry_time >= ? AND entry_time < ?
                      AND status IN ('open','closed')
                """, (index.upper(), today_iso, today_iso + "T99")).fetchall()
        except Exception:
            return True, ""

        for (opt,) in rows:
            opt_up = str(opt or "").upper()
            existing_type = None
            if opt_up.startswith("SELL-"):
                existing_type = "SELL_VOL"
            elif opt_up.startswith("FUT-"):
                existing_type = "FUT"
            else:
                existing_type = "LONG_OPT"
            if existing_type == direction_type:
                return False, (
                    f"Same-day duplicate block: already have a {direction_type} "
                    f"entry on {index} today"
                )
        return True, ""

    def _check_min_dte(self, expiry: str, min_dte: int = None) -> tuple[bool, str]:
        """
        Block long option entries where days-to-expiry < FNO_MIN_LONG_DTE
        (default 2).  0/1 DTE long positions are pure gamma gambles with
        a negative EV for an automated agent.
        """
        if min_dte is None:
            min_dte = int(self._cfg("FNO_MIN_LONG_DTE", 2) or 0)
        if min_dte <= 0:
            return True, ""
        try:
            expiry_dt = datetime.strptime(expiry, "%d-%b-%Y").date()
            today_dt  = datetime.now().date()
            dte = (expiry_dt - today_dt).days
            if dte < min_dte:
                return False, (
                    f"DTE too low for long options ({dte}d < min {min_dte}d) — "
                    f"expiry {expiry}"
                )
        except Exception:
            pass
        return True, ""

    def _within_daily_loss_limit(self) -> tuple[bool, str]:
        """
        Circuit breaker: block new F&O entries if today's realized F&O P&L is
        already below -FNO_DAILY_LOSS_LIMIT_PCT * VIRTUAL_CAPITAL.
        Default 3% (Rs.30k on Rs.10L book). Disable by setting pct <= 0.
        """
        try:
            from config import VIRTUAL_CAPITAL
            limit_pct = float(self._cfg("FNO_DAILY_LOSS_LIMIT_PCT", 3.0) or 0)
            if limit_pct <= 0:
                return True, ""
            limit_abs = VIRTUAL_CAPITAL * limit_pct / 100.0
            today_iso = datetime.now().strftime("%Y-%m-%d")
            with self._conn() as conn:
                row = conn.execute("""
                    SELECT COALESCE(SUM(pnl), 0)
                    FROM fno_trades
                    WHERE status='closed' AND exit_time >= ? AND exit_time < ?
                """, (today_iso, today_iso + "T99")).fetchone()
            day_pnl = float(row[0] or 0)
            if day_pnl <= -limit_abs:
                return False, (
                    f"F&O daily loss circuit breaker tripped "
                    f"(day P&L Rs.{day_pnl:+,.0f} <= -Rs.{limit_abs:,.0f}, "
                    f"limit {limit_pct:.1f}% of capital)"
                )
        except Exception as e:
            logger.warning(f"Daily loss check failed, allowing entry: {e}")
        return True, ""


    def _check_hv_circuit_breaker(self, index):
        """Block new F&O entries when 20d HV exceeds FNO_HV_CIRCUIT_BREAKER_PCT."""
        try:
            import yfinance as yf, numpy as np, math
            limit = float(self._cfg("FNO_HV_CIRCUIT_BREAKER_PCT", FNO_HV_CIRCUIT_BREAKER_PCT) or 0)
            if limit <= 0:
                return True, ""
            ticker = "^NSEI" if index.upper() == "NIFTY" else "^NSEBANK"
            df = yf.Ticker(ticker).history(period="2mo", interval="1d", auto_adjust=True)
            if df.empty or len(df) < 22:
                return True, ""
            close = df["Close"]
            log_ret = np.log(close / close.shift(1)).dropna()
            hv_20 = float(log_ret.tail(20).std() * math.sqrt(252) * 100)
            if hv_20 > limit:
                return False, (
                    f"HV circuit breaker: {index} 20d HV={hv_20:.1f}% "
                    f"exceeds limit {limit:.1f}% -- no new F&O entries"
                )
        except Exception as e:
            logger.warning(f"HV circuit breaker check failed ({index}), allowing: {e}")
        return True, ""

    def _current_hv(self, index):
        """Return current 20-day annualised HV% for the index. Returns 0.0 on failure."""
        try:
            import yfinance as yf, numpy as np, math
            ticker = "^NSEI" if index.upper() == "NIFTY" else "^NSEBANK"
            df = yf.Ticker(ticker).history(period="2mo", interval="1d", auto_adjust=True)
            if df.empty or len(df) < 22:
                return 0.0
            close = df["Close"]
            log_ret = np.log(close / close.shift(1)).dropna()
            return float(log_ret.tail(20).std() * math.sqrt(252) * 100)
        except Exception:
            return 0.0

    def _check_sl_cooldown(self, index, direction_type):
        """Block re-entry on same index+direction_type for FNO_SL_COOLDOWN_HOURS after SL_HIT."""
        try:
            cooldown_h = int(self._cfg("FNO_SL_COOLDOWN_HOURS", FNO_SL_COOLDOWN_HOURS) or 0)
            if cooldown_h <= 0:
                return True, ""
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(hours=cooldown_h)).isoformat()
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT option_type, exit_time FROM fno_trades "
                    "WHERE instrument=? AND exit_reason='SL_HIT' AND exit_time >= ? "
                    "ORDER BY exit_time DESC",
                    (index.upper(), cutoff)
                ).fetchall()
            for (opt, exit_t) in rows:
                opt_up = str(opt or "").upper()
                if opt_up.startswith("SELL-"):
                    ex_type = "SELL_VOL"
                elif opt_up.startswith("FUT-"):
                    ex_type = "FUT"
                else:
                    ex_type = "LONG_OPT"
                if ex_type == direction_type:
                    return False, (
                        f"SL cooldown: {index} {direction_type} had SL_HIT at {exit_t} "
                        f"-- waiting {cooldown_h}h before re-entry"
                    )
        except Exception as e:
            logger.warning(f"SL cooldown check failed ({index}), allowing: {e}")
        return True, ""

    def _is_expired(self, expiry: str, today: datetime | None = None) -> bool:
        """Robust expiry comparison for values like 03-Apr-2026."""
        try:
            expiry_dt = datetime.strptime(expiry, "%d-%b-%Y").date()
            today_dt = (today or datetime.now()).date()
            return expiry_dt <= today_dt
        except Exception:
            logger.warning(f"Could not parse expiry '{expiry}'")
            return False

    def __init__(self):
        self.db  = SQLITE_DB_FILE
        self.chain = NSEOptionsChain()
        self._init_table()
        self._backfill_short_pnl_sign()

    # ------------------------------------------------------------------
    # One-shot migration: fix inverted P&L sign on historical short trades
    # ------------------------------------------------------------------
    def _backfill_short_pnl_sign(self) -> None:
        """
        Before the close_position() sign fix, every closed SELL-* and
        FUT-SHORT trade had pnl written with the long-perspective formula,
        producing losses labelled as wins and vice versa.

        This migration re-derives pnl / pnl_pct from entry_premium and
        exit_premium for every such row, then stamps a settings flag so
        it never runs twice.
        """
        try:
            if S.get("FNO_SHORT_PNL_SIGN_BACKFILLED", False):
                return
        except Exception:
            pass

        try:
            with self._conn() as conn:
                rows = conn.execute("""
                    SELECT id, option_type, entry_premium, exit_premium, qty, pnl
                    FROM fno_trades
                    WHERE status='closed'
                      AND (option_type LIKE 'SELL-%' OR option_type='FUT-SHORT')
                      AND entry_premium IS NOT NULL
                      AND exit_premium  IS NOT NULL
                """).fetchall()

                updated = 0
                for trade_id, opt_type, entry, exit_p, qty, old_pnl in rows:
                    if entry is None or exit_p is None or qty is None:
                        continue
                    try:
                        new_pnl = round((float(entry) - float(exit_p)) * float(qty), 2)
                        new_pct = round((float(entry) - float(exit_p)) / float(entry) * 100, 2) if entry else 0
                    except Exception:
                        continue
                    # Only rewrite rows whose sign actually differs (idempotent)
                    if old_pnl is None or round(float(old_pnl), 2) != new_pnl:
                        conn.execute(
                            "UPDATE fno_trades SET pnl=?, pnl_pct=? WHERE id=?",
                            (new_pnl, new_pct, trade_id),
                        )
                        updated += 1
                conn.commit()
        except Exception as e:
            logger.error(f"F&O P&L sign backfill failed: {e}")
            return

        try:
            S.save({"FNO_SHORT_PNL_SIGN_BACKFILLED": True})
        except Exception:
            pass
        if updated:
            logger.warning(
                f"F&O P&L sign backfill: corrected {updated} historical short "
                f"trades (flipped sign on SELL-*/FUT-SHORT rows)"
            )

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
        ok, reason = self._within_position_cap(1)
        if not ok:
            logger.warning(f"{reason} — skipping {index} {direction}")
            return None
        ok, reason = self._within_daily_loss_limit()
        if not ok:
            logger.warning(f"{reason} — skipping {index} {direction}")
            return None
        ok, reason = self._check_hv_circuit_breaker(index)
        if not ok:
            logger.warning(f"{reason} -- skipping {index} {direction}")
            return None
        ok, reason = self._check_sl_cooldown(index, "LONG_OPT")
        if not ok:
            logger.warning(f"{reason} -- skipping {index} {direction}")
            return None
        ok, reason = self._check_same_day_index_duplicate(index, "LONG_OPT")
        if not ok:
            logger.warning(f"{reason} — skipping {index} {direction}")
            return None
        ok, reason = self._check_min_dte(expiry)
        if not ok:
            logger.warning(f"{reason} — skipping {index} {direction}")
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
        reserve_inr = reserve_for_fno_order(index, opt_type, entry_premium, qty)
        ok, reason, _ = can_allocate("fno", reserve_inr)
        if not ok:
            logger.warning(f"F&O treasury block for {index} {strike}{opt_type}: {reason}")
            return None
        ok, reason = self._check_underlying_risk(index, reserve_inr)
        if not ok:
            logger.warning(f"F&O underlying block for {index} {strike}{opt_type}: {reason}")
            return None

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
        log_treasury_event("reserve_open", "fno", reserve_inr, f"{index} {strike}{opt_type}", {"trade_id": trade_id, "index": index})
        write_treasury_snapshot()

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

        # ── P&L sign by position direction ──────────────────────────────────
        # BUY CE/PE, FUT-LONG  → long perspective : (exit - entry) * qty
        # SELL-*   (short vol) → short perspective: (entry - exit) * qty
        # FUT-SHORT            → short perspective: (entry - exit) * qty
        opt_upper = str(opt_type or "").upper()
        is_short  = opt_upper.startswith("SELL-") or opt_upper == "FUT-SHORT"
        if is_short:
            pnl     = round((entry_prem - exit_premium) * qty, 2)
            pnl_pct = round((entry_prem - exit_premium) / entry_prem * 100, 2)
        else:
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
        reserve_inr = reserve_for_fno_order(index, opt_type, entry_prem, qty)
        log_treasury_event("release_close", "fno", reserve_inr, f"{index} {strike}{opt_type}", {"trade_id": trade_id, "index": index, "pnl": pnl})
        write_treasury_snapshot()

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
            elif self._is_expired(expiry):
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
        reserve_inr = (
            reserve_for_fno_order(index, f"SELL-CE-{strategy}", ce_premium, qty)
            + reserve_for_fno_order(index, f"SELL-PE-{strategy}", pe_premium, qty)
        )
        ok, reason = self._within_position_cap(2)
        if not ok:
            logger.warning(f"{reason} — skipping {index} {strategy}")
            return None, None
        ok, reason = self._within_daily_loss_limit()
        if not ok:
            logger.warning(f"{reason} — skipping {index} {strategy}")
            return None, None
        ok, reason = self._check_hv_circuit_breaker(index)
        if not ok:
            logger.warning(f"{reason} -- skipping {index} {strategy}")
            return None, None
        ok, reason = self._check_sl_cooldown(index, "SELL_VOL")
        if not ok:
            logger.warning(f"{reason} -- skipping {index} {strategy}")
            return None, None
        ok, reason = self._check_same_day_index_duplicate(index, "SELL_VOL")
        if not ok:
            logger.warning(f"{reason} — skipping {index} {strategy}")
            return None, None
        ok, reason, _ = can_allocate("fno", reserve_inr)
        if not ok:
            logger.warning(f"F&O treasury block for {index} {strategy}: {reason}")
            return None, None
        ok, reason = self._check_underlying_risk(index, reserve_inr)
        if not ok:
            logger.warning(f"F&O underlying block for {index} {strategy}: {reason}")
            return None, None

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
        log_treasury_event("reserve_open", "fno", reserve_inr, f"{index} {strategy}", {"ce_trade_id": ce_id, "pe_trade_id": pe_id, "index": index})
        write_treasury_snapshot()

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

            hv_now = self._current_hv(index)
            hv_threshold = float(self._cfg("FNO_SELL_SL_HV_THRESHOLD", FNO_SELL_SL_HV_THRESHOLD) or 20.0)
            if hv_now > hv_threshold:
                sell_sl = float(self._cfg("FNO_SELL_SL_MULT_HIGH_VOL", FNO_SELL_SL_MULT_HIGH_VOL) or 2.0)
            else:
                sell_sl = float(self._cfg("FNO_SELL_SL_MULT", FNO_SELL_SL_MULT) or 1.5)
            reason = None
            if curr >= entry_prem * sell_sl:
                reason = "SL_HIT"     # premium rose past SL multiplier — buy back, take loss
            elif curr <= entry_prem * 0.20:
                reason = "TP_HIT"     # 80% premium decay — buy back, take profit
            elif self._is_expired(expiry):
                reason = "EXPIRY"

            if reason:
                result = self.close_position(trade_id, curr, reason)
                if result:
                    # close_position() now writes short-perspective P&L
                    # directly (see is_short branch), so no inversion needed.
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
        ok, reason = self._within_position_cap(1)
        if not ok:
            logger.warning(f"{reason} — skipping {index} FUT-{direction}")
            return None
        ok, reason = self._within_daily_loss_limit()
        if not ok:
            logger.warning(f"{reason} — skipping {index} FUT-{direction}")
            return None
        ok, reason = self._check_hv_circuit_breaker(index)
        if not ok:
            logger.warning(f"{reason} -- skipping {index} FUT-{direction}")
            return None
        ok, reason = self._check_sl_cooldown(index, "FUT")
        if not ok:
            logger.warning(f"{reason} -- skipping {index} FUT-{direction}")
            return None
        ok, reason = self._check_same_day_index_duplicate(index, "FUT")
        if not ok:
            logger.warning(f"{reason} — skipping {index} FUT-{direction}")
            return None
        reserve_inr = reserve_for_fno_order(index, f"FUT-{direction}", price, qty)
        ok, reason, _ = can_allocate("fno", reserve_inr)
        if not ok:
            logger.warning(f"F&O treasury block for {index} FUT-{direction}: {reason}")
            return None
        if direction.upper() == "SHORT" and bool(self._cfg("FNO_BLOCK_DUPLICATE_FUT_SHORT_WITH_STRADDLE", True)):
            if self._has_open_short_structure(index):
                logger.warning(f"F&O duplicate short block for {index}: open short-vol structure already exists")
                return None
        ok, reason = self._check_underlying_risk(index, reserve_inr)
        if not ok:
            logger.warning(f"F&O underlying block for {index} FUT-{direction}: {reason}")
            return None

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
        log_treasury_event("reserve_open", "fno", reserve_inr, f"{index} FUT-{direction}", {"trade_id": trade_id, "index": index})
        write_treasury_snapshot()

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
            if chg_pct <= -(FUTURES_SL_PCT * 100):
                reason = "SL_HIT"
            elif chg_pct >= (FUTURES_TP_PCT * 100):
                reason = "TP_HIT"
            elif self._is_expired(expiry):
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
