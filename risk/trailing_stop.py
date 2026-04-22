# =============================================================================
# risk/trailing_stop.py — Trailing Stop Loss Monitor
#
# Runs after every agent session. For each open paper position:
#   - Fetches current price
#   - If price rose significantly, moves stop loss UP
#   - If stop loss is hit, marks position for exit
#   - Sends Telegram alert when SL is moved or hit
#
# Usage: python -m risk.trailing_stop
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import yfinance as yf
from datetime import datetime, timezone
from config import VIRTUAL_PORTFOLIO_FILE, TRADING_MODE, TRAIL_PCT, SQLITE_DB_FILE, HOLD_DAYS_MAX
from execution.portfolio_lock import load_portfolio_locked, save_portfolio_locked
from utils import get_logger
from utils.telegram import send
from utils.alert_formatter import sl_alert_allowed

logger = get_logger("TrailingStop")


class TrailingStopMonitor:
    """
    Monitors open paper positions and adjusts stop losses upward
    as stocks move in our favour — locking in profits.
    """

    def run(self) -> dict:
        """Check all open positions and update trailing stops."""
        if not os.path.exists(VIRTUAL_PORTFOLIO_FILE):
            logger.info("No virtual portfolio found — nothing to monitor")
            return {}

        portfolio = load_portfolio_locked(VIRTUAL_PORTFOLIO_FILE)
        if not portfolio:
            return {}

        positions = portfolio.get("positions", {})
        if not positions:
            logger.info("No open positions to monitor")
            return {}

        logger.info(f"Checking trailing stops for {len(positions)} positions...")
        updates   = {}
        exits     = []

        for symbol, pos in positions.items():
            result = self._check_position(symbol, pos)
            if result:
                updates[symbol] = result
                if result["action"] == "MOVE_SL":
                    positions[symbol]["stop_loss"] = result["new_sl"]
                    moved_up  = round(result["new_sl"] - result["old_sl"], 2)
                    locked    = result["locked_profit"]
                    reason    = result.get("reason", "")
                    logger.info(f"{symbol}: SL moved UP "
                                f"₹{result['old_sl']:,.0f} → ₹{result['new_sl']:,.0f} "
                                f"(price: ₹{result['current_price']:,.0f})")

                    if reason == "breakeven":
                        msg = (
                            f"📈 *Stop Loss at Breakeven — {symbol}*\n"
                            f"Position is up {result.get('gain_pct', 0):.1f}% — "
                            f"stop loss moved to your entry price.\n"
                            f"Current price: `₹{result['current_price']:,.0f}`\n"
                            f"Stop loss now: `₹{result['new_sl']:,.0f}` (your entry)\n"
                            f"_This trade can no longer lose money._"
                        )
                    elif locked >= 0:
                        msg = (
                            f"📈 *Stop Loss Raised — {symbol}*\n"
                            f"Your stop loss moved up by ₹{moved_up:,.0f}.\n"
                            f"Current price: `₹{result['current_price']:,.0f}`\n"
                            f"New stop loss: `₹{result['new_sl']:,.0f}` (was ₹{result['old_sl']:,.0f})\n"
                            f"✅ `₹{locked:+,.0f}` profit now protected"
                        )
                    else:
                        msg = (
                            f"📈 *Stop Loss Raised — {symbol}*\n"
                            f"Current price: `₹{result['current_price']:,.0f}`\n"
                            f"New stop loss: `₹{result['new_sl']:,.0f}` (was ₹{result['old_sl']:,.0f})"
                        )
                    send(msg)

                elif result["action"] == "EXIT":
                    exits.append(symbol)
                    pnl = result.get("pnl", 0)
                    logger.info(f"{symbol}: STOP LOSS HIT at ₹{result['current_price']:,.0f} "
                                f"(SL was ₹{pos['stop_loss']:,.0f})")
                    icon = "✅" if pnl >= 0 else "🔴"
                    send(
                        f"{icon} *Stop Loss Hit — {symbol}*\n"
                        f"Position automatically closed at your stop loss.\n"
                        f"Exit price: `₹{result['current_price']:,.0f}`\n"
                        f"P&L: `₹{pnl:+,.0f}`"
                    )

        # Remove exited positions + close in SQLite
        for sym in exits:
            exit_price = updates[sym].get("current_price", positions[sym]["stop_loss"])
            entry      = positions[sym]["entry"]
            qty        = positions[sym]["qty"]
            pnl        = (exit_price - entry) * qty
            portfolio["cash"] += exit_price * qty
            portfolio["total_trades"] = portfolio.get("total_trades", 0) + 1
            if pnl > 0:
                portfolio["wins"] = portfolio.get("wins", 0) + 1
            del positions[sym]

            # Close the matching trade in SQLite (fixes orphan bug)
            self._close_trade_sqlite(sym, exit_price, entry_price=entry, qty=qty)

        portfolio["positions"] = positions
        save_portfolio_locked(VIRTUAL_PORTFOLIO_FILE, portfolio)

        if updates:
            logger.info(f"Trailing stop update: {len(updates)} positions checked, "
                        f"{len(exits)} exited, "
                        f"{sum(1 for u in updates.values() if u['action']=='MOVE_SL')} SLs moved")
        return updates

    def _close_trade_sqlite(self, symbol: str, exit_price: float,
                            entry_price: float = 0, qty: int = 0):
        """Close the open trade record in SQLite so it doesn't become an orphan.
        Falls back to inserting a synthetic closed record if no open row exists."""
        try:
            import sqlite3
            if not os.path.exists(SQLITE_DB_FILE):
                return
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                row = conn.execute(
                    "SELECT id, entry_price, qty FROM trades "
                    "WHERE symbol=? AND status='open' ORDER BY id DESC LIMIT 1",
                    (symbol,)
                ).fetchone()
                if row:
                    trade_id, entry, db_qty = row
                    pnl     = round((exit_price - entry) * db_qty, 2)
                    pnl_pct = round((exit_price - entry) / entry * 100, 2) if entry else 0
                    conn.execute("""
                        UPDATE trades SET exit_price=?, exit_time=?,
                        pnl=?, pnl_pct=?, status='closed' WHERE id=?
                    """, (round(exit_price, 2), datetime.now().isoformat(),
                          pnl, pnl_pct, trade_id))
                    logger.debug(f"SQLite: closed trade #{trade_id} for {symbol} "
                                 f"(trailing stop) P&L Rs.{pnl:+,.0f}")
                elif entry_price and qty:
                    # Position existed in portfolio JSON but not SQLite — insert synthetic record
                    pnl     = round((exit_price - entry_price) * qty, 2)
                    pnl_pct = round((exit_price - entry_price) / entry_price * 100, 2) if entry_price else 0
                    conn.execute("""
                        INSERT INTO trades
                        (symbol, action, qty, entry_price, exit_price,
                         entry_time, exit_time, pnl, pnl_pct, status)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (symbol, "SL_HIT", qty,
                          round(entry_price, 2), round(exit_price, 2),
                          datetime.now().strftime("%Y-%m-%d"),
                          datetime.now().isoformat(),
                          pnl, pnl_pct, "closed"))
                    logger.debug(f"SQLite: inserted synthetic trailing-stop exit for {symbol} "
                                 f"P&L Rs.{pnl:+,.0f}")
        except Exception as e:
            logger.warning(f"SQLite trailing stop close failed for {symbol}: {e}")

    def _check_position(self, symbol: str, pos: dict) -> dict | None:
        """Check one position and return action if needed."""
        try:
            bare = symbol.replace("INTRA:", "")
            ticker = yf.Ticker(f"{bare}.NS")
            hist   = ticker.history(period="2d", interval="1d")
            if hist.empty:
                return None

            current = float(hist["Close"].iloc[-1])
            entry   = pos["entry"]
            sl      = pos["stop_loss"]
            qty     = pos["qty"]

            # Stop loss hit
            if current <= sl:
                pnl = (current - entry) * qty
                return {
                    "action":        "EXIT",
                    "current_price": round(current, 2),
                    "exit_price":    round(sl, 2),
                    "pnl":           round(pnl, 2),
                }

            # --- Time-based exit: auto-close positions held too long ---
            try:
                ts_str   = pos.get("timestamp", "")
                if ts_str:
                    entry_dt = datetime.fromisoformat(ts_str)
                    # make both naive for comparison
                    now_dt   = datetime.now()
                    if entry_dt.tzinfo:
                        entry_dt = entry_dt.replace(tzinfo=None)
                    held_days = (now_dt - entry_dt).days
                    if held_days >= HOLD_DAYS_MAX:
                        pnl = (current - entry) * qty
                        logger.info(
                            f"{symbol}: time-based exit after {held_days}d "
                            f"(max {HOLD_DAYS_MAX}d) | PnL ₹{pnl:+,.0f}"
                        )
                        return {
                            "action":        "EXIT",
                            "reason":        f"held {held_days}d ≥ HOLD_DAYS_MAX {HOLD_DAYS_MAX}d",
                            "current_price": round(current, 2),
                            "exit_price":    round(current, 2),
                            "pnl":           round(pnl, 2),
                        }
            except Exception:
                pass

            # --- Breakeven trailing: move SL to entry when gain ≥ 2% ---
            gain_pct = (current - entry) / entry * 100
            if gain_pct >= 2.0 and sl < entry:
                logger.info(f"{symbol}: breakeven SL triggered (+{gain_pct:.1f}%) — moving SL to entry ₹{entry}")
                return {
                    "action":        "MOVE_SL",
                    "current_price": round(current, 2),
                    "old_sl":        round(sl, 2),
                    "new_sl":        round(entry, 2),
                    "locked_profit": 0.0,
                    "gain_pct":      round(gain_pct, 2),
                    "reason":        "breakeven",
                }

            # --- Approaching SL alert: warn when within 2% of stop ---
            sl_gap_pct = (current - sl) / max(current, 1) * 100
            if 0 < sl_gap_pct <= 2.0:
                try:
                    if sl_alert_allowed(symbol):
                        loss_if_hit = round((sl - entry) * qty, 0)
                        send(
                            f"⚠️ *Stop Loss Warning — {symbol}*\n"
                            f"Price is very close to your stop loss.\n"
                            f"Current price: `₹{current:,.2f}`\n"
                            f"Stop loss at:  `₹{sl:,.2f}` (only {sl_gap_pct:.1f}% away)\n"
                            f"If stop hits:  `₹{loss_if_hit:+,.0f}` loss"
                        )
                except Exception:
                    pass

            # Tighten trail as the gain grows — outsized winners get a tighter leash
            # so a late reversal can't wipe out most of the profit.
            if gain_pct >= 20:
                trail_pct = 0.010
            elif gain_pct >= 10:
                trail_pct = 0.015
            else:
                trail_pct = TRAIL_PCT

            new_sl = max(round(current * (1 - trail_pct), 2), entry)

            if new_sl > sl:
                locked = (new_sl - entry) * qty
                return {
                    "action":        "MOVE_SL",
                    "current_price": round(current, 2),
                    "old_sl":        round(sl, 2),
                    "new_sl":        new_sl,
                    "locked_profit": round(locked, 2),
                    "gain_pct":      round(gain_pct, 2),
                }

            # No action needed
            return {
                "action":        "HOLD",
                "current_price": round(current, 2),
                "sl":            round(sl, 2),
                "unrealised_pnl":round((current - entry) * qty, 2),
            }

        except Exception as e:
            logger.debug(f"Trailing stop check failed for {symbol}: {e}")
            return None


if __name__ == "__main__":
    monitor = TrailingStopMonitor()
    results = monitor.run()
    if results:
        print("\nPosition Status:")
        for sym, r in results.items():
            print(f"  {sym}: {r['action']} | Price: Rs.{r.get('current_price',0):,.0f}")
    else:
        print("No open positions.")
