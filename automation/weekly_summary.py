# =============================================================================
# automation/weekly_summary.py
# Sends a rich weekly performance summary to Telegram every Sunday 8 PM IST.
# Called by scheduler.py — never needs to be run manually.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import sqlite3
from datetime import datetime, timedelta
from config import VIRTUAL_CAPITAL, SQLITE_DB_FILE, VIRTUAL_PORTFOLIO_FILE
from utils import get_logger
from utils.telegram import send

logger = get_logger("WeeklySummary")


def send_weekly_summary():
    """Build and send the weekly trading report to Telegram."""
    try:
        _send()
    except Exception as e:
        logger.error(f"Weekly summary failed: {e}")
        send(f"Weekly summary error: {e}")


def _send():
    week_label  = datetime.now().strftime("%d %b %Y")
    week_start  = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # Portfolio value
    # ------------------------------------------------------------------
    initial_capital = VIRTUAL_CAPITAL
    portfolio_value = initial_capital
    open_positions  = {}

    if os.path.exists(VIRTUAL_PORTFOLIO_FILE):
        with open(VIRTUAL_PORTFOLIO_FILE) as f:
            pf = json.load(f)
        cash           = pf.get("cash", initial_capital)
        open_positions = pf.get("positions", {})
        # MTM: cash + open positions valued at entry price
        mtm            = sum(p["entry"] * p["qty"] for p in open_positions.values())
        portfolio_value = cash + mtm
    else:
        cash = initial_capital

    pnl     = portfolio_value - initial_capital
    pnl_pct = pnl / initial_capital * 100

    # ------------------------------------------------------------------
    # Trades this week from SQLite
    # ------------------------------------------------------------------
    week_trades   = []
    total_signals = 0

    if os.path.exists(SQLITE_DB_FILE):
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            conn.row_factory = sqlite3.Row

            rows = conn.execute("""
                SELECT symbol, action, entry_price, exit_price,
                       pnl, pnl_pct, exit_time
                FROM trades
                WHERE status='closed' AND exit_time >= ?
                ORDER BY exit_time DESC
            """, (week_start,)).fetchall()
            week_trades = [dict(r) for r in rows]

            total_signals = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE timestamp >= ?",
                (week_start,)
            ).fetchone()[0]

    wins   = [t for t in week_trades if t.get("pnl") and t["pnl"] > 0]
    losses = [t for t in week_trades if t.get("pnl") and t["pnl"] <= 0]
    total_pnl_week = sum(t["pnl"] for t in week_trades if t.get("pnl"))
    win_rate = (len(wins) / len(week_trades) * 100) if week_trades else 0

    best  = max(week_trades, key=lambda t: t.get("pnl") or 0, default=None)
    worst = min(week_trades, key=lambda t: t.get("pnl") or 0, default=None)

    # ------------------------------------------------------------------
    # Readiness gates
    # ------------------------------------------------------------------
    gates_passed = gates_total = 0
    readiness_file = "logs/readiness_report.json"
    if os.path.exists(readiness_file):
        with open(readiness_file) as f:
            r = json.load(f)
        gates_passed = r.get("passed", 0)
        gates_total  = r.get("total", 8)

    # ------------------------------------------------------------------
    # Build message
    # ------------------------------------------------------------------
    lines = [
        "*Weekly Trading Report*",
        f"_Week ending {week_label}_",
        "",
        "*Portfolio*",
        f"Value:  `Rs.{portfolio_value:>12,.0f}`",
        f"P&L:    `Rs.{pnl:>+12,.0f}` ({pnl_pct:+.2f}%)",
        f"Open positions: `{len(open_positions)}`",
        "",
        "*This Week*",
        f"Signals generated: `{total_signals}`",
        f"Trades executed:   `{len(week_trades)}`",
        f"Wins / Losses:     `{len(wins)} / {len(losses)}`",
        f"Win rate:          `{win_rate:.0f}%`",
        f"Week P&L:          `Rs.{total_pnl_week:+,.0f}`",
    ]

    if best and best.get("pnl"):
        lines.append(f"Best trade:  `{best['symbol']}` "
                     f"`Rs.{best['pnl']:+,.0f}` ({best.get('pnl_pct', 0):+.1f}%)")
    if worst and worst.get("pnl"):
        lines.append(f"Worst trade: `{worst['symbol']}` "
                     f"`Rs.{worst['pnl']:+,.0f}` ({worst.get('pnl_pct', 0):+.1f}%)")

    if open_positions:
        lines += ["", "*Open Positions*"]
        for sym, pos in list(open_positions.items())[:5]:
            lines.append(f"  {sym} | entry Rs.{pos['entry']:,.0f} "
                         f"| SL Rs.{pos['stop_loss']:,.0f} "
                         f"| TP Rs.{pos['take_profit']:,.0f}")
        if len(open_positions) > 5:
            lines.append(f"  ...and {len(open_positions) - 5} more")

    if gates_total > 0:
        lines += [
            "",
            f"*Live Readiness: {gates_passed}/{gates_total} gates*",
            "Ready to go live!" if gates_passed == gates_total
            else f"~{max(0,(gates_total-gates_passed)*5)} more trading days needed",
        ]

    lines += ["", "_Keep the agent running daily!_"]

    send("\n".join(lines))
    logger.info(f"Weekly summary sent — {len(week_trades)} trades this week, "
                f"P&L Rs.{total_pnl_week:+,.0f}")


if __name__ == "__main__":
    send_weekly_summary()
