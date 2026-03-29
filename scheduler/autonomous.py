# =============================================================================
# scheduler/autonomous.py — Master Autonomous Scheduler
#
# Runs the COMPLETE trading agent 24/7 without any manual input.
# Every job is time-triggered. Nothing needs human intervention.
#
# Schedule (IST, Mon-Fri only):
#   09:00 AM  Pre-market check (regime, PCR, FII)
#   09:15 AM  Morning scan + swing trade entry
#   09:30 AM  Intraday scan + entry
#   09:30 AM - 3:25 PM  Price monitor every 15 mins
#   12:30 PM  Midday scan (if < 3 positions open)
#   03:20 PM  Final intraday price check
#   03:25 PM  Close all intraday positions
#   03:30 PM  Daily P&L report to Telegram
#   06:00 PM  Readiness check + snapshot
#   Sunday 10 AM  Weekly backtest + optimiser
#
# Run: python -m scheduler.autonomous
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from datetime import datetime
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from utils import get_logger
from utils.telegram import send

logger = get_logger("AutonomousScheduler")
IST = pytz.timezone("Asia/Kolkata")


# ===========================================================================
# JOB FUNCTIONS
# ===========================================================================

def job_premarket():
    """09:00 AM — Pre-market check. Warn if market is bad."""
    logger.info("=== PRE-MARKET CHECK ===")
    try:
        from analysis.market_regime import MarketRegimeFilter
        from analysis.pcr_signal    import PCRAnalyser
        from analysis.fii_dii       import FIIDIIAnalyser

        regime = MarketRegimeFilter().get_regime()
        pcr    = PCRAnalyser().get_signal()
        fii    = FIIDIIAnalyser().get_signal()

        # Save regime for dashboard
        import json
        os.makedirs("logs", exist_ok=True)
        with open("logs/market_regime.json", "w") as f:
            json.dump({"regime": regime.regime, "rsi": regime.nifty_rsi,
                       "ret_1m": regime.nifty_1m_return}, f)

        lines = [
            f"*Pre-Market Check — {datetime.now(IST).strftime('%d %b')}*",
            f"Market: `{regime.regime.upper()}` | RSI: `{regime.nifty_rsi:.1f}`",
            f"PCR: `{pcr.pcr:.2f}` ({pcr.signal})",
            f"FII: {fii.signal} — {fii.message[:50]}",
        ]
        if not regime.allow_buys:
            lines.append("\n⚠️ *Bear market — no trades today*")
        send("\n".join(lines))
    except Exception as e:
        logger.error(f"Pre-market check failed: {e}")


def job_morning_scan():
    """09:15 AM — Full swing agent scan + trade entry."""
    logger.info("=== MORNING SCAN ===")
    try:
        from main import run_agent
        signals = run_agent(dry_run=False)   # REAL paper trades
        logger.info(f"Morning scan: {len(signals)} signals, trades executed")
    except Exception as e:
        logger.error(f"Morning scan failed: {e}")
        send(f"*Morning Scan Error*\n`{e}`")


def job_intraday_scan():
    """09:30 AM — Intraday scan after market opens."""
    logger.info("=== INTRADAY SCAN ===")
    try:
        # Check if market is in bull regime before intraday
        import json
        with open("logs/market_regime.json") as f:
            reg = json.load(f)
        if reg.get("regime") == "bear":
            logger.info("Bear market — skipping intraday trades")
            return

        from execution.intraday_agent import IntradayAgent
        from memory.portfolio_memory  import PortfolioMemory

        # Get symbols the swing agent liked today
        memory        = PortfolioMemory()
        recent_signals= memory.get_recent_signals(limit=20)
        today         = datetime.now(IST).strftime("%Y-%m-%d")
        swing_symbols = [
            s["symbol"] for s in recent_signals
            if s["timestamp"].startswith(today) and s["action"] == "BUY"
        ]

        agent   = IntradayAgent()
        signals = agent.scan_and_trade(swing_symbols or None)
        logger.info(f"Intraday scan: {len(signals)} signals")
    except Exception as e:
        logger.error(f"Intraday scan failed: {e}")


def job_price_monitor():
    """Every 15 mins — Check SL/TP for all open positions."""
    logger.info("=== PRICE MONITOR ===")
    try:
        from execution.price_monitor import PriceMonitor
        monitor = PriceMonitor()
        results = monitor.run()

        exits = [r for r in results if r.action in ("SL_HIT","TP_HIT")]
        holds = [r for r in results if r.action == "HOLD"]
        trails= [r for r in results if r.action == "TRAIL_UPDATED"]

        if exits or trails:
            logger.info(f"Monitor: {len(exits)} exits, {len(trails)} SL updates, "
                        f"{len(holds)} holding")
    except Exception as e:
        logger.error(f"Price monitor failed: {e}")


def job_midday_scan():
    """12:30 PM — Midday scan if fewer than 3 positions open."""
    logger.info("=== MIDDAY SCAN ===")
    try:
        import json
        with open(os.path.join("logs","virtual_portfolio.json")) as f:
            pf = json.load(f)
        open_count = len(pf.get("positions", {}))

        if open_count >= 3:
            logger.info(f"Midday scan skipped — {open_count} positions already open")
            return

        from main import run_agent
        signals = run_agent(dry_run=False)
        logger.info(f"Midday scan: {len(signals)} new signals")
    except Exception as e:
        logger.error(f"Midday scan failed: {e}")


def job_eod_close():
    """03:25 PM — Force close all intraday positions."""
    logger.info("=== EOD CLOSE ===")
    try:
        from execution.price_monitor import PriceMonitor
        monitor = PriceMonitor()
        # Final SL/TP check
        monitor.run(force=True)
        # Close remaining intraday
        results = monitor.close_all_intraday()
        logger.info(f"EOD: closed {len(results)} intraday positions")
    except Exception as e:
        logger.error(f"EOD close failed: {e}")


def job_daily_report():
    """03:30 PM — Send daily P&L report to Telegram."""
    logger.info("=== DAILY REPORT ===")
    try:
        from execution.daily_report import DailyReporter
        DailyReporter().send_report()
    except Exception as e:
        logger.error(f"Daily report failed: {e}")


def job_evening_check():
    """06:00 PM — Readiness check + portfolio snapshot."""
    logger.info("=== EVENING CHECK ===")
    try:
        from readiness.checker import ReadinessChecker
        from execution.executor import get_executor
        from memory.portfolio_memory import PortfolioMemory

        report   = ReadinessChecker().check()
        executor = get_executor()
        summary  = executor.get_portfolio_summary()
        PortfolioMemory().save_snapshot(summary)

        logger.info(f"Evening: {report.passed_count}/{report.total_gates} gates | "
                    f"Portfolio: Rs.{summary.get('portfolio_value',0):,.0f}")
    except Exception as e:
        logger.error(f"Evening check failed: {e}")


def job_weekly_backtest():
    """Sunday 10:00 AM — Weekly backtest + strategy optimiser."""
    logger.info("=== WEEKLY BACKTEST ===")
    try:
        from backtest.engine import BacktestEngine
        from automation.weekly_summary import send_weekly_summary
        from datetime import timedelta

        engine = BacktestEngine()
        end    = datetime.today().strftime("%Y-%m-%d")
        start  = (datetime.today()-timedelta(days=365*3)).strftime("%Y-%m-%d")
        engine.run_all(
            ["BRITANNIA","TITAN","BAJFINANCE","HDFCBANK","RELIANCE",
             "ICICIBANK","SBIN","AXISBANK","INFY","TCS"],
            start, end
        )
        send_weekly_summary()
        logger.info("Weekly backtest complete")
    except Exception as e:
        logger.error(f"Weekly backtest failed: {e}")


# ===========================================================================
# SCHEDULER SETUP
# ===========================================================================

def start():
    """Start the autonomous scheduler."""
    scheduler = BlockingScheduler(timezone=IST)

    # Pre-market
    scheduler.add_job(job_premarket, CronTrigger(
        hour=9, minute=0, day_of_week="mon-fri", timezone=IST), id="premarket")

    # Morning swing scan
    scheduler.add_job(job_morning_scan, CronTrigger(
        hour=9, minute=15, day_of_week="mon-fri", timezone=IST), id="morning_scan")

    # Intraday entry
    scheduler.add_job(job_intraday_scan, CronTrigger(
        hour=9, minute=30, day_of_week="mon-fri", timezone=IST), id="intraday_scan")

    # Price monitor every 15 mins (9:30 AM – 3:20 PM)
    scheduler.add_job(job_price_monitor, CronTrigger(
        minute="*/15", hour="9-15", day_of_week="mon-fri", timezone=IST),
        id="price_monitor")

    # Midday scan
    scheduler.add_job(job_midday_scan, CronTrigger(
        hour=12, minute=30, day_of_week="mon-fri", timezone=IST), id="midday_scan")

    # EOD close
    scheduler.add_job(job_eod_close, CronTrigger(
        hour=15, minute=25, day_of_week="mon-fri", timezone=IST), id="eod_close")

    # Daily report
    scheduler.add_job(job_daily_report, CronTrigger(
        hour=15, minute=30, day_of_week="mon-fri", timezone=IST), id="daily_report")

    # Evening check
    scheduler.add_job(job_evening_check, CronTrigger(
        hour=18, minute=0, day_of_week="mon-fri", timezone=IST), id="evening_check")

    # Weekly backtest (Sunday)
    scheduler.add_job(job_weekly_backtest, CronTrigger(
        hour=10, minute=0, day_of_week="sun", timezone=IST), id="weekly_backtest")

    # Print schedule
    print("\n" + "="*60)
    print("  AUTONOMOUS TRADING AGENT — STARTED")
    print("="*60)
    print(f"  Mode: PAPER TRADING (virtual Rs.{10_00_000:,.0f})")
    print(f"  Style: Swing (80%) + Intraday (20%)")
    print("")
    print("  Daily Schedule (Mon-Fri IST):")
    print("    09:00 AM  Pre-market check")
    print("    09:15 AM  Morning swing scan + entry")
    print("    09:30 AM  Intraday scan + entry")
    print("    09:30 AM  Price monitor every 15 mins")
    print("    12:30 PM  Midday scan (if < 3 positions)")
    print("    03:25 PM  EOD close all intraday")
    print("    03:30 PM  Daily P&L report → Telegram")
    print("    06:00 PM  Readiness + snapshot")
    print("")
    print("  Weekly (Sunday IST):")
    print("    10:00 AM  Backtest + optimiser + weekly report")
    print("")
    print("  Press Ctrl+C to stop")
    print("="*60 + "\n")

    send("*Autonomous Agent Started*\n"
         "Paper trading active — real data, virtual money\n"
         "Schedule: 9:15 AM scan, 15-min monitor, 3:30 PM report")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Autonomous scheduler stopped")
        send("*Autonomous Agent Stopped*")


if __name__ == "__main__":
    start()
