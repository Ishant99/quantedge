# =============================================================================
# automation/weekly_summary.py
# Sends a rich weekly performance summary to Telegram every Sunday 8 PM IST.
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
    try:
        _send()
    except Exception as e:
        logger.error(f"Weekly summary failed: {e}")
        send(f"Weekly summary error: {e}")


def _send():
    week_label = datetime.now().strftime("%d %b %Y")
    week_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # NSE equity portfolio
    # ------------------------------------------------------------------
    initial_capital = VIRTUAL_CAPITAL
    portfolio_value = initial_capital
    open_positions  = {}

    if os.path.exists(VIRTUAL_PORTFOLIO_FILE):
        with open(VIRTUAL_PORTFOLIO_FILE) as f:
            pf = json.load(f)
        cash           = pf.get("cash", initial_capital)
        open_positions = pf.get("positions", {})
        mtm            = sum(p.get("entry", 0) * p.get("qty", 0)
                             for p in open_positions.values())
        portfolio_value = cash + mtm
    else:
        cash = initial_capital

    pnl     = portfolio_value - initial_capital
    pnl_pct = pnl / initial_capital * 100

    # ------------------------------------------------------------------
    # NSE equity trades this week
    # ------------------------------------------------------------------
    week_trades   = []
    total_signals = 0

    if os.path.exists(SQLITE_DB_FILE):
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            conn.row_factory = sqlite3.Row

            # NSE equity trades
            try:
                rows = conn.execute("""
                    SELECT symbol, action, entry_price, exit_price,
                           pnl, pnl_pct, exit_time
                    FROM trades
                    WHERE status='closed' AND exit_time >= ?
                    ORDER BY exit_time DESC
                """, (week_start,)).fetchall()
                week_trades = [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass

            try:
                total_signals = conn.execute(
                    "SELECT COUNT(*) FROM signals WHERE timestamp >= ?",
                    (week_start,)
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass

    wins   = [t for t in week_trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in week_trades if (t.get("pnl") or 0) <= 0]
    total_pnl_week = sum(t.get("pnl") or 0 for t in week_trades)
    win_rate = (len(wins) / len(week_trades) * 100) if week_trades else 0

    best  = max(week_trades, key=lambda t: t.get("pnl") or 0, default=None)
    worst = min(week_trades, key=lambda t: t.get("pnl") or 0, default=None)

    # ------------------------------------------------------------------
    # F&O trades this week
    # ------------------------------------------------------------------
    fno_week_pnl = 0
    fno_week_count = 0
    fno_open_count = 0

    if os.path.exists(SQLITE_DB_FILE):
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            try:
                row = conn.execute("""
                    SELECT COUNT(*), COALESCE(SUM(pnl),0)
                    FROM fno_trades WHERE status='closed' AND exit_time >= ?
                """, (week_start,)).fetchone()
                fno_week_count, fno_week_pnl = row[0], row[1]
                fno_open_count = conn.execute(
                    "SELECT COUNT(*) FROM fno_trades WHERE status='open'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass

    # ------------------------------------------------------------------
    # Crypto trades this week
    # ------------------------------------------------------------------
    crypto_week_pnl   = 0
    crypto_week_count = 0
    crypto_open_count = 0

    if os.path.exists(SQLITE_DB_FILE):
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            try:
                row = conn.execute("""
                    SELECT COUNT(*), COALESCE(SUM(pnl_usdt),0)
                    FROM crypto_trades WHERE status='closed' AND exit_time >= ?
                """, (week_start,)).fetchone()
                crypto_week_count, crypto_week_pnl = row[0], row[1]
                crypto_open_count = conn.execute(
                    "SELECT COUNT(*) FROM crypto_trades WHERE status='open'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass

    # ------------------------------------------------------------------
    # US trades this week
    # ------------------------------------------------------------------
    us_week_pnl   = 0
    us_week_count = 0
    us_open_count = 0

    if os.path.exists(SQLITE_DB_FILE):
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            try:
                row = conn.execute("""
                    SELECT COUNT(*), COALESCE(SUM(pnl_usd),0)
                    FROM us_trades WHERE status='closed' AND exit_time >= ?
                """, (week_start,)).fetchone()
                us_week_count, us_week_pnl = row[0], row[1]
                us_open_count = conn.execute(
                    "SELECT COUNT(*) FROM us_trades WHERE status='open'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass

    INR_RATE = 83.0
    combined_pnl = (total_pnl_week + fno_week_pnl +
                    crypto_week_pnl * INR_RATE + us_week_pnl * INR_RATE)

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
        "*NSE Equity Portfolio*",
        f"Value:  `Rs.{portfolio_value:>12,.0f}`",
        f"P&L:    `Rs.{pnl:>+12,.0f}` ({pnl_pct:+.2f}%)",
        f"Open positions: `{len(open_positions)}`",
        "",
        "*This Week — NSE Equity*",
        f"Signals generated: `{total_signals}`",
        f"Trades executed:   `{len(week_trades)}`",
        f"Wins / Losses:     `{len(wins)} / {len(losses)}`",
        f"Win rate:          `{win_rate:.0f}%`",
        f"Week P&L:          `Rs.{total_pnl_week:+,.0f}`",
    ]

    if best and best.get("pnl"):
        lines.append(f"Best:  `{best['symbol']}` `Rs.{best['pnl']:+,.0f}` ({best.get('pnl_pct', 0):+.1f}%)")
    if worst and worst.get("pnl"):
        lines.append(f"Worst: `{worst['symbol']}` `Rs.{worst['pnl']:+,.0f}` ({worst.get('pnl_pct', 0):+.1f}%)")

    lines += [
        "",
        "*This Week — F&O*",
        f"Trades: `{fno_week_count}` | Open: `{fno_open_count}`",
        f"P&L:    `Rs.{fno_week_pnl:+,.0f}`",
        "",
        "*This Week — Crypto*",
        f"Trades: `{crypto_week_count}` | Open: `{crypto_open_count}`",
        f"P&L:    `{crypto_week_pnl:+.2f} USDT` (Rs.{crypto_week_pnl * INR_RATE:+,.0f})",
        "",
        "*This Week — US Stocks*",
        f"Trades: `{us_week_count}` | Open: `{us_open_count}`",
        f"P&L:    `${us_week_pnl:+.2f}` (Rs.{us_week_pnl * INR_RATE:+,.0f})",
        "",
        f"*Combined Week P&L: `Rs.{combined_pnl:+,.0f}`*",
    ]

    if open_positions:
        lines += ["", "*Top NSE Open Positions*"]
        for sym, pos in list(open_positions.items())[:5]:
            lines.append(f"  {sym} | entry Rs.{pos.get('entry', 0):,.0f}")
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
    logger.info(f"Weekly summary sent — {len(week_trades)} NSE + {fno_week_count} F&O + "
                f"{crypto_week_count} crypto + {us_week_count} US trades, "
                f"combined P&L Rs.{combined_pnl:+,.0f}")


if __name__ == "__main__":
    send_weekly_summary()
