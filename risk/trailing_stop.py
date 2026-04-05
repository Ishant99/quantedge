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
from datetime import datetime
from config import VIRTUAL_PORTFOLIO_FILE, TRADING_MODE, TRAIL_PCT, SQLITE_DB_FILE
from execution.portfolio_lock import load_portfolio_locked, save_portfolio_locked
from utils import get_logger
from utils.telegram import send

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
                    logger.info(f"{symbol}: SL moved UP "
                                f"Rs.{result['old_sl']:,.0f} -> Rs.{result['new_sl']:,.0f} "
                                f"(price: Rs.{result['current_price']:,.0f})")
                    send(f"*Trailing SL Update*\n"
                         f"{symbol}: Stop loss moved UP\n"
                         f"Price: `Rs.{result['current_price']:,.0f}`\n"
                         f"New SL: `Rs.{result['new_sl']:,.0f}` "
                         f"(was Rs.{result['old_sl']:,.0f})\n"
                         f"Locked profit: `Rs.{result['locked_profit']:,.0f}`")

                elif result["action"] == "EXIT":
                    exits.append(symbol)
                    logger.info(f"{symbol}: STOP LOSS HIT at Rs.{result['current_price']:,.0f} "
                                f"(SL was Rs.{pos['stop_loss']:,.0f})")
                    send(f"*Stop Loss Hit*\n"
                         f"{symbol}: Position closed\n"
                         f"Exit price: `Rs.{result['current_price']:,.0f}`\n"
                         f"P&L: `Rs.{result['pnl']:+,.0f}`")

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
            self._close_trade_sqlite(sym, exit_price)

        portfolio["positions"] = positions
        save_portfolio_locked(VIRTUAL_PORTFOLIO_FILE, portfolio)

        if updates:
            logger.info(f"Trailing stop update: {len(updates)} positions checked, "
                        f"{len(exits)} exited, "
                        f"{sum(1 for u in updates.values() if u['action']=='MOVE_SL')} SLs moved")
        return updates

    def _close_trade_sqlite(self, symbol: str, exit_price: float):
        """Close the open trade record in SQLite so it doesn't become an orphan."""
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
                    trade_id, entry, qty = row
                    pnl     = round((exit_price - entry) * qty, 2)
                    pnl_pct = round((exit_price - entry) / entry * 100, 2) if entry else 0
                    conn.execute("""
                        UPDATE trades SET exit_price=?, exit_time=?,
                        pnl=?, pnl_pct=?, status='closed' WHERE id=?
                    """, (round(exit_price, 2), datetime.now().isoformat(),
                          pnl, pnl_pct, trade_id))
                    logger.debug(f"SQLite: closed trade #{trade_id} for {symbol} "
                                 f"(trailing stop) P&L Rs.{pnl:+,.0f}")
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

            # Calculate trailing SL
            # New SL = current price * (1 - TRAIL_PCT)
            # Only move UP — never lower the SL
            new_sl = round(current * (1 - TRAIL_PCT), 2)

            if new_sl > sl:
                locked = (new_sl - entry) * qty
                return {
                    "action":        "MOVE_SL",
                    "current_price": round(current, 2),
                    "old_sl":        round(sl, 2),
                    "new_sl":        new_sl,
                    "locked_profit": round(locked, 2),
                    "gain_pct":      round((current - entry) / entry * 100, 2),
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
