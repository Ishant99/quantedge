# =============================================================================
# execution/executor.py — M7: Execution Layer
#
# THE most important safety boundary in the entire agent.
# TRADING_MODE = "paper" → logs to CSV, no real orders ever
# TRADING_MODE = "live"  → calls Zerodha Kite API
#
# Both modes use identical interfaces so switching is one config change.
# =============================================================================

import json, os, csv, sqlite3
from datetime import datetime
from dataclasses import asdict
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    TRADING_MODE, VIRTUAL_CAPITAL, VIRTUAL_PORTFOLIO_FILE,
    KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN_FILE,
    SQLITE_DB_FILE, RISK_PER_TRADE_PCT, MAX_POSITION_RISK_PCT,
)
from strategy.engine import TradeSignal
from services.paper_treasury import can_allocate, log_treasury_event, write_treasury_snapshot
from execution.portfolio_lock import load_portfolio_locked, save_portfolio_locked
from utils import get_logger

logger = get_logger("Executor")


class PaperExecutor:
    """
    Simulates order execution. Updates a virtual portfolio JSON file.
    Identical interface to LiveExecutor — swap by changing config only.
    """

    def __init__(self):
        self.portfolio = self._load_portfolio()
        logger.info(f"Paper executor ready — virtual capital: ₹{self.portfolio['cash']:,.0f}")

    def execute(self, signal: TradeSignal) -> dict:
        """Simulate placing an order."""
        if signal.action not in ("BUY", "SELL"):
            return {"status": "skipped", "reason": "HOLD signal"}

        if signal.position_size <= 0:
            return {"status": "skipped", "reason": "position size is 0"}

        # Reload portfolio from disk to pick up changes from other scheduler jobs
        # (price_monitor, trailing_stop, EOD close) that may have run concurrently.
        fresh = self._load_portfolio()
        if fresh is not None:
            self.portfolio = fresh

        result = {}

        if signal.action == "BUY":
            # Re-check after reload — trailing stop or scheduler may have opened/closed
            # a position between the caller's check and now.
            if signal.symbol in self.portfolio["positions"]:
                return {"status": "skipped", "reason": "position already open"}

            # --- Live price refresh at execution time ---
            # Signal prices may be stale by minutes; recalculate using live price.
            entry_price = signal.entry_price
            stop_loss   = signal.stop_loss
            take_profit = signal.take_profit
            live_price  = self._get_mark_price(signal.symbol)
            if live_price and abs(live_price - entry_price) / max(entry_price, 1) > 0.002:
                sl_pct  = (entry_price - stop_loss)   / entry_price
                tp_pct  = (take_profit - entry_price) / entry_price
                entry_price = live_price
                stop_loss   = round(entry_price * (1 - sl_pct),  2)
                take_profit = round(entry_price * (1 + tp_pct),  2)
                logger.info(
                    f"BUY {signal.symbol}: price refreshed "
                    f"₹{signal.entry_price:,.2f} → ₹{entry_price:,.2f}"
                )

            # Recalculate position size from refreshed entry / SL
            sl_distance   = entry_price - stop_loss
            portfolio_val = self.portfolio["cash"] + sum(
                p["entry"] * p["qty"] for p in self.portfolio["positions"].values()
            )
            if sl_distance > 0:
                position_size = int((portfolio_val * RISK_PER_TRADE_PCT) / sl_distance)
                position_size = max(1, position_size)
            else:
                position_size = signal.position_size

            # --- Position-level max loss guard ---
            position_risk = sl_distance * position_size
            max_allowed   = portfolio_val * MAX_POSITION_RISK_PCT
            if position_risk > max_allowed:
                position_size = max(1, int(max_allowed / sl_distance)) if sl_distance > 0 else 1
                logger.info(
                    f"BUY {signal.symbol}: size capped to {position_size} "
                    f"(risk cap Rs.{max_allowed:,.0f})"
                )

            # Slippage (0.1%) + flat brokerage (₹20) on BUY
            slippage_buy  = round(entry_price * position_size * 0.001, 2)
            brokerage_buy = 20.0
            cost = entry_price * position_size + slippage_buy + brokerage_buy
            ok, reason, _ = can_allocate("nse", cost)
            if not ok:
                return {"status": "rejected", "reason": reason}
            if cost > self.portfolio["cash"]:
                return {"status": "rejected", "reason": "insufficient cash"}

            self.portfolio["cash"] -= cost
            self.portfolio["positions"][signal.symbol] = {
                "qty":              position_size,
                "entry":            entry_price,
                "stop_loss":        stop_loss,
                "take_profit":      take_profit,
                "entry_confidence": signal.confidence,
                "trade_type":       getattr(signal, "trade_type", "swing"),
                "timestamp":        datetime.now().isoformat(),
                "entry_friction":   round(slippage_buy + brokerage_buy, 2),
            }
            result = {
                "status":    "filled",
                "action":    "BUY",
                "symbol":    signal.symbol,
                "qty":       position_size,
                "price":     entry_price,
                "cost":      round(cost, 2),
                "friction":  round(slippage_buy + brokerage_buy, 2),
                "mode":      "paper",
            }
            logger.info(f"PAPER BUY  {signal.symbol} × {position_size} @ ₹{entry_price:,.2f}")
            log_treasury_event("reserve_open", "nse", cost, f"{signal.symbol} BUY", {"symbol": signal.symbol})

        elif signal.action == "SELL":
            pos = self.portfolio["positions"].get(signal.symbol)
            if not pos:
                return {"status": "skipped", "reason": "no open position to sell"}

            # Use live price if signal.entry_price looks stale (same as original entry)
            sell_price = signal.entry_price
            if abs(sell_price - pos["entry"]) < 0.01:
                live = self._get_mark_price(signal.symbol)
                if live:
                    sell_price = live
                    logger.debug(f"SELL {signal.symbol}: using live price ₹{live:,.2f} instead of stale entry")

            # Slippage (0.1%) + flat brokerage (₹20) on SELL
            slippage_sell  = round(sell_price * pos["qty"] * 0.001, 2)
            brokerage_sell = 20.0
            proceeds = sell_price * pos["qty"] - slippage_sell - brokerage_sell
            # Deduct entry friction so PnL reflects total round-trip cost
            pnl      = proceeds - (pos["entry"] * pos["qty"]) - pos.get("entry_friction", 0.0)
            self.portfolio["cash"] += proceeds
            del self.portfolio["positions"][signal.symbol]
            self.portfolio["total_trades"] += 1
            if pnl > 0:
                self.portfolio["wins"] += 1

            result = {
                "status":    "filled",
                "action":    "SELL",
                "symbol":    signal.symbol,
                "qty":       pos["qty"],
                "price":     sell_price,
                "pnl":       round(pnl, 2),
                "friction":  round(slippage_sell + brokerage_sell, 2),
                "mode":      "paper",
            }
            logger.info(f"PAPER SELL {signal.symbol} × {pos['qty']} @ ₹{sell_price:,.2f} | PnL ₹{pnl:+,.0f}")
            log_treasury_event("release_close", "nse", pos["entry"] * pos["qty"], f"{signal.symbol} SELL", {"symbol": signal.symbol, "pnl": round(pnl, 2)})

        # SQLite first — if it fails we don't update the portfolio JSON,
        # keeping both stores consistent (atomic write ordering).
        self._log_trade(signal, result)
        self._save_portfolio()
        write_treasury_snapshot()
        return result

    def get_portfolio_value(self) -> float:
        """Cash + mark-to-market value of open positions."""
        total = self.portfolio["cash"]
        for sym, pos in self.portfolio["positions"].items():
            mark = self._get_mark_price(sym) or pos["entry"]
            total += mark * pos["qty"]
        return round(total, 2)

    def get_open_positions_count(self) -> int:
        return len(self.portfolio["positions"])

    def get_portfolio_summary(self) -> dict:
        total    = self.get_portfolio_value()
        initial  = VIRTUAL_CAPITAL
        pnl      = total - initial
        trades   = self.portfolio["total_trades"]
        wins     = self.portfolio["wins"]
        return {
            "mode":           "paper",
            "cash":           round(self.portfolio["cash"], 2),
            "portfolio_value":total,
            "pnl":            round(pnl, 2),
            "pnl_pct":        round((pnl / initial) * 100, 2),
            "open_positions": self.get_open_positions_count(),
            "total_trades":   trades,
            "win_rate":       round((wins / trades * 100) if trades else 0, 1),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_portfolio(self) -> dict:
        os.makedirs("logs", exist_ok=True)
        data = load_portfolio_locked(VIRTUAL_PORTFOLIO_FILE)
        if data:
            return data
        return {
            "cash":         VIRTUAL_CAPITAL,
            "positions":    {},
            "total_trades": 0,
            "wins":         0,
            "created":      datetime.now().isoformat(),
        }

    def _save_portfolio(self):
        save_portfolio_locked(VIRTUAL_PORTFOLIO_FILE, self.portfolio)

    def _get_mark_price(self, symbol: str) -> float | None:
        """Best-effort live mark for paper-mode risk checks and reporting."""
        try:
            import yfinance as yf
            hist = yf.Ticker(f"{symbol}.NS").history(period="1d", interval="15m")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass
        return None

    def _log_trade(self, signal: TradeSignal, result: dict):
        os.makedirs("logs", exist_ok=True)

        # --- SQLite (primary — needed for stats, dashboard, history) ---
        try:
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS trades (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol      TEXT NOT NULL,
                        action      TEXT,
                        entry_price REAL,
                        exit_price  REAL,
                        stop_loss   REAL,
                        take_profit REAL,
                        qty         INTEGER,
                        pnl         REAL DEFAULT 0,
                        pnl_pct     REAL DEFAULT 0,
                        status      TEXT DEFAULT 'open',
                        trade_type  TEXT DEFAULT 'swing',
                        entry_time  TEXT,
                        exit_time   TEXT,
                        reasoning   TEXT
                    )
                """)
                if signal.action == "BUY":
                    conn.execute("""
                        INSERT INTO trades
                        (symbol, action, entry_price, stop_loss, take_profit,
                         qty, status, trade_type, entry_time, reasoning)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (signal.symbol, "BUY", signal.entry_price,
                          signal.stop_loss, signal.take_profit,
                          result.get("qty", 0), "open", "swing",
                          datetime.now().isoformat(), signal.reasoning))
                elif signal.action == "SELL":
                    pnl        = result.get("pnl", 0) or 0
                    qty        = result.get("qty", 0)
                    actual_price = result.get("price", signal.entry_price) or signal.entry_price
                    cost_basis   = actual_price * qty if qty else 1
                    pnl_pct    = round(pnl / cost_basis * 100, 2) if cost_basis else 0
                    row = conn.execute(
                        "SELECT id, entry_price FROM trades WHERE symbol=? AND status='open' "
                        "ORDER BY id DESC LIMIT 1", (signal.symbol,)
                    ).fetchone()
                    if row:
                        trade_id, entry_p = row
                        entry_cost = (entry_p or actual_price) * qty
                        real_pnl_pct = round(pnl / entry_cost * 100, 2) if entry_cost else 0
                        conn.execute("""
                            UPDATE trades SET exit_price=?, exit_time=?,
                            pnl=?, pnl_pct=?, status='closed' WHERE id=?
                        """, (round(actual_price, 2), datetime.now().isoformat(),
                              round(pnl, 2), real_pnl_pct, trade_id))
                    else:
                        # No open BUY found — insert as standalone closed record
                        conn.execute("""
                            INSERT INTO trades
                            (symbol, action, entry_price, exit_price, qty,
                             pnl, pnl_pct, status, trade_type, exit_time, reasoning)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        """, (signal.symbol, "SELL", round(actual_price, 2),
                              round(actual_price, 2), qty,
                              round(pnl, 2), pnl_pct, "closed", "swing",
                              datetime.now().isoformat(), signal.reasoning))
        except Exception as e:
            logger.warning(f"SQLite trade log failed ({signal.symbol}): {e}")

        # --- CSV (audit log) ---
        log_file = "logs/paper_trades.csv"
        fieldnames = [
            "timestamp","symbol","action","qty","price","confidence",
            "ta_score","sentiment","stop_loss","take_profit",
            "pnl","status","reasoning"
        ]
        row = {
            "timestamp":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol":     signal.symbol,
            "action":     signal.action,
            "qty":        result.get("qty", 0),
            "price":      result.get("price", signal.entry_price),
            "confidence": signal.confidence,
            "ta_score":   signal.ta_score,
            "sentiment":  signal.sentiment,
            "stop_loss":  signal.stop_loss,
            "take_profit":signal.take_profit,
            "pnl":        result.get("pnl", ""),
            "status":     result.get("status"),
            "reasoning":  signal.reasoning,
        }
        write_header = not os.path.exists(log_file)
        with open(log_file, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                w.writeheader()
            w.writerow(row)


class LiveExecutor:
    """
    Real Zerodha Kite execution. Only instantiated when TRADING_MODE=live.
    Requires kiteconnect package: pip install kiteconnect
    """

    def __init__(self):
        try:
            from kiteconnect import KiteConnect
            self.kite = KiteConnect(api_key=KITE_API_KEY)
            token = self._load_token()
            self.kite.set_access_token(token)
            profile = self.kite.profile()
            logger.info(f"Kite connected — user: {profile['user_name']}")
        except ImportError:
            raise RuntimeError("Install kiteconnect: pip install kiteconnect")
        except Exception as e:
            raise RuntimeError(f"Kite connection failed: {e}")

    def execute(self, signal: TradeSignal) -> dict:
        if signal.action not in ("BUY", "SELL"):
            return {"status": "skipped", "reason": "HOLD signal"}
        if signal.position_size <= 0:
            return {"status": "skipped", "reason": "position size is 0"}

        # Guard against race conditions — re-check live positions before placing order
        if signal.action == "BUY":
            try:
                live_positions = {p["tradingsymbol"] for p in self.kite.positions().get("net", [])}
                if signal.symbol in live_positions:
                    return {"status": "skipped", "reason": "position already open (live check)"}
            except Exception as e:
                logger.warning(f"Live position check failed for {signal.symbol}: {e}")

        from kiteconnect import KiteConnect
        transaction = (
            self.kite.TRANSACTION_TYPE_BUY
            if signal.action == "BUY"
            else self.kite.TRANSACTION_TYPE_SELL
        )
        try:
            order_id = self.kite.place_order(
                tradingsymbol = signal.symbol,
                exchange      = "NSE",
                transaction_type = transaction,
                quantity      = signal.position_size,
                order_type    = self.kite.ORDER_TYPE_MARKET,
                product       = self.kite.PRODUCT_CNC,   # delivery (not intraday)
                variety       = self.kite.VARIETY_REGULAR,
            )
            # Place GTT stop-loss — if this fails, return error so caller can alert operator
            if signal.action == "BUY":
                gtt_ok = self._place_gtt_sl(signal, order_id)
                if not gtt_ok:
                    logger.error(f"GTT SL failed for {signal.symbol} — position is LONG but unhedged")
                    return {"status": "gtt_failed", "order_id": order_id, "mode": "live",
                            "warning": "BUY placed but GTT stop-loss failed — manual action required"}

            logger.info(f"LIVE {signal.action} {signal.symbol} × {signal.position_size} — order_id: {order_id}")
            return {"status": "filled", "order_id": order_id, "mode": "live"}

        except Exception as e:
            logger.error(f"Order failed for {signal.symbol}: {e}")
            return {"status": "failed", "error": str(e)}

    def _place_gtt_sl(self, signal: TradeSignal, parent_order_id: str) -> bool:
        """Place a GTT stop-loss. Returns True on success, False on failure."""
        try:
            self.kite.place_gtt(
                trigger_type  = self.kite.GTT_TYPE_SINGLE,
                tradingsymbol = signal.symbol,
                exchange      = "NSE",
                trigger_values= [signal.stop_loss],
                last_price    = signal.entry_price,
                orders=[{
                    "transaction_type": self.kite.TRANSACTION_TYPE_SELL,
                    "quantity":         signal.position_size,
                    "order_type":       self.kite.ORDER_TYPE_LIMIT,
                    "price":            signal.stop_loss,
                    "product":          self.kite.PRODUCT_CNC,
                }]
            )
            logger.info(f"GTT SL placed for {signal.symbol} @ ₹{signal.stop_loss}")
            return True
        except Exception as e:
            logger.error(f"GTT SL failed for {signal.symbol}: {e}")
            return False

    def get_portfolio_value(self) -> float:
        margins = self.kite.margins()
        return float(margins["equity"]["net"])

    def get_open_positions_count(self) -> int:
        return len(self.kite.positions()["net"])

    def get_portfolio_summary(self) -> dict:
        margins   = self.kite.margins()
        positions = self.kite.positions()["net"]
        holdings  = self.kite.holdings()
        equity    = float(margins.get("equity", {}).get("net", 0) or 0)
        cash      = float(
            margins.get("equity", {}).get("available", {}).get("live_balance", 0)
            or margins.get("equity", {}).get("available", {}).get("cash", 0)
            or equity
        )
        return {
            "mode":           "live",
            "cash":           cash,
            "portfolio_value":equity,
            "pnl":            0.0,
            "pnl_pct":        0.0,
            "open_positions": len(positions),
            "holdings":       len(holdings),
        }

    def _load_token(self) -> str:
        if os.path.exists(KITE_ACCESS_TOKEN_FILE):
            with open(KITE_ACCESS_TOKEN_FILE) as f:
                return f.read().strip()
        raise FileNotFoundError(
            f"Access token not found at {KITE_ACCESS_TOKEN_FILE}.\n"
            f"Run: python execution/kite_auth.py to generate it."
        )


def get_executor():
    """
    Factory function — returns the correct executor based on TRADING_MODE.
    This is the ONLY place in the codebase that reads TRADING_MODE for execution.
    """
    if TRADING_MODE == "live":
        logger.warning("LIVE MODE ACTIVE — real orders will be placed")
        return LiveExecutor()
    else:
        logger.info("Paper mode — no real orders will be placed")
        return PaperExecutor()
