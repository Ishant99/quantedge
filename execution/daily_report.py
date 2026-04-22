# =============================================================================
# execution/daily_report.py — End of Day P&L Report (all markets)
#
# Sent to Telegram at 3:30 PM every market day.
# Covers NSE equity + F&O + Crypto + US stocks.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import sqlite3
import csv
from datetime import datetime, date
import pytz
from config import VIRTUAL_CAPITAL, VIRTUAL_PORTFOLIO_FILE, SQLITE_DB_FILE, INR_PER_USD, INR_PER_USDT
from memory.portfolio_memory import PortfolioMemory
from utils import get_logger
from utils.telegram import send

logger = get_logger("DailyReport")
IST = pytz.timezone("Asia/Kolkata")


class DailyReporter:
    """Generates and sends end-of-day performance report for all markets."""

    def send_report(self):
        today_str = date.today().strftime("%d %b %Y")
        now_str   = datetime.now(IST).strftime("%I:%M %p IST")
        today_db  = date.today().strftime("%Y-%m-%d")

        # ── NSE equity ──────────────────────────────────────────────────
        pf         = self._load_portfolio()
        cash       = pf.get("cash", VIRTUAL_CAPITAL)
        positions  = pf.get("positions", {})
        # Include live MTM of open positions in portfolio value
        total_val  = cash
        try:
            from execution.executor import get_executor
            total_val = get_executor().get_portfolio_value()
        except Exception:
            pass
        pnl_total  = total_val - VIRTUAL_CAPITAL
        pnl_pct    = pnl_total / VIRTUAL_CAPITAL * 100

        today_trades = self._get_today_trades_csv()
        today_pnl    = sum(t.get("pnl", 0) for t in today_trades if t.get("pnl"))

        memory = PortfolioMemory()
        stats  = memory.get_stats()

        # ── F&O ─────────────────────────────────────────────────────────
        fno_today_count = fno_today_pnl = 0
        fno_open = 0
        if os.path.exists(SQLITE_DB_FILE):
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                try:
                    row = conn.execute(
                        "SELECT COUNT(*), COALESCE(SUM(pnl),0) FROM fno_trades "
                        "WHERE status='closed' AND exit_time LIKE ?", (f"{today_db}%",)
                    ).fetchone()
                    fno_today_count, fno_today_pnl = row[0], row[1]
                    fno_open = conn.execute(
                        "SELECT COUNT(*) FROM fno_trades WHERE status='open'"
                    ).fetchone()[0]
                except Exception:
                    pass

        # ── Crypto ──────────────────────────────────────────────────────
        cr_today_count = cr_today_pnl = 0
        cr_open = 0
        if os.path.exists(SQLITE_DB_FILE):
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                try:
                    row = conn.execute(
                        "SELECT COUNT(*), COALESCE(SUM(pnl_usdt),0) FROM crypto_trades "
                        "WHERE status='closed' AND exit_time LIKE ?", (f"{today_db}%",)
                    ).fetchone()
                    cr_today_count, cr_today_pnl = row[0], row[1]
                    cr_open = conn.execute(
                        "SELECT COUNT(*) FROM crypto_trades WHERE status='open'"
                    ).fetchone()[0]
                except Exception:
                    pass

        # ── US Stocks ───────────────────────────────────────────────────
        us_today_count = us_today_pnl = 0
        us_open = 0
        if os.path.exists(SQLITE_DB_FILE):
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                try:
                    row = conn.execute(
                        "SELECT COUNT(*), COALESCE(SUM(pnl_usd),0) FROM us_trades "
                        "WHERE status='closed' AND exit_time LIKE ?", (f"{today_db}%",)
                    ).fetchone()
                    us_today_count, us_today_pnl = row[0], row[1]
                    us_open = conn.execute(
                        "SELECT COUNT(*) FROM us_trades WHERE status='open'"
                    ).fetchone()[0]
                except Exception:
                    pass

        combined_pnl = (today_pnl + fno_today_pnl +
                        cr_today_pnl * INR_PER_USDT + us_today_pnl * INR_PER_USD)

        # ── Readiness ───────────────────────────────────────────────────
        r = self._load_json("logs/readiness_report.json")
        gates_str = f"{r.get('passed',0)}/{r.get('total',8)}" if r else "N/A"

        # ── Build message ───────────────────────────────────────────────
        day_icon = "✅" if combined_pnl >= 0 else "🔴"
        lines = [
            f"*📊 End of Day Report — {today_str}*",
            f"_{now_str}_",
            "",
            f"*NSE Equity*",
            f"Portfolio value: `₹{total_val:,.0f}` ({pnl_pct:+.2f}% from start)",
            f"Positions open: `{len(positions)}` | Trades today: `{len(today_trades)}`",
            f"Today P&L: `₹{today_pnl:+,.0f}`",
        ]

        if today_trades:
            for t in today_trades[:3]:
                pnl_val = float(t.get("pnl", 0))
                icon    = "✅" if pnl_val > 0 else "🔴"
                lines.append(f"  {icon} {t.get('symbol','')} → `₹{pnl_val:+,.0f}`")

        lines += [
            "",
            "*F&O (Options/Futures)*",
            f"Open: `{fno_open}` positions | Closed today: `{fno_today_count}`",
            f"Today P&L: `₹{fno_today_pnl:+,.0f}`",
            "",
            "*Crypto*",
            f"Open: `{cr_open}` | Closed today: `{cr_today_count}`",
            f"Today P&L: `{cr_today_pnl:+.2f} USDT` = `₹{cr_today_pnl * INR_PER_USDT:+,.0f}`",
            "",
            "*US Stocks*",
            f"Open: `{us_open}` | Closed today: `{us_today_count}`",
            f"Today P&L: `${us_today_pnl:+.2f}` = `₹{us_today_pnl * INR_PER_USD:+,.0f}`",
            "",
            f"{day_icon} *Total Today (all markets): `₹{combined_pnl:+,.0f}`*",
            "",
            "*Overall Performance*",
            f"Total trades: `{stats.get('total_trades', 0)}` | "
            f"Win rate: `{stats.get('win_rate_pct', 0):.1f}%` | "
            f"Profit factor: `{stats.get('profit_factor', 0):.2f}`",
            f"Avg per trade: `₹{stats.get('expectancy', 0):+,.0f}` | "
            f"Max drawdown: `{stats.get('max_drawdown_pct', 0):.1f}%`",
            "",
            f"*Readiness: `{gates_str}` gates passed*",
        ]

        # Next trading day note
        weekday = datetime.now(IST).weekday()
        if weekday >= 4:  # Friday or weekend
            lines.append("_Next trading day: Monday_")

        send("\n".join(lines))
        logger.info(f"Daily report sent — NSE: {len(today_trades)} trades Rs.{today_pnl:+,.0f} | "
                    f"F&O: {fno_today_count} Rs.{fno_today_pnl:+,.0f} | "
                    f"Combined: Rs.{combined_pnl:+,.0f}")

    def _get_today_trades_csv(self) -> list[dict]:
        """Read today's trades from paper_trades.csv."""
        log_file = "logs/paper_trades.csv"
        if not os.path.exists(log_file):
            return []
        today = date.today().strftime("%Y-%m-%d")
        trades = []
        try:
            with open(log_file, "r") as f:
                for row in csv.DictReader(f):
                    if row.get("timestamp", "").startswith(today):
                        try:
                            row["pnl"] = float(row.get("pnl", 0) or 0)
                        except Exception:
                            row["pnl"] = 0
                        trades.append(row)
        except Exception as e:
            logger.debug(f"Trade log read error: {e}")
        return trades

    def _load_portfolio(self) -> dict:
        if os.path.exists(VIRTUAL_PORTFOLIO_FILE):
            with open(VIRTUAL_PORTFOLIO_FILE) as f:
                return json.load(f)
        return {"cash": VIRTUAL_CAPITAL, "positions": {}}

    def _load_json(self, path: str) -> dict:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}
