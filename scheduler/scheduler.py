# =============================================================================
# scheduler/scheduler.py — Master scheduler
#
# Jobs (all IST, Mon-Fri unless noted):
#   09:15  → Full scan (run_daily_scan)     [configurable via dashboard]
#   15:00  → Afternoon scan (run_daily_scan)[configurable via dashboard]
#   Every 15 min 09:15–15:25 → Price monitor (SL/TP exits)
#   15:25  → EOD close of all intraday positions
#   Sunday 20:00 → Weekly performance summary to Telegram
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


# =============================================================================
# JOB 1 & 2 — Full daily scan (9:15 AM and 3:00 PM)
# =============================================================================

def run_daily_scan():
    """Full pipeline — runs every weekday at configured scan times."""
    logger.info(f"Scheduled scan triggered at {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
    try:
        from main import run_agent
        from memory.portfolio_memory import PortfolioMemory

        # dry_run=False: paper mode simulates trades virtually (correct behavior)
        # dry_run=True is only for: python main.py --dry-run (manual override)
        signals = run_agent(dry_run=False)

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
        logger.error(f"Scheduled scan failed: {e}")
        send_telegram_message(f"*Agent ERROR (scan)*\n`{e}`")


# =============================================================================
# JOB 3 — Price monitor every 15 minutes (9:15 AM – 3:25 PM)
# =============================================================================

def run_price_monitor():
    """
    Check all open positions for SL/TP hits and trailing stop updates.
    Runs every 15 minutes during market hours.
    """
    logger.debug(f"Price monitor tick at {datetime.now(IST).strftime('%H:%M IST')}")
    try:
        from execution.price_monitor import PriceMonitor
        results = PriceMonitor().run()

        exits = [r for r in results if r.action in ("SL_HIT", "TP_HIT")]
        trails = [r for r in results if r.action == "TRAIL_UPDATED"]

        if exits:
            logger.info(f"Price monitor: {len(exits)} exits, "
                        f"total P&L Rs.{sum(r.pnl for r in exits):+,.0f}")
        if trails:
            logger.info(f"Price monitor: {len(trails)} trailing stop updates")

    except Exception as e:
        logger.error(f"Price monitor failed: {e}")


# =============================================================================
# JOB 4 — EOD close at 3:25 PM (intraday positions only)
# =============================================================================

def run_eod_close():
    """Force-close all intraday positions at 3:25 PM."""
    logger.info(f"EOD close triggered at {datetime.now(IST).strftime('%H:%M IST')}")
    try:
        from execution.price_monitor import PriceMonitor
        results = PriceMonitor().close_all_intraday()
        if results:
            total_pnl = sum(r.pnl for r in results)
            logger.info(f"EOD: closed {len(results)} intraday positions, "
                        f"P&L Rs.{total_pnl:+,.0f}")
        else:
            logger.info("EOD: no intraday positions to close")
    except Exception as e:
        logger.error(f"EOD close failed: {e}")


# =============================================================================
# JOB 5 — Signal outcome tracker at 3:30 PM (after market close, Mon-Fri)
# =============================================================================

def run_outcome_tracker():
    """Check all open signals — mark TP_HIT / SL_HIT / EXPIRED outcomes."""
    logger.info(f"Outcome tracker triggered at {datetime.now(IST).strftime('%H:%M IST')}")
    try:
        from analysis.outcome_tracker import OutcomeTracker
        result = OutcomeTracker().run()
        logger.info(f"Outcomes: TP={result['tp_hit']} SL={result['sl_hit']} "
                    f"EXPIRED={result['expired']} OPEN={result['still_open']}")
    except Exception as e:
        logger.error(f"Outcome tracker failed: {e}")


# =============================================================================
# JOB 6 — Weekly summary every Sunday 8 PM IST
# =============================================================================

def run_weekly_summary():
    """Send weekly performance report to Telegram."""
    logger.info(f"Weekly summary triggered at {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
    try:
        from automation.weekly_summary import send_weekly_summary
        send_weekly_summary()
    except Exception as e:
        logger.error(f"Weekly summary failed: {e}")
        send_telegram_message(f"*Agent ERROR (weekly summary)*\n`{e}`")


# =============================================================================
# Telegram helper
# =============================================================================

def send_telegram_alert(signals: list, stats: dict):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    date_str = datetime.now(IST).strftime("%d %b %Y")
    lines = [f"*NSE Agent — {date_str}*", f"Mode: {TRADING_MODE.upper()}", ""]

    if not signals:
        lines.append("No BUY signals today.")
    else:
        for i, s in enumerate(signals[:5], 1):
            lines += [
                f"*#{i} {s.symbol}* — {s.action} ({s.confidence:.0%})",
                f"Entry: Rs.{s.entry_price:,.0f} | "
                f"SL: Rs.{s.stop_loss:,.0f} | "
                f"TP: Rs.{s.take_profit:,.0f}",
                f"_{s.reasoning[:80]}_",
                "",
            ]

    lines.append(
        f"Win Rate: {stats['win_rate_pct']:.0f}% | "
        f"Trades: {stats['total_trades']}"
    )
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
        logger.info("Telegram message sent")
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    from config import SCAN_TIME_1, SCAN_TIME_2

    def _parse_time(t: str):
        h, m = t.split(":")
        return int(h), int(m)

    h1, m1 = _parse_time(SCAN_TIME_1)
    h2, m2 = _parse_time(SCAN_TIME_2)

    scheduler = BlockingScheduler(timezone=IST)

    # --- Job 1: Morning scan ---
    scheduler.add_job(
        run_daily_scan,
        CronTrigger(hour=h1, minute=m1, day_of_week="mon-fri", timezone=IST),
        id="scan_1",
        name=f"Morning Scan ({SCAN_TIME_1} IST)",
    )

    # --- Job 2: Afternoon scan ---
    scheduler.add_job(
        run_daily_scan,
        CronTrigger(hour=h2, minute=m2, day_of_week="mon-fri", timezone=IST),
        id="scan_2",
        name=f"Afternoon Scan ({SCAN_TIME_2} IST)",
    )

    # --- Job 3: Price monitor every 15 min, 9:15 AM – 3:25 PM ---
    scheduler.add_job(
        run_price_monitor,
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-15",
            minute="15,30,45,0",
            timezone=IST,
        ),
        id="price_monitor",
        name="Price Monitor (every 15 min)",
    )

    # --- Job 4: EOD close at 3:25 PM ---
    scheduler.add_job(
        run_eod_close,
        CronTrigger(hour=15, minute=25, day_of_week="mon-fri", timezone=IST),
        id="eod_close",
        name="EOD Close (15:25 IST)",
    )

    # --- Job 5: Outcome tracker at 3:30 PM (after market close) ---
    scheduler.add_job(
        run_outcome_tracker,
        CronTrigger(hour=15, minute=30, day_of_week="mon-fri", timezone=IST),
        id="outcome_tracker",
        name="Signal Outcome Tracker (15:30 IST)",
    )

    # --- Job 6: Weekly summary every Sunday 8 PM ---
    scheduler.add_job(
        run_weekly_summary,
        CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=IST),
        id="weekly_summary",
        name="Weekly Summary (Sun 20:00 IST)",
    )

    logger.info("=" * 55)
    logger.info("  QUANTEDGE SCHEDULER STARTED")
    logger.info(f"  Morning scan    : {SCAN_TIME_1} IST (Mon-Fri)")
    logger.info(f"  Afternoon scan  : {SCAN_TIME_2} IST (Mon-Fri)")
    logger.info("  Price monitor   : every 15 min, 09:15-15:25 (Mon-Fri)")
    logger.info("  EOD close       : 15:25 IST (Mon-Fri)")
    logger.info("  Outcome tracker : 15:30 IST (Mon-Fri)")
    logger.info("  Weekly report   : Sunday 20:00 IST")
    logger.info("=" * 55)

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped")
