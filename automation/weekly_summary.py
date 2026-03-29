# automation/weekly_summary.py
# Sends a weekly performance summary to Telegram every Sunday
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from memory.portfolio_memory import PortfolioMemory
from utils.telegram import send
import json

def send_weekly_summary():
    memory = PortfolioMemory()
    stats  = memory.get_stats()
    snaps  = memory.get_snapshots()

    # Portfolio value
    pf_file = "logs/virtual_portfolio.json"
    cash    = 1_000_000
    if os.path.exists(pf_file):
        with open(pf_file) as f:
            cash = json.load(f).get("cash", 1_000_000)

    pnl     = cash - 1_000_000
    pnl_pct = pnl / 1_000_000 * 100

    # Readiness
    gates_passed = 0
    gates_total  = 8
    if os.path.exists("logs/readiness_report.json"):
        with open("logs/readiness_report.json") as f:
            r = json.load(f)
            gates_passed = r["passed"]
            gates_total  = r["total"]

    week = datetime.now().strftime("%d %b %Y")
    msg  = f"""*Weekly Trading Agent Report*
_Week ending {week}_

*Portfolio*
Value: `Rs.{cash:,.0f}`
P&L: `Rs.{pnl:+,.0f}` ({pnl_pct:+.2f}%)

*Performance*
Trades: `{stats['total_trades']}`
Win Rate: `{stats['win_rate_pct']:.1f}%`
Profit Factor: `{stats['profit_factor']:.2f}`
Max Drawdown: `{stats['max_drawdown_pct']:.1f}%`

*Phase 2 Readiness*
Gates: `{gates_passed}/{gates_total}` passed
{"Ready to go live!" if gates_passed == gates_total else f"~{max(0,(gates_total-gates_passed)*5)} more days"}

_Keep running the agent daily!_"""

    send(msg)
    print("Weekly summary sent to Telegram")

if __name__ == "__main__":
    send_weekly_summary()
