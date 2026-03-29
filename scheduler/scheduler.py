# =============================================================================
# scheduler/scheduler.py — Daily scheduler
# Runs the full agent pipeline at 9:15 AM IST every weekday.
# Also sends Telegram alerts with top signals.
#
# Run: python scheduler/scheduler.py
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TRADING_MODE
from utils import get_logger

logger = get_logger("Scheduler")
IST = pytz.timezone("Asia/Kolkata")


def run_daily_scan():
    """Full pipeline — runs every weekday at 9:15 AM IST."""
    logger.info(f"Scheduled scan triggered at {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
    try:
        from main import run_agent
        from memory.portfolio_memory import PortfolioMemory

        signals = run_agent(dry_run=(TRADING_MODE == "paper"))

        memory = PortfolioMemory()
        for sig in signals:
            memory.save_signal(sig)
        summary = memory.get_stats()

        from execution.executor import get_executor
        from config import VIRTUAL_CAPITAL
        exec_ = get_executor()
        pv    = exec_.get_portfolio_value()
        memory.save_snapshot({
            "portfolio_value": pv,
            "cash":            pv,
            "pnl":             pv - VIRTUAL_CAPITAL,
            "pnl_pct":         (pv - VIRTUAL_CAPITAL) / VIRTUAL_CAPITAL * 100,
            "open_positions":  exec_.get_open_positions_count(),
            "total_trades":    summary["total_trades"],
            "win_rate":        summary["win_rate_pct"],
        })

        send_telegram_alert(signals, summary)

    except Exception as e:
        logger.error(f"Scheduled run failed: {e}")
        send_telegram_message(f"Trading Agent ERROR: {e}")


def send_telegram_alert(signals: list, stats: dict):
    """Send top signals to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.info("Telegram not configured — skipping alert")
        return

    date_str = datetime.now(IST).strftime("%d %b %Y")
    lines = [f"*NSE Trading Agent — {date_str}*", f"Mode: {TRADING_MODE.upper()}", ""]

    if not signals:
        lines.append("No BUY signals today.")
    else:
        for i, s in enumerate(signals[:5], 1):
            lines += [
                f"*#{i} {s.symbol}* — {s.action} ({s.confidence:.0%})",
                f"Entry: Rs.{s.entry_price:,.0f} | SL: Rs.{s.stop_loss:,.0f} | TP: Rs.{s.take_profit:,.0f}",
                f"Reason: {s.reasoning[:80]}...",
                "",
            ]

    lines += [
        f"_Win Rate: {stats['win_rate_pct']:.0f}% | Trades: {stats['total_trades']}_"
    ]
    send_telegram_message("\n".join(lines))


def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        logger.info("Telegram alert sent")
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")


if __name__ == "__main__":
    # Read scan times from user settings (configurable from dashboard)
    from config import SCAN_TIME_1, SCAN_TIME_2

    def _parse_time(t: str):
        h, m = t.split(":")
        return int(h), int(m)

    h1, m1 = _parse_time(SCAN_TIME_1)
    h2, m2 = _parse_time(SCAN_TIME_2)

    scheduler = BlockingScheduler(timezone=IST)

    scheduler.add_job(
        run_daily_scan,
        CronTrigger(hour=h1, minute=m1, day_of_week="mon-fri", timezone=IST),
        id="scan_1",
        name=f"Scan 1 ({SCAN_TIME_1} IST)",
    )

    scheduler.add_job(
        run_daily_scan,
        CronTrigger(hour=h2, minute=m2, day_of_week="mon-fri", timezone=IST),
        id="scan_2",
        name=f"Scan 2 ({SCAN_TIME_2} IST)",
    )

    logger.info(f"Scheduler started — scans at {SCAN_TIME_1} and {SCAN_TIME_2} IST (Mon-Fri)")
    logger.info("Press Ctrl+C to stop")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped")
