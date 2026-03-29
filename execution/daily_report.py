# =============================================================================
# execution/daily_report.py — End of Day P&L Report
#
# Sent to Telegram at 3:30 PM every market day.
# Includes: trades taken today, P&L, portfolio value, win rate update.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import csv
from datetime import datetime, date
import pytz
from config import VIRTUAL_CAPITAL, VIRTUAL_PORTFOLIO_FILE
from memory.portfolio_memory import PortfolioMemory
from utils import get_logger
from utils.telegram import send

logger = get_logger("DailyReport")
IST = pytz.timezone("Asia/Kolkata")


class DailyReporter:
    """Generates and sends end-of-day performance report."""

    def send_report(self):
        """Build and send the daily report to Telegram."""
        today_str = date.today().strftime("%d %b %Y")
        now_str   = datetime.now(IST).strftime("%I:%M %p IST")

        # Load portfolio
        pf         = self._load_portfolio()
        cash       = pf.get("cash", VIRTUAL_CAPITAL)
        positions  = pf.get("positions", {})
        total_val  = cash   # simplified: use cash (positions marked at entry)
        pnl_total  = total_val - VIRTUAL_CAPITAL
        pnl_pct    = pnl_total / VIRTUAL_CAPITAL * 100

        # Today's trades from CSV
        today_trades = self._get_today_trades()
        today_pnl    = sum(t.get("pnl", 0) for t in today_trades if t.get("pnl"))

        # Stats from memory
        memory = PortfolioMemory()
        stats  = memory.get_stats()

        # Readiness
        r = self._load_json("logs/readiness_report.json")
        gates_str = f"{r.get('passed',0)}/{r.get('total',8)}" if r else "N/A"

        # Build message
        lines = [
            f"*Daily Report — {today_str}*",
            f"_{now_str}_",
            "",
            "*Portfolio*",
            f"Value: `Rs.{total_val:,.0f}`",
            f"Total P&L: `Rs.{pnl_total:+,.0f}` ({pnl_pct:+.2f}%)",
            f"Open Positions: `{len(positions)}`",
            "",
            f"*Today's Activity*",
            f"Trades: `{len(today_trades)}`",
            f"Today P&L: `Rs.{today_pnl:+,.0f}`",
        ]

        # List today's trades
        if today_trades:
            lines.append("")
            for t in today_trades[:5]:
                icon = "✅" if float(t.get("pnl",0)) > 0 else "🔴"
                lines.append(
                    f"{icon} {t.get('symbol','')} `{t.get('action','')}` "
                    f"→ Rs.{float(t.get('pnl',0)):+,.0f}"
                )

        lines += [
            "",
            "*Performance Stats*",
            f"Total Trades: `{stats['total_trades']}`",
            f"Win Rate: `{stats['win_rate_pct']:.1f}%`",
            f"Profit Factor: `{stats['profit_factor']:.2f}`",
            f"Max Drawdown: `{stats['max_drawdown_pct']:.1f}%`",
            "",
            f"*Phase 2 Readiness: `{gates_str}` gates*",
        ]

        # Market tomorrow
        tomorrow = datetime.now(IST).weekday()
        if tomorrow == 4:   # Friday
            lines.append("\n_Next trading day: Monday_")
        elif tomorrow == 5: # Saturday
            lines.append("\n_Next trading day: Monday_")

        msg = "\n".join(lines)
        send(msg)
        logger.info(f"Daily report sent — trades: {len(today_trades)}, P&L: Rs.{today_pnl:+,.0f}")

    def _get_today_trades(self) -> list[dict]:
        """Read today's trades from paper_trades.csv."""
        log_file = "logs/paper_trades.csv"
        if not os.path.exists(log_file):
            return []

        today = date.today().strftime("%Y-%m-%d")
        trades = []
        try:
            with open(log_file, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
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
