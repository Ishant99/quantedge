# =============================================================================
# execution/price_monitor.py — Intraday Price Monitor
#
# Runs every 15 minutes during market hours (9:15 AM – 3:25 PM IST).
# For every open position:
#   - Fetches current price via yfinance
#   - Checks if stop loss or take profit is hit
#   - Auto-closes position and logs P&L
#   - Updates trailing stop
#   - Sends Telegram alert on every exit
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import sqlite3
import yfinance as yf
import pandas as pd
from datetime import datetime, time
import pytz
from dataclasses import dataclass
from config import VIRTUAL_PORTFOLIO_FILE, VIRTUAL_CAPITAL, SQLITE_DB_FILE
from utils import get_logger
from utils.telegram import send

logger = get_logger("PriceMonitor")

IST = pytz.timezone("Asia/Kolkata")

MARKET_OPEN  = time(9, 15)
MARKET_CLOSE = time(15, 25)

# Trailing stop: move SL up when price rises this much
TRAIL_TRIGGER_PCT = 0.015   # trail starts after 1.5% gain
TRAIL_PCT         = 0.02    # trail distance = 2%


@dataclass
class MonitorResult:
    symbol:         str
    action:         str       # HOLD | SL_HIT | TP_HIT | TRAIL_UPDATED | CLOSED_EOD
    current_price:  float
    entry_price:    float
    old_sl:         float
    new_sl:         float
    pnl:            float
    pnl_pct:        float
    qty:            int
    trade_type:     str       # swing | intraday


class PriceMonitor:
    """
    Monitors all open positions in real time.
    Automatically exits positions when SL or TP is hit.
    """

    def __init__(self):
        self.portfolio = self._load_portfolio()

    def is_market_open(self) -> bool:
        """Check if NSE is currently open."""
        now = datetime.now(IST).time()
        day = datetime.now(IST).weekday()
        return day < 5 and MARKET_OPEN <= now <= MARKET_CLOSE

    def run(self, force: bool = False) -> list[MonitorResult]:
        """
        Check all open positions.
        force=True bypasses market hours check (for testing).
        """
        if not force and not self.is_market_open():
            logger.info("Market closed — price monitor skipped")
            return []

        positions = self.portfolio.get("positions", {})
        if not positions:
            logger.info("No open positions to monitor")
            return []

        logger.info(f"Monitoring {len(positions)} open positions...")
        results  = []
        to_close = []

        for symbol, pos in positions.items():
            result = self._check_position(symbol, pos)
            if result:
                results.append(result)
                if result.action in ("SL_HIT", "TP_HIT", "CLOSED_EOD"):
                    to_close.append((symbol, result))
                elif result.action == "TRAIL_UPDATED":
                    positions[symbol]["stop_loss"] = result.new_sl

        # Close triggered positions
        for symbol, result in to_close:
            self._close_position(symbol, result)

        # Save updated portfolio
        self.portfolio["positions"] = {
            k: v for k, v in positions.items()
            if k not in [s for s, _ in to_close]
        }
        self._save_portfolio()

        # Log summary
        exits = [r for r in results if r.action in ("SL_HIT","TP_HIT","CLOSED_EOD")]
        if exits:
            total_pnl = sum(r.pnl for r in exits)
            logger.info(f"Monitor: {len(exits)} exits | Total P&L: Rs.{total_pnl:+,.0f}")

        return results

    def close_all_intraday(self) -> list[MonitorResult]:
        """
        Force-close all intraday positions at market price.
        Called at 3:25 PM.
        """
        positions = self.portfolio.get("positions", {})
        results   = []

        for symbol, pos in list(positions.items()):
            if pos.get("trade_type") != "intraday":
                continue
            curr = self._fetch_price(symbol)
            if curr:
                pnl     = (curr - pos["entry"]) * pos["qty"]
                pnl_pct = (curr - pos["entry"]) / pos["entry"] * 100
                result  = MonitorResult(
                    symbol=symbol, action="CLOSED_EOD",
                    current_price=curr, entry_price=pos["entry"],
                    old_sl=pos["stop_loss"], new_sl=pos["stop_loss"],
                    pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 2),
                    qty=pos["qty"], trade_type="intraday"
                )
                results.append(result)
                self._close_position(symbol, result)

        if results:
            total_pnl = sum(r.pnl for r in results)
            msg = (f"*EOD Close*\n"
                   f"Closed {len(results)} intraday positions\n"
                   f"P&L: `Rs.{total_pnl:+,.0f}`")
            send(msg)
            logger.info(f"EOD: closed {len(results)} intraday positions, P&L: Rs.{total_pnl:+,.0f}")

        # Remove closed from portfolio
        for r in results:
            self.portfolio["positions"].pop(r.symbol, None)
        self._save_portfolio()
        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_position(self, symbol: str, pos: dict) -> MonitorResult | None:
        """Check one position — return action needed."""
        curr = self._fetch_price(symbol)
        if curr is None:
            return None

        entry  = pos["entry"]
        sl     = pos["stop_loss"]
        tp     = pos["take_profit"]
        qty    = pos["qty"]
        ttype  = pos.get("trade_type", "swing")

        pnl     = (curr - entry) * qty
        pnl_pct = (curr - entry) / entry * 100

        # Stop loss hit
        if curr <= sl:
            logger.info(f"{symbol}: SL HIT at Rs.{curr:,.2f} (SL: Rs.{sl:,.2f})")
            return MonitorResult(
                symbol=symbol, action="SL_HIT",
                current_price=curr, entry_price=entry,
                old_sl=sl, new_sl=sl,
                pnl=round((sl - entry) * qty, 2),
                pnl_pct=round((sl - entry)/entry*100, 2),
                qty=qty, trade_type=ttype
            )

        # Take profit hit
        if curr >= tp:
            logger.info(f"{symbol}: TP HIT at Rs.{curr:,.2f} (TP: Rs.{tp:,.2f})")
            return MonitorResult(
                symbol=symbol, action="TP_HIT",
                current_price=curr, entry_price=entry,
                old_sl=sl, new_sl=sl,
                pnl=round((tp - entry) * qty, 2),
                pnl_pct=round((tp - entry)/entry*100, 2),
                qty=qty, trade_type=ttype
            )

        # Trailing stop update
        gain_pct = (curr - entry) / entry
        if gain_pct >= TRAIL_TRIGGER_PCT:
            new_sl = round(curr * (1 - TRAIL_PCT), 2)
            if new_sl > sl:
                logger.info(f"{symbol}: Trail SL {sl:,.2f} → {new_sl:,.2f} "
                            f"(price Rs.{curr:,.2f})")
                return MonitorResult(
                    symbol=symbol, action="TRAIL_UPDATED",
                    current_price=curr, entry_price=entry,
                    old_sl=sl, new_sl=new_sl,
                    pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 2),
                    qty=qty, trade_type=ttype
                )

        # Holding — no action
        return MonitorResult(
            symbol=symbol, action="HOLD",
            current_price=curr, entry_price=entry,
            old_sl=sl, new_sl=sl,
            pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 2),
            qty=qty, trade_type=ttype
        )

    def _close_position(self, symbol: str, result: MonitorResult):
        """Close a position — update portfolio + send Telegram."""
        pos = self.portfolio["positions"].get(symbol, {})
        if not pos:
            return

        # Update portfolio cash
        exit_price = result.current_price
        proceeds   = exit_price * result.qty
        self.portfolio["cash"] = self.portfolio.get("cash", 0) + proceeds
        self.portfolio["total_trades"] = self.portfolio.get("total_trades", 0) + 1
        if result.pnl > 0:
            self.portfolio["wins"] = self.portfolio.get("wins", 0) + 1

        # Log to CSV
        self._log_trade(result, exit_price)

        # Telegram alert
        icon = "✅" if result.pnl > 0 else "🔴"
        action_label = {
            "SL_HIT":    "Stop Loss Hit",
            "TP_HIT":    "Take Profit Hit",
            "CLOSED_EOD":"EOD Close",
        }.get(result.action, result.action)

        send(
            f"{icon} *{action_label}*\n"
            f"Stock: `{symbol}` ({result.trade_type})\n"
            f"Entry: `Rs.{result.entry_price:,.2f}` → Exit: `Rs.{exit_price:,.2f}`\n"
            f"Qty: `{result.qty}` shares\n"
            f"P&L: `Rs.{result.pnl:+,.0f}` ({result.pnl_pct:+.2f}%)"
        )

    def _fetch_price(self, symbol: str) -> float | None:
        """Fetch latest price via yfinance."""
        try:
            ticker = yf.Ticker(f"{symbol}.NS")
            hist   = ticker.history(period="1d", interval="15m")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as e:
            logger.debug(f"{symbol} price fetch failed: {e}")
        return None

    def _log_trade(self, result: MonitorResult, exit_price: float):
        """Write trade exit to SQLite (trades table) + CSV fallback."""
        # --- SQLite (primary) ---
        try:
            db = SQLITE_DB_FILE
            if os.path.exists(db):
                with sqlite3.connect(db) as conn:
                    # Find the most recent open trade for this symbol
                    row = conn.execute(
                        "SELECT id, entry_price, qty FROM trades "
                        "WHERE symbol=? AND status='open' ORDER BY id DESC LIMIT 1",
                        (result.symbol,)
                    ).fetchone()
                    if row:
                        trade_id, entry, qty = row
                        pnl     = round((exit_price - entry) * qty, 2)
                        pnl_pct = round((exit_price - entry) / entry * 100, 2)
                        conn.execute("""
                            UPDATE trades
                            SET exit_price=?, exit_time=?, pnl=?, pnl_pct=?, status='closed'
                            WHERE id=?
                        """, (round(exit_price, 2), datetime.now().isoformat(),
                              pnl, pnl_pct, trade_id))
                        logger.debug(f"SQLite: closed trade #{trade_id} for {result.symbol} "
                                     f"P&L Rs.{pnl:+,.0f}")
        except Exception as e:
            logger.warning(f"SQLite trade close failed for {result.symbol}: {e}")

        # --- CSV (fallback / audit log) ---
        import csv
        os.makedirs("logs", exist_ok=True)
        log_file   = "logs/paper_trades.csv"
        fieldnames = ["timestamp","symbol","trade_type","action","qty",
                      "entry_price","exit_price","pnl","pnl_pct"]
        row = {
            "timestamp":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol":      result.symbol,
            "trade_type":  result.trade_type,
            "action":      result.action,
            "qty":         result.qty,
            "entry_price": result.entry_price,
            "exit_price":  round(exit_price, 2),
            "pnl":         result.pnl,
            "pnl_pct":     result.pnl_pct,
        }
        write_header = not os.path.exists(log_file)
        with open(log_file, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                w.writeheader()
            w.writerow(row)

    def _load_portfolio(self) -> dict:
        os.makedirs("logs", exist_ok=True)
        if os.path.exists(VIRTUAL_PORTFOLIO_FILE):
            with open(VIRTUAL_PORTFOLIO_FILE) as f:
                return json.load(f)
        return {"cash": VIRTUAL_CAPITAL, "positions": {},
                "total_trades": 0, "wins": 0}

    def _save_portfolio(self):
        with open(VIRTUAL_PORTFOLIO_FILE, "w") as f:
            json.dump(self.portfolio, f, indent=2)
