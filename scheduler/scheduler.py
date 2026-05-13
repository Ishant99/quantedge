# =============================================================================
# scheduler/scheduler.py — Master scheduler
#
# Jobs (all IST, Mon-Fri unless noted):
#   09:15        → NSE morning scan
#   15:00        → NSE afternoon scan
#   09:30–14:30  → Intraday scan (hourly)
#   Every 15 min → Price monitor + F&O paper monitor
#   15:25        → EOD close of all intraday positions
#   15:30        → Signal outcome tracker
#   19:00        → US stocks scan (Mon-Fri)
#   Every 4h     → Crypto scan (24/7)
#   Sunday 02:00 → Walk-forward strategy optimizer (saves to logs/optimiser_results.json)
#   Sunday 20:00 → Weekly performance summary to Telegram
#
# Run: python scheduler/scheduler.py
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import threading
import time
from datetime import datetime, date
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
import requests

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TRADING_MODE,
    US_USD_PER_TRADE, CRYPTO_USDT_PER_TRADE,
)
from utils import get_logger
from utils.housekeeping import cleanup_runtime_artifacts
from utils.discord import send as send_discord_message_raw
from services.runtime_state import (
    PID_FILE,
    acquire_pid_file as svc_acquire_pid_file,
    release_pid_file as svc_release_pid_file,
    write_scheduler_status as svc_write_scheduler_status,
)
from services.state_sync import sync_unified_state

logger = get_logger("Scheduler")
IST = pytz.timezone("Asia/Kolkata")

# =============================================================================
# NSE Holiday Calendar 2025–2026
# Source: NSE India official trading calendar
# =============================================================================
NSE_HOLIDAYS: set[date] = {
    # 2025
    date(2025, 1, 26),   # Republic Day
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr (Ramadan Eid)
    date(2025, 4, 14),   # Dr. Ambedkar Jayanti / Ram Navami
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10, 2),   # Gandhi Jayanti / Dussehra
    date(2025, 10, 21),  # Diwali Laxmi Puja (Muhurat Trading day — partial)
    date(2025, 10, 22),  # Diwali Balipratipada
    date(2025, 11, 5),   # Prakash Gurpurb (Gurunanak Jayanti)
    date(2025, 12, 25),  # Christmas
    # 2026
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 20),   # Holi (approx — confirm with NSE)
    date(2026, 4, 3),    # Good Friday (approx)
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 11, 14),  # Gurunanak Jayanti (approx)
    date(2026, 12, 25),  # Christmas
}


def _is_nse_holiday(dt: datetime | None = None) -> bool:
    """Return True if today (or given dt) is an NSE trading holiday."""
    check = (dt or datetime.now(IST)).date()
    return check in NSE_HOLIDAYS


# =============================================================================
# Scan lock — prevents price_monitor / F&O monitor from running while a
# full scan is active (avoids portfolio lock contention and double signals).
# Uses a threading.Lock (not Event) so that check+set is atomic.
# =============================================================================
_SCAN_LOCK = threading.Lock()
_SCAN_ACTIVE = False
_SCAN_STATE_LOCK = threading.Lock()


def _acquire_scan_lock(job_name: str) -> bool:
    """Try to mark scan as running. Returns False if already locked (non-blocking)."""
    global _SCAN_ACTIVE
    with _SCAN_STATE_LOCK:
        if _SCAN_ACTIVE:
            logger.warning(f"{job_name}: scan lock held — skipping to avoid overlap")
            return False
        _SCAN_ACTIVE = True
        return True


def _release_scan_lock():
    global _SCAN_ACTIVE
    with _SCAN_STATE_LOCK:
        _SCAN_ACTIVE = False


def _release_pid_file():
    svc_release_pid_file(PID_FILE)


def _acquire_pid_file() -> bool:
    ok, message = svc_acquire_pid_file(PID_FILE)
    if message:
        if ok:
            logger.warning(message)
        else:
            logger.error(message)
    return ok


def run_housekeeping():
    """Trim stale logs/caches so Oracle Free VM storage stays healthy."""
    _write_scheduler_status("housekeeping", "running")
    try:
        summary = cleanup_runtime_artifacts()
        logger.info(
            f"Housekeeping complete | removed={summary.get('removed_files', 0)} | "
            f"logs={summary.get('log_files', 0)} ({summary.get('log_size_mb', 0.0):.2f} MB) | "
            f"cache={summary.get('cache_size_mb', 0.0):.2f} MB | "
            f"db={summary.get('db_size_mb', 0.0):.2f} MB"
        )
        _write_scheduler_status("housekeeping", "ok", f"Removed files: {summary.get('removed_files', 0)}")
    except Exception as e:
        _write_scheduler_status("housekeeping", "error", str(e))
        logger.warning(f"Housekeeping failed: {e}")


def _write_scheduler_status(job_name: str, state: str, detail: str = ""):
    svc_write_scheduler_status(job_name, state, detail)


def _sync_state(label: str):
    try:
        state = sync_unified_state()
        logger.info(
            f"Unified state synced after {label} | "
            f"positions={len(state.get('positions', []))} "
            f"trades={len(state.get('trades', []))} "
            f"signals={len(state.get('signals', []))}"
        )
    except Exception as e:
        logger.warning(f"Unified state sync failed after {label}: {e}")


# =============================================================================
# JOB 0 — GIFT Nifty pre-market check (8:30 AM IST)
# =============================================================================

def run_gift_nifty_check():
    """Check GIFT Nifty gap before market open — sets expectation for the day."""
    if _is_nse_holiday():
        return
    _write_scheduler_status("gift_nifty", "running")
    try:
        from analysis.gift_nifty import GiftNiftyAnalyser
        result = GiftNiftyAnalyser().get_signal()
        logger.info(f"[GIFT] {result.message}")

        # Only alert if gap is meaningful (not flat)
        if result.signal != "flat":
            icon = "📈" if "up" in result.signal else "📉"
            send_telegram_message(
                f"{icon} *Pre-Market Gap Alert*\n"
                f"{result.message}\n"
                f"_Market opens in ~45 min_"
            )
        _write_scheduler_status("gift_nifty", "ok", f"Gap: {result.gap_pct:+.2f}%")
    except Exception as e:
        _write_scheduler_status("gift_nifty", "error", str(e))
        logger.warning(f"GIFT Nifty check failed: {e}")


# =============================================================================
# JOB 1 & 2 — Full daily scan (9:15 AM and 3:00 PM)
# =============================================================================

def _preflight_check() -> bool:
    """Quick connectivity check — fetch 1 day of Nifty data via yfinance."""
    try:
        import yfinance as _yf
        df = _yf.Ticker("^NSEI").history(period="1d", interval="1d")
        if df.empty:
            logger.warning("Pre-flight: Nifty data returned empty — market may be closed")
        return True   # non-empty OR empty both mean network is reachable
    except Exception as e:
        logger.warning(f"Pre-flight check failed: {e}")
        return False


def run_daily_scan():
    """Full pipeline — runs every weekday at configured scan times."""
    # Skip NSE holidays
    if _is_nse_holiday():
        logger.info(f"NSE holiday today ({date.today()}) — skipping scan")
        _write_scheduler_status("daily_scan", "skipped", "NSE holiday")
        return

    if not _acquire_scan_lock("run_daily_scan"):
        return

    _write_scheduler_status("daily_scan", "running")
    logger.info(f"Scheduled scan triggered at {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
    if not _preflight_check():
        _release_scan_lock()
        _write_scheduler_status("daily_scan", "skipped", "Pre-flight failed")
        send_telegram_message("*Pre-flight FAILED* — skipping scan (network issue?)")
        return
    try:
        from pipeline.legacy import run_pipeline
        from memory.portfolio_memory import PortfolioMemory

        # dry_run=False: paper mode simulates trades virtually (correct behavior)
        # dry_run=True is only for: python main.py --dry-run (manual override)
        signals = run_pipeline(dry_run=False)  # tries TradingPipeline, falls back to run_agent

        # Options signals — Nifty/BankNifty weekly CE/PE ideas
        _run_options_signals()
        # Futures signals — Nifty/BankNifty directional futures
        _run_futures_signals()
        # Options selling — straddle/strangle (Tue/Wed/Thu only)
        _run_selling_signals()
        # Iron Condor — high-VIX spread (Mon–Wed, VIX >= threshold)
        _run_iron_condor_signals()
        _sync_state("daily_scan")
        _write_scheduler_status("daily_scan", "ok", f"Signals processed: {len(signals)}")

    except Exception as e:
        _write_scheduler_status("daily_scan", "error", str(e))
        logger.error(f"Scheduled scan failed: {e}")
        # Retry once after 60s for transient errors (e.g. empty API response)
        import time as _time
        _time.sleep(60)
        try:
            from pipeline.legacy import run_pipeline
            signals = run_pipeline(dry_run=False)
            _run_options_signals()
            _run_futures_signals()
            _run_selling_signals()
            _run_iron_condor_signals()
            _sync_state("daily_scan_retry")
            _write_scheduler_status("daily_scan", "ok", f"Recovered on retry: {len(signals)} signals")
            logger.info("Scheduled scan recovered on retry")
        except Exception as e2:
            _write_scheduler_status("daily_scan", "error", f"Retry also failed: {e2}")
            logger.error(f"Scheduled scan retry also failed: {e2}")
            send_telegram_message(f"*Agent ERROR (scan)*\n`{e2}`")
    finally:
        _release_scan_lock()


def _run_options_signals():
    """Generate Nifty/BankNifty options signals, open paper positions, send Telegram."""
    try:
        from analysis.options_signals import OptionsSignalGenerator
        from execution.brokers.fno_paper_broker import FNOPaperBroker

        opt_signals = OptionsSignalGenerator().run()
        if not opt_signals:
            logger.info("Options signals: no clear directional bias today")
            return

        broker = FNOPaperBroker()
        lines  = ["*Nifty/BankNifty Options — Paper Trade Opened*", ""]

        for s in opt_signals:
            # Open paper position with live premium if available
            trade_id = broker.open_position(
                index         = s.index,
                direction     = s.direction,
                strike        = s.strike,
                expiry        = s.expiry,
                lots          = 1,
                entry_premium = s.entry_premium if s.entry_premium > 0 else None,
                reasoning     = s.reasoning,
            )

            arrow = "▲" if s.direction == "CALL" else "▼"
            status = f"Paper #{trade_id}" if trade_id else "Skipped (cap/DTE/dup — see logs)"
            lines += [
                f"*{s.index} {arrow} {s.direction} {s.strike}*  |  Expiry {s.expiry}",
                f"Spot {s.index_spot:,.0f}  |  Entry {s.entry_zone}",
                f"SL (idx) {s.stop_loss_idx:,.0f}  |  Target {s.target_idx:,.0f}",
                f"_{s.iv_note}_",
                f"Status: {status}",
                "",
            ]

        lines.append("_SL=50% premium loss | TP=100% premium gain_")
        send_telegram_message("\n".join(lines))
        logger.info(f"Options signals: {len(opt_signals)} processed")
    except Exception as e:
        logger.warning(f"Options signals failed: {e}")


def run_fno_monitor():
    """Check all open F&O paper positions (options + futures) for TP/SL/expiry."""
    if _is_nse_holiday():
        return
    _write_scheduler_status("fno_monitor", "running")
    try:
        from execution.brokers.fno_paper_broker import FNOPaperBroker
        broker = FNOPaperBroker()
        closed = broker.monitor_and_exit() + broker.monitor_futures() + broker.monitor_selling()
        if closed:
            lines = ["*F&O Paper — Position Closed*", ""]
            for t in closed:
                lines.append(
                    f"{t['index']} {t['strike'] or 'FUT'}{t['option_type']} | "
                    f"{t['reason']} | P&L Rs.{t['pnl']:+,.0f} ({t['pnl_pct']:+.1f}%)"
                )
            send_telegram_message("\n".join(lines))
            logger.info(f"F&O monitor: closed {len(closed)} positions")
        _sync_state("fno_monitor")
        _write_scheduler_status("fno_monitor", "ok", f"Closed positions: {len(closed)}")
    except Exception as e:
        _write_scheduler_status("fno_monitor", "error", str(e))
        logger.error(f"F&O monitor failed: {e}")


def _run_selling_signals():
    """Generate straddle/strangle sell ideas — only Tue/Wed/Thu of expiry week."""
    try:
        from analysis.options_selling import OptionsSellingGenerator
        from execution.brokers.fno_paper_broker import FNOPaperBroker
        signals = OptionsSellingGenerator().run()
        if not signals:
            return
        broker = FNOPaperBroker()
        lines  = ["*Options Selling — Paper Trade*", ""]
        for s in signals:
            ce_id, pe_id = broker.open_selling_position(
                index=s.index, ce_strike=s.ce_strike, pe_strike=s.pe_strike,
                ce_premium=s.ce_premium, pe_premium=s.pe_premium,
                expiry=s.expiry, lots=1, strategy=s.strategy,
                reasoning=s.reasoning,
            )
            lines += [
                f"*{s.index} {s.strategy}* | Sell CE {s.ce_strike} + PE {s.pe_strike}",
                f"Collect Rs.{s.total_premium:.0f}/lot | Expiry {s.expiry}",
                f"BE: {s.breakeven_lower:.0f}–{s.breakeven_upper:.0f}",
                f"SL if premium > Rs.{s.sl_premium:.0f} | TP at Rs.{s.tp_premium:.0f}",
                f"_{s.iv_note}_",
                "",
            ]
        lines.append("_Theta decay strategy — max profit if index stays in range_")
        send_telegram_message("\n".join(lines))
    except Exception as e:
        logger.warning(f"Selling signals failed: {e}")


def _run_iron_condor_signals():
    """
    Generate Iron Condor signals when India VIX is elevated (>= VIX_MIN).
    Only fires Mon–Wed (3+ days to expiry).
    """
    try:
        from analysis.iron_condor import IronCondorGenerator
        signals = IronCondorGenerator().run()
        if not signals:
            return
        lines = ["*Iron Condor — High VIX Options Spread*", ""]
        for s in signals:
            lines += [
                f"*{s.index} Iron Condor* | VIX {s.vix:.1f} | Expiry {s.expiry}",
                f"Sell {s.short_put}P + Buy {s.long_put}P",
                f"Sell {s.short_call}C + Buy {s.long_call}C",
                f"Net Credit ≈ {s.net_credit:.0f} pts | Max Loss ≈ {s.max_loss:.0f} pts",
                f"BE Range: {s.breakeven_lower:.0f}–{s.breakeven_upper:.0f}",
                f"R:R = {s.reward_risk:.2f} | Conf {s.confidence:.0%}",
                f"_{s.iv_note}_",
                "",
            ]
        lines.append("_Iron Condor: max profit if index stays within short strikes at expiry_")
        send_telegram_message("\n".join(lines))
        logger.info(f"Iron Condor: {len(signals)} signal(s) sent")
    except Exception as e:
        logger.warning(f"Iron Condor signals failed: {e}")


def _run_futures_signals():
    """Generate Nifty/BankNifty futures signals and open paper positions."""
    try:
        from analysis.futures_signals import FuturesSignalGenerator
        from execution.brokers.fno_paper_broker import FNOPaperBroker
        signals = FuturesSignalGenerator().run()
        if not signals:
            return
        broker = FNOPaperBroker()
        opened_lines = []
        for s in signals:
            tid = broker.open_futures(
                index=s.index, direction=s.direction,
                expiry=s.expiry, lots=1, reasoning=s.reasoning,
            )
            if tid:
                arrow = "▲ LONG" if s.direction == "LONG" else "▼ SHORT"
                opened_lines += [
                    f"*{s.index} FUT {arrow}* | Expiry {s.expiry}",
                    f"Entry {s.entry_price:,.0f} | SL {s.sl_price:,.0f} | Target {s.target_price:,.0f}",
                    f"Conf {s.confidence:.0%} | Paper #{tid}",
                    "",
                ]
            else:
                logger.info(f"Futures signal {s.index} {s.direction} skipped (cap/dup/treasury)")
        # Only notify if at least one position was actually opened
        if opened_lines:
            lines = ["*Nifty/BankNifty Futures — Paper Trade Opened*", ""] + opened_lines
            send_telegram_message("\n".join(lines))
    except Exception as e:
        logger.warning(f"Futures signals failed: {e}")


# =============================================================================
# JOB 3 — Price monitor every 15 minutes (9:15 AM – 3:25 PM)
# =============================================================================

def run_price_monitor():
    """
    Check all open positions for SL/TP hits and trailing stop updates.
    Runs every 15 minutes during market hours.
    """
    if _is_nse_holiday():
        return
    # Skip if a full scan is currently running to avoid portfolio lock contention
    with _SCAN_STATE_LOCK:
        scan_running = _SCAN_ACTIVE
    if scan_running:
        logger.debug("Price monitor: scan lock held — skipping this tick")
        return
    _write_scheduler_status("price_monitor", "running")
    logger.debug(f"Price monitor tick at {datetime.now(IST).strftime('%H:%M IST')}")
    try:
        from execution.price_monitor import PriceMonitor
        results = PriceMonitor().run()

        exits  = [r for r in results if r.action in ("SL_HIT", "TP_HIT")]
        trails = [r for r in results if r.action == "TRAIL_UPDATED"]

        if exits:
            total_pnl = sum(r.pnl for r in exits)
            logger.info(f"Price monitor: {len(exits)} exits, P&L Rs.{total_pnl:+,.0f}")
            lines = ["*NSE Price Alert — Position Closed*", ""]
            for r in exits:
                emoji = "✅" if r.pnl > 0 else "❌"
                lines.append(f"{emoji} {r.symbol} | {r.action} | P&L Rs.{r.pnl:+,.0f}")
            lines.append(f"\nTotal: Rs.{total_pnl:+,.0f}")
            send_telegram_message("\n".join(lines))
        if trails:
            logger.info(f"Price monitor: {len(trails)} trailing stop updates")
        _sync_state("price_monitor")
        _write_scheduler_status(
            "price_monitor",
            "ok",
            f"Exits: {len(exits)} | Trail updates: {len(trails)}",
        )

    except Exception as e:
        _write_scheduler_status("price_monitor", "error", str(e))
        logger.error(f"Price monitor failed: {e}")


# =============================================================================
# JOB 4 — EOD close at 3:25 PM (intraday positions only)
# =============================================================================

def run_eod_close():
    """Force-close all intraday positions at 3:25 PM."""
    if _is_nse_holiday():
        return
    _write_scheduler_status("eod_close", "running")
    logger.info(f"EOD close triggered at {datetime.now(IST).strftime('%H:%M IST')}")
    try:
        from execution.price_monitor import PriceMonitor
        results = PriceMonitor().close_all_intraday()
        if results:
            total_pnl = sum(r.pnl for r in results)
            logger.info(f"EOD: closed {len(results)} intraday positions, "
                        f"P&L Rs.{total_pnl:+,.0f}")
            lines = ["*EOD Intraday Close — 15:25 IST*", ""]
            for r in results:
                emoji = "✅" if r.pnl > 0 else "❌"
                lines.append(f"{emoji} {r.symbol} | EOD | Rs.{r.pnl:+,.0f}")
            lines.append(f"\nIntraday P&L today: Rs.{total_pnl:+,.0f}")
            send_telegram_message("\n".join(lines))
        else:
            logger.info("EOD: no intraday positions to close")
        _sync_state("eod_close")
        _write_scheduler_status("eod_close", "ok", f"Closed positions: {len(results)}")
    except Exception as e:
        _write_scheduler_status("eod_close", "error", str(e))
        logger.error(f"EOD close failed: {e}")


# =============================================================================
# JOB — Daily EOD digest at 6:00 PM (all markets)
# =============================================================================

def run_eod_digest():
    """Send 6 PM all-market daily summary to Telegram."""
    _write_scheduler_status("eod_digest", "running")
    logger.info(f"EOD digest at {datetime.now(IST).strftime('%H:%M IST')}")
    try:
        import sqlite3, os
        from config import SQLITE_DB_FILE, INR_PER_USD, INR_PER_USDT

        today = datetime.now(IST).strftime("%Y-%m-%d")
        date_label = datetime.now(IST).strftime("%d %b %Y")

        counts = {"nse": 0, "fno": 0, "crypto": 0, "us": 0}
        pnls   = {"nse": 0.0, "fno": 0.0, "crypto": 0.0, "us": 0.0}

        if os.path.exists(SQLITE_DB_FILE):
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                for key, table, pnl_col in [
                    ("nse",    "trades",        "pnl"),
                    ("fno",    "fno_trades",    "pnl"),
                    ("crypto", "crypto_trades", "pnl_usdt"),
                    ("us",     "us_trades",     "pnl_usd"),
                ]:
                    try:
                        row = conn.execute(
                            f"SELECT COUNT(*), COALESCE(SUM({pnl_col}),0) "
                            f"FROM {table} WHERE status='closed' AND exit_time LIKE ?",
                            (f"{today}%",)
                        ).fetchone()
                        counts[key], pnls[key] = row[0], row[1]
                    except Exception as _e:
                        logger.debug(f"EOD digest: {table} not available yet — {_e}")

        nse_count,    nse_pnl    = counts["nse"],    pnls["nse"]
        fno_count,    fno_pnl    = counts["fno"],    pnls["fno"]
        crypto_count, crypto_pnl = counts["crypto"], pnls["crypto"]
        us_count,     us_pnl     = counts["us"],     pnls["us"]

        combined = (nse_pnl + fno_pnl +
                    crypto_pnl * INR_PER_USDT + us_pnl * INR_PER_USD)

        lines = [
            f"*Daily Summary — {date_label}*",
            "",
            f"NSE Equity : {nse_count} trades | Rs.{nse_pnl:+,.0f}",
            f"F&O Paper  : {fno_count} trades | Rs.{fno_pnl:+,.0f}",
            f"Crypto     : {crypto_count} trades | {crypto_pnl:+.2f} USDT",
            f"US Stocks  : {us_count} trades | ${us_pnl:+.2f}",
            "",
            f"*Combined P&L: Rs.{combined:+,.0f}*",
        ]
        send_telegram_message("\n".join(lines))
        logger.info(f"EOD digest sent — combined P&L Rs.{combined:+,.0f}")
        _write_scheduler_status("eod_digest", "ok", f"Combined P&L: Rs.{combined:+,.0f}")
    except Exception as e:
        _write_scheduler_status("eod_digest", "error", str(e))
        logger.error(f"EOD digest failed: {e}")


# =============================================================================
# JOB 5 — Intraday scan every hour 9:30–14:30 (Mon-Fri)
# =============================================================================

def run_thesis_check():
    """Re-evaluate held positions — sell if thesis has degraded significantly."""
    if _is_nse_holiday():
        return
    _write_scheduler_status("thesis_check", "running")
    logger.info(f"Thesis check at {datetime.now(IST).strftime('%H:%M IST')}")
    try:
        import json
        from config import VIRTUAL_PORTFOLIO_FILE
        from execution.portfolio_lock import load_portfolio_locked
        from execution.executor import get_executor
        from strategy.engine import StrategyEngine
        from analysis.technical_agent import TechnicalAgent
        from analysis.sentiment_agent import SentimentAgent

        portfolio = load_portfolio_locked(VIRTUAL_PORTFOLIO_FILE)
        if not portfolio:
            _write_scheduler_status("thesis_check", "ok", "No portfolio")
            return

        positions = portfolio.get("positions", {})
        # Only check swing positions (not INTRA: prefixed)
        held = {sym: pos for sym, pos in positions.items()
                if not sym.startswith("INTRA:") and pos.get("entry_confidence", 0) > 0}
        if not held:
            logger.info("Thesis check: no swing positions with entry_confidence")
            _write_scheduler_status("thesis_check", "ok", "No held positions to check")
            return

        logger.info(f"Thesis check: re-evaluating {len(held)} held positions")
        import yfinance as yf
        ta_agent = TechnicalAgent()
        sent_agent = SentimentAgent()
        engine = StrategyEngine()
        executor = get_executor()
        sells = 0

        for sym, pos in held.items():
            try:
                # Fetch fresh data for this symbol
                df = yf.Ticker(f"{sym}.NS").history(period="6mo", auto_adjust=True)
                if df is None or df.empty:
                    continue
                df.columns = [c.lower() for c in df.columns]
                ta_result = ta_agent.analyse(sym, df)
                sent_result = sent_agent.analyse(sym)

                signal = engine.generate(
                    ta=ta_result, sentiment=sent_result, df=df,
                    held_position=True,
                    entry_confidence=pos["entry_confidence"],
                )
                if signal.action == "SELL":
                    # Use current price for the sell
                    signal.entry_price = float(df["close"].iloc[-1])
                    result = executor.execute(signal)
                    if result.get("status") == "filled":
                        sells += 1
                        logger.info(f"Thesis SELL {sym}: {signal.reasoning}")
            except Exception as e:
                logger.debug(f"Thesis check error for {sym}: {e}")

        _sync_state("thesis_check")
        _write_scheduler_status("thesis_check", "ok", f"Checked: {len(held)}, Sells: {sells}")
        if sells:
            send_telegram_message(
                f"*Thesis Re-evaluation*\n"
                f"Checked {len(held)} positions, exited {sells} "
                f"(confidence degraded)"
            )
    except Exception as e:
        _write_scheduler_status("thesis_check", "error", str(e))
        logger.error(f"Thesis check failed: {e}")


def run_intraday_scan():
    """15-min EMA/VWAP intraday signals on top swing candidates."""
    if _is_nse_holiday():
        return
    _write_scheduler_status("intraday_scan", "running")
    logger.info(f"Intraday scan at {datetime.now(IST).strftime('%H:%M IST')}")
    try:
        from execution.intraday_agent import IntradayAgent
        # Use last daily scan's top symbols if available, else defaults
        agent   = IntradayAgent()
        signals = agent.scan_and_trade()
        if signals:
            logger.info(f"Intraday: {len(signals)} entries placed")
        else:
            logger.info("Intraday: no setups found this hour")
        _sync_state("intraday_scan")
        _write_scheduler_status("intraday_scan", "ok", f"Signals: {len(signals)}")
    except Exception as e:
        _write_scheduler_status("intraday_scan", "error", str(e))
        logger.error(f"Intraday scan failed: {e}")


# =============================================================================
# JOB 6 — Signal outcome tracker at 3:30 PM (after market close, Mon-Fri)
# =============================================================================

def run_outcome_tracker():
    """Check all open signals — mark TP_HIT / SL_HIT / EXPIRED outcomes."""
    if _is_nse_holiday():
        return
    _write_scheduler_status("outcome_tracker", "running")
    logger.info(f"Outcome tracker triggered at {datetime.now(IST).strftime('%H:%M IST')}")
    try:
        from analysis.outcome_tracker import OutcomeTracker
        result = OutcomeTracker().run()
        logger.info(f"Outcomes: TP={result['tp_hit']} SL={result['sl_hit']} "
                    f"EXPIRED={result['expired']} OPEN={result['still_open']}")
        _sync_state("outcome_tracker")
        _write_scheduler_status(
            "outcome_tracker",
            "ok",
            f"TP={result['tp_hit']} SL={result['sl_hit']} EXP={result['expired']}",
        )
    except Exception as e:
        _write_scheduler_status("outcome_tracker", "error", str(e))
        logger.error(f"Outcome tracker failed: {e}")


# =============================================================================
# JOB 6 — Weekly summary every Sunday 8 PM IST
# =============================================================================

def run_us_scan():
    """US stocks scan — runs at 7:00 PM IST (US market open), Mon-Fri."""
    _write_scheduler_status("us_scan", "running")
    logger.info(f"US scan at {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
    try:
        from data.us_scanner import USScanner
        from analysis.technical_agent import TechnicalAgent
        from execution.brokers.us_paper_broker import USPaperBroker
        from config import MIN_CONFIDENCE

        scanner     = USScanner()
        market_data = scanner.run(max_workers=15)
        if not market_data:
            logger.warning("US scan: no data returned")
            _write_scheduler_status("us_scan", "skipped", "No market data returned")
            return

        ta_results = TechnicalAgent().analyse_all(market_data)
        broker     = USPaperBroker()

        # Monitor existing positions
        closed = broker.monitor_and_exit()
        if closed:
            lines = ["*US Stocks — Position Closed*", ""]
            for t in closed:
                lines.append(f"{t['symbol']} {t['direction']} | {t['reason']} | "
                             f"P&L ${t['pnl_usd']:+.2f} ({t['pnl_pct']:+.1f}%)")
            send_telegram_message("\n".join(lines))

        # New signals — use ta.score/10 as confidence (TAResult has no .confidence)
        new_signals = []
        for symbol, ta in ta_results.items():
            ta_conf = ta.score / 10.0
            if not ta.tradeable or ta_conf < MIN_CONFIDENCE:
                continue
            if ta.signal == "bullish":
                tid = broker.open_position(
                    symbol=symbol, direction="LONG", usd_amount=US_USD_PER_TRADE,
                    reasoning=f"US LONG | TA {ta.score:.1f} | {ta.reasoning[:80]}",
                )
                if tid:
                    new_signals.append((symbol, "LONG", tid))

        if new_signals:
            lines = [f"*US Stocks Scan — {len(new_signals)} signals*", ""]
            for sym, direction, tid in new_signals[:5]:
                price = scanner.get_current_price(sym) or 0
                lines.append(f"{sym} {direction} @ ${price:.2f} | Paper #{tid}")
            send_telegram_message("\n".join(lines))

        stats = broker.get_stats()
        detail = (
            f"Universe: {len(market_data)} | Analysed: {len(ta_results)} | "
            f"New signals: {len(new_signals)} | Closed: {len(closed)} | "
            f"Open positions: {stats['open_positions']}"
        )
        if not new_signals:
            logger.info(f"US scan: no new trades | {detail}")
        else:
            logger.info(f"US scan complete | {detail}")
        _sync_state("us_scan")
        _write_scheduler_status("us_scan", "ok", detail)
    except Exception as e:
        _write_scheduler_status("us_scan", "error", str(e))
        logger.error(f"US scan failed: {e}")


def run_crypto_scan():
    """Crypto market scan — runs every 4 hours, 24/7."""
    _write_scheduler_status("crypto_scan", "running")
    logger.info(f"Crypto scan at {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
    try:
        from data.crypto_scanner import CryptoScanner
        from analysis.technical_agent import TechnicalAgent
        from analysis.btc_dominance import BTCDominanceFilter
        from execution.brokers.crypto_paper_broker import CryptoPaperBroker

        scanner     = CryptoScanner()
        market_data = scanner.run(max_workers=10)
        if not market_data:
            logger.warning("Crypto scan: no data returned")
            _write_scheduler_status("crypto_scan", "skipped", "No market data returned")
            return

        # BTC Dominance filter — restrict to blue chips when BTC.D > threshold
        dominance_filter = BTCDominanceFilter()
        dominance        = dominance_filter.get_dominance()
        allowed_symbols  = dominance_filter.filter_symbols(list(market_data.keys()), dominance)
        market_data      = {s: df for s, df in market_data.items() if s in allowed_symbols}

        if dominance.blue_chip_only:
            send_telegram_message(
                f"📊 *BTC Dominance Alert*\n"
                f"BTC.D = {dominance.btc_dominance:.1f}% — altcoins suppressed.\n"
                f"Crypto scan restricted to BTCUSDT + ETHUSDT only."
            )

        ta_results  = TechnicalAgent().analyse_all(market_data)
        broker      = CryptoPaperBroker()

        # Close positions hitting TP/SL
        closed = broker.monitor_and_exit()
        if closed:
            lines = ["*Crypto — Position Closed*", ""]
            for t in closed:
                lines.append(f"{t['symbol']} {t['direction']} | {t['reason']} | "
                             f"P&L {t['pnl_usdt']:+.2f} USDT ({t['pnl_pct']:+.1f}%)")
            send_telegram_message("\n".join(lines))

        # New signals — use ta.score/10 as confidence (TAResult has no .confidence)
        from config import MIN_CONFIDENCE
        new_signals = []
        for symbol, ta in ta_results.items():
            ta_conf = ta.score / 10.0
            if not ta.tradeable or ta_conf < MIN_CONFIDENCE:
                continue
            if ta.signal == "bullish":
                tid = broker.open_position(
                    symbol=symbol, direction="LONG",
                    usdt_amount=CRYPTO_USDT_PER_TRADE,
                    reasoning=f"Crypto LONG | TA {ta.score:.1f} | {ta.reasoning[:80]}",
                )
                if tid:
                    new_signals.append((symbol, "LONG", tid))
            elif ta.signal == "bearish":
                tid = broker.open_position(
                    symbol=symbol, direction="SHORT",
                    usdt_amount=CRYPTO_USDT_PER_TRADE,
                    reasoning=f"Crypto SHORT | TA {ta.score:.1f} | {ta.reasoning[:80]}",
                )
                if tid:
                    new_signals.append((symbol, "SHORT", tid))

        if new_signals:
            lines = [f"*Crypto Scan — {len(new_signals)} signals*", ""]
            for sym, direction, tid in new_signals[:5]:
                price = scanner.get_current_price(sym) or 0
                lines.append(f"{sym} {direction} @ {price:.4f} USDT | Paper #{tid}")
            send_telegram_message("\n".join(lines))

        stats = broker.get_stats()
        detail = (
            f"Universe: {len(market_data)} | Analysed: {len(ta_results)} | "
            f"New signals: {len(new_signals)} | Closed: {len(closed)} | "
            f"Open positions: {stats['open_positions']} | "
            f"Total P&L: {stats['total_pnl_usdt']:+.2f} USDT"
        )
        if not new_signals:
            logger.info(f"Crypto scan: no new trades | {detail}")
        else:
            logger.info(f"Crypto scan complete | {detail}")
        _sync_state("crypto_scan")
        _write_scheduler_status("crypto_scan", "ok", detail)
    except Exception as e:
        _write_scheduler_status("crypto_scan", "error", str(e))
        logger.error(f"Crypto scan failed: {e}")
        send_telegram_message(f"*Agent ERROR (crypto)*\n`{e}`")


def run_morning_digest():
    """
    Pre-scan morning digest at 9:00 AM IST.
    Reads from OHLCV store (no API calls) — shows regime + top momentum candidates.
    """
    if _is_nse_holiday():
        return
    _write_scheduler_status("morning_digest", "running")
    logger.info(f"Morning digest at {datetime.now(IST).strftime('%H:%M IST')}")
    try:
        from analysis.market_regime import MarketRegimeFilter
        from analysis.momentum_filter import MomentumFilter
        from analysis.pattern_recognition import PatternRecogniser
        from analysis.support_resistance import SupportResistanceAnalyser
        from data.ohlcv_store import OHLCVStore
        from data.market_scanner import MarketScanner

        # 1. Market regime (quick — uses cached Nifty data)
        regime_result = MarketRegimeFilter().get_regime()
        regime = regime_result.regime.upper()
        nifty_trend = getattr(regime_result, "nifty_trend", "flat")
        regime_icon = {
            "BULL": "🐂", "RECOVERY": "🔄", "SIDEWAYS": "↔️", "BEAR": "🐻"
        }.get(regime, "📊")

        # 2. Load OHLCV from local store (fast — no yfinance calls at open)
        store       = OHLCVStore()
        all_symbols = MarketScanner().get_all_symbols()[:250]   # top 250 by rank

        market_data = {}
        for sym in all_symbols:
            df = store.get_symbol(sym, days=200)
            if not df.empty and len(df) >= 50:
                market_data[sym] = df

        if not market_data:
            logger.info("Morning digest: OHLCV store empty — digest skipped (store fills at 15:45)")
            _write_scheduler_status("morning_digest", "skipped", "OHLCV store empty")
            return

        # 3. Momentum filter — long candidates only
        momentum_results = MomentumFilter().filter_all(market_data, mode="buy")

        # 4. Pattern + S/R on top 60 momentum stocks
        top_syms = list(momentum_results.keys())[:60]
        top_data = {s: market_data[s] for s in top_syms if s in market_data}
        pattern_results = PatternRecogniser().analyse_all(top_data)
        sr_results      = SupportResistanceAnalyser().analyse_all(top_data)

        # 5. Score and rank candidates
        candidates = []
        for sym in top_syms:
            mom = momentum_results.get(sym)
            pat = pattern_results.get(sym)
            sr  = sr_results.get(sym)
            score = 5.0
            if pat: score += (pat.pattern_score - 5.0) * 0.3
            if sr:  score += (sr.sr_score       - 5.0) * 0.2
            candidates.append({
                "sym":      sym,
                "score":    score,
                "rsi":      mom.rsi if mom else 50,
                "buy_zone": sr.recommendation == "buy_zone" if sr else False,
                "pattern":  pat.primary_pattern if (pat and pat.bias == "bullish") else None,
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        top5 = candidates[:5]

        # 6. Build Telegram message
        date_str = datetime.now(IST).strftime("%d %b %Y")
        lines = [
            f"*🌅 Morning Digest — {date_str}*",
            f"{regime_icon} Regime: *{regime}* | Nifty trend: {nifty_trend.upper()}",
            "",
            f"*Top Candidates ({len(momentum_results)}/{len(market_data)} passed momentum filter):*",
        ]
        for c in top5:
            flags = []
            if c["buy_zone"]:             flags.append("📍 buy zone")
            if c["pattern"]:              flags.append(f"📐 {c['pattern']}")
            if not flags:                 flags.append("trending")
            lines.append(
                f"• *{c['sym']}*  RSI {c['rsi']:.0f}  |  {' | '.join(flags)}"
            )

        lines += [
            "",
            f"_Full scan starts at 09:15 IST  |  {len(market_data)} symbols loaded_",
        ]

        send_telegram_message("\n".join(lines))
        _write_scheduler_status(
            "morning_digest", "ok",
            f"Regime:{regime} | Passed:{len(momentum_results)} | Data:{len(market_data)}"
        )
        logger.info(
            f"Morning digest sent — regime={regime} "
            f"candidates={len(momentum_results)}/{len(market_data)}"
        )
    except Exception as e:
        _write_scheduler_status("morning_digest", "error", str(e))
        logger.error(f"Morning digest failed: {e}")


def run_ohlcv_update():
    """Store daily OHLCV candles for all watched symbols after market close."""
    _write_scheduler_status("ohlcv_update", "running")
    try:
        from data.ohlcv_store import OHLCVStore
        from data.market_scanner import MarketScanner
        store   = OHLCVStore()
        symbols = MarketScanner().get_all_symbols()
        results = store.update_all(symbols, lookback_days=400)
        ok = sum(1 for v in results.values() if v > 0)
        _write_scheduler_status("ohlcv_update", "ok", f"{ok}/{len(symbols)} symbols stored")
        logger.info(f"OHLCV update complete: {ok}/{len(symbols)} symbols")
    except Exception as e:
        _write_scheduler_status("ohlcv_update", "error", str(e))
        logger.error(f"OHLCV update failed: {e}")


def run_weekly_summary():
    """Send weekly performance report to Telegram."""
    _write_scheduler_status("weekly_summary", "running")
    logger.info(f"Weekly summary triggered at {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
    try:
        from automation.weekly_summary import send_weekly_summary
        send_weekly_summary()
        _write_scheduler_status("weekly_summary", "ok", "Weekly summary sent")
    except Exception as e:
        _write_scheduler_status("weekly_summary", "error", str(e))
        logger.error(f"Weekly summary failed: {e}")
        send_telegram_message(f"*Agent ERROR (weekly summary)*\n`{e}`")


def run_weekly_optimizer():
    """
    Run the walk-forward strategy optimizer on Sunday at 02:00 IST.
    Grid-searches RSI / momentum / confidence thresholds and saves
    best parameters to logs/optimiser_results.json.
    Dashboard picks up the results automatically on next load.
    """
    _write_scheduler_status("weekly_optimizer", "running")
    logger.info(
        f"Walk-forward optimizer triggered at "
        f"{datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}"
    )
    try:
        from backtest.optimiser import StrategyOptimiser
        optimiser = StrategyOptimiser()
        optimiser.run()

        # Surface best results in Telegram
        try:
            import json as _json
            _res_file = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "logs", "optimiser_results.json",
            )
            with open(_res_file, encoding="utf-8") as _f:
                _res = _json.load(_f)
            sharpe   = _res.get("best_sharpe",   0)
            ret      = _res.get("best_return",   0)
            win_rate = _res.get("best_win_rate", 0)
            bp       = _res.get("best_params",   {})
            param_str = " | ".join(f"{k}={v}" for k, v in list(bp.items())[:6])
            msg = (
                f"*Optimizer Complete (Sun 02:00)*\n"
                f"Sharpe: `{sharpe:.3f}` | Return: `{ret:.1f}%` | Win: `{win_rate:.1f}%`\n"
                f"Best params: `{param_str}`"
            )
            send_telegram_message(msg)
        except Exception as _te:
            logger.debug(f"Optimizer Telegram notify failed: {_te}")

        _write_scheduler_status("weekly_optimizer", "ok", "Optimizer complete")
    except Exception as e:
        _write_scheduler_status("weekly_optimizer", "error", str(e))
        logger.error(f"Walk-forward optimizer failed: {e}")
        send_telegram_message(f"*Agent ERROR (optimizer)*\n`{e}`")


# =============================================================================
# Alert deduplication — prevents the same (symbol, action) from firing
# multiple times in one trading day (e.g., intraday + daily scan overlap).
# =============================================================================

_DEDUP_DB: str = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "alert_dedup.db"
)

def _dedup_init():
    """Create dedup table if it doesn't exist. Enables WAL mode to prevent
    'database is locked' errors when concurrent scheduler jobs hit the same db."""
    try:
        os.makedirs(os.path.dirname(_DEDUP_DB), exist_ok=True)
        with __import__("sqlite3").connect(_DEDUP_DB) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sent_alerts (
                    key  TEXT NOT NULL,
                    date TEXT NOT NULL,
                    PRIMARY KEY (key, date)
                )
            """)
    except Exception as e:
        logger.debug(f"Dedup init failed: {e}")

def _dedup_check(symbol: str, action: str) -> bool:
    """
    Return True if this (symbol, action) alert was already sent today.
    Registers the alert as sent if it wasn't.
    """
    try:
        import sqlite3 as _sq
        today = datetime.now(IST).strftime("%Y-%m-%d")
        key   = f"{symbol}:{action}"
        with _sq.connect(_DEDUP_DB) as conn:
            existing = conn.execute(
                "SELECT 1 FROM sent_alerts WHERE key=? AND date=?", (key, today)
            ).fetchone()
            if existing:
                return True   # already sent today
            conn.execute("INSERT OR IGNORE INTO sent_alerts (key, date) VALUES (?,?)", (key, today))
        return False
    except Exception:
        return False   # on error, allow through (never silently drop)

_dedup_init()


# =============================================================================
# Telegram helper
# =============================================================================

def send_telegram_alert(signals: list, stats: dict):
    """Send today's scan results — delegates to utils.telegram for consistent formatting."""
    from config import MIN_CONFIDENCE
    actionable = [s for s in signals if getattr(s, "confidence", 0) >= MIN_CONFIDENCE]
    # Dedup: skip any (symbol, action) already alerted today
    deduped = []
    for sig in actionable:
        sym    = getattr(sig, "symbol", "")
        action = getattr(sig, "action", "")
        if sym and action and _dedup_check(sym, action):
            logger.debug(f"Dedup: {sym} {action} already sent today — skipping")
        else:
            deduped.append(sig)
    if len(actionable) != len(deduped):
        logger.info(f"Dedup: suppressed {len(actionable)-len(deduped)} duplicate alerts")
    from utils.telegram import send_signals
    send_signals(deduped, stats, mode=TRADING_MODE)


def send_telegram_message(text: str):
    """
    Send a plain text message to Telegram (chunked) and Discord.
    Central dispatcher used by all scheduler jobs.
    """
    from utils.alert_formatter import chunk_message
    chunks  = chunk_message(text, limit=4000)
    sent_any = False

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        for chunk in chunks:
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "Markdown"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    sent_any = True
                else:
                    logger.warning(f"Telegram HTTP {resp.status_code}: {resp.text[:300]}")
            except Exception as e:
                logger.warning(f"Telegram send failed: {e}")

    if send_discord_message_raw(text):
        sent_any = True

    if not sent_any:
        logger.info("No Telegram/Discord channel available for this message")


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    if not _acquire_pid_file():
        sys.exit(1)

    _write_scheduler_status("scheduler", "booting", "Scheduler process started")

    # Ollama availability check — sentiment quality depends on this
    try:
        import requests as _req
        from config import OLLAMA_BASE_URL, SENTIMENT_MODEL
        r = _req.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        if r.status_code == 200:
            tags = [m.get("name", "") for m in r.json().get("models", [])]
            if any(SENTIMENT_MODEL in t for t in tags):
                logger.info(f"Ollama OK — model '{SENTIMENT_MODEL}' available")
            else:
                logger.warning(
                    f"Ollama running but model '{SENTIMENT_MODEL}' not found. "
                    f"Available: {tags}. Sentiment will use keyword fallback."
                )
        else:
            logger.warning("Ollama not reachable — sentiment will use keyword fallback")
    except Exception as _e:
        logger.warning(f"Ollama not reachable ({_e}) — sentiment will use keyword fallback")

    run_housekeeping()

    from config import SCAN_TIME_1, SCAN_TIME_2

    def _parse_time(t: str):
        h, m = t.split(":")
        return int(h), int(m)

    h1, m1 = _parse_time(SCAN_TIME_1)
    h2, m2 = _parse_time(SCAN_TIME_2)

    # misfire_grace_time: if job fires late (e.g. system busy), allow up to
    # 120 s grace before treating it as a misfire and skipping it entirely.
    _GRACE = 120   # seconds

    scheduler = BlockingScheduler(timezone=IST)

    # --- Job 0: GIFT Nifty pre-market check at 8:30 AM ---
    scheduler.add_job(
        run_gift_nifty_check,
        CronTrigger(hour=8, minute=30, day_of_week="mon-fri", timezone=IST),
        id="gift_nifty",
        name="GIFT Nifty Pre-Market Check (08:30 IST)",
        misfire_grace_time=_GRACE,
    )

    # --- Job 0b: Morning digest at 9:00 AM (reads OHLCV store — no API calls) ---
    scheduler.add_job(
        run_morning_digest,
        CronTrigger(hour=9, minute=0, day_of_week="mon-fri", timezone=IST),
        id="morning_digest",
        name="Morning Digest (09:00 IST)",
        misfire_grace_time=_GRACE,
    )

    # --- Job 1: Morning scan ---
    scheduler.add_job(
        run_daily_scan,
        CronTrigger(hour=h1, minute=m1, day_of_week="mon-fri", timezone=IST),
        id="scan_1",
        name=f"Morning Scan ({SCAN_TIME_1} IST)",
        misfire_grace_time=_GRACE,
    )

    # --- Job 2: Afternoon scan ---
    scheduler.add_job(
        run_daily_scan,
        CronTrigger(hour=h2, minute=m2, day_of_week="mon-fri", timezone=IST),
        id="scan_2",
        name=f"Afternoon Scan ({SCAN_TIME_2} IST)",
        misfire_grace_time=_GRACE,
    )

    # --- Job 3: Price monitor every 15 min, 9:20 AM – 3:20 PM ---
    # Offset by 5 min from :15/:00 scan jobs to avoid portfolio lock contention.
    # hour="9-14" + minute 20 covers 15:20 via the 15:05/15:20 ticks from hour=15 below.
    # Explicitly stop at 15:20 (market closes 15:30) to avoid stale post-close fetches.
    scheduler.add_job(
        run_price_monitor,
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-14",
            minute="20,35,50,5",
            timezone=IST,
        ),
        id="price_monitor",
        name="Price Monitor (every 15 min, 09:05-14:50)",
        misfire_grace_time=_GRACE,
    )
    scheduler.add_job(
        run_price_monitor,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=5, timezone=IST),
        id="price_monitor_1505",
        name="Price Monitor (15:05 IST)",
        misfire_grace_time=_GRACE,
    )
    scheduler.add_job(
        run_price_monitor,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=20, timezone=IST),
        id="price_monitor_1520",
        name="Price Monitor (15:20 IST — last pre-close tick)",
        misfire_grace_time=_GRACE,
    )

    # --- Job 3b: F&O paper position monitor every 15 min ---
    scheduler.add_job(
        run_fno_monitor,
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-14",
            minute="20,35,50,5",
            timezone=IST,
        ),
        id="fno_monitor",
        name="F&O Paper Monitor (every 15 min, 09:05-14:50)",
        misfire_grace_time=_GRACE,
    )
    scheduler.add_job(
        run_fno_monitor,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=5, timezone=IST),
        id="fno_monitor_1505",
        name="F&O Monitor (15:05 IST)",
        misfire_grace_time=_GRACE,
    )
    scheduler.add_job(
        run_fno_monitor,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=20, timezone=IST),
        id="fno_monitor_1520",
        name="F&O Monitor (15:20 IST — last pre-close tick)",
        misfire_grace_time=_GRACE,
    )

    # --- Job 4: EOD close at 3:25 PM ---
    scheduler.add_job(
        run_eod_close,
        CronTrigger(hour=15, minute=25, day_of_week="mon-fri", timezone=IST),
        id="eod_close",
        name="EOD Close (15:25 IST)",
        misfire_grace_time=_GRACE,
    )

    # --- Job 5: Intraday scan every hour 9:30–14:30 ---
    scheduler.add_job(
        run_intraday_scan,
        CronTrigger(
            day_of_week="mon-fri",
            hour="9,10,11,12,13,14",
            minute=30,
            timezone=IST,
        ),
        id="intraday_scan",
        name="Intraday Scan (hourly 09:30-14:30 IST)",
        misfire_grace_time=_GRACE,
    )

    # --- Job 5b: Thesis re-evaluation at 1:00 PM ---
    scheduler.add_job(
        run_thesis_check,
        CronTrigger(hour=13, minute=0, day_of_week="mon-fri", timezone=IST),
        id="thesis_check",
        name="Thesis Re-evaluation (13:00 IST)",
        misfire_grace_time=_GRACE,
    )

    # --- Job 6: Outcome tracker at 3:30 PM (after market close) ---
    scheduler.add_job(
        run_outcome_tracker,
        CronTrigger(hour=15, minute=30, day_of_week="mon-fri", timezone=IST),
        id="outcome_tracker",
        name="Signal Outcome Tracker (15:30 IST)",
        misfire_grace_time=_GRACE,
    )

    # --- Job 6b: OHLCV store update at 3:45 PM (after NSE close) ---
    scheduler.add_job(
        run_ohlcv_update,
        CronTrigger(hour=15, minute=45, day_of_week="mon-fri", timezone=IST),
        id="ohlcv_update",
        name="OHLCV Store Update (15:45 IST)",
        misfire_grace_time=_GRACE,
    )

    # --- Job 7: US stocks scan at 7:00 PM IST (US market open) ---
    scheduler.add_job(
        run_us_scan,
        CronTrigger(hour=19, minute=0, day_of_week="mon-fri", timezone=IST),
        id="us_scan",
        name="US Stocks Scan (19:00 IST)",
        misfire_grace_time=_GRACE,
    )

    # --- Job 8: Crypto scan every 4 hours (24/7) ---
    scheduler.add_job(
        run_crypto_scan,
        CronTrigger(hour="0,4,8,12,16,20", minute=0, timezone=IST),
        id="crypto_scan",
        name="Crypto Scan (every 4h)",
        misfire_grace_time=_GRACE,
    )

    # --- Job 9: EOD daily digest at 6:00 PM (all markets) ---
    scheduler.add_job(
        run_eod_digest,
        CronTrigger(hour=18, minute=0, day_of_week="mon-fri", timezone=IST),
        id="eod_digest",
        name="EOD Digest (18:00 IST)",
        misfire_grace_time=_GRACE,
    )

    # --- Job 10: Weekly summary every Sunday 8 PM ---
    scheduler.add_job(
        run_weekly_summary,
        CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=IST),
        id="weekly_summary",
        name="Weekly Summary (Sun 20:00 IST)",
        misfire_grace_time=_GRACE,
    )

    # --- Job 11: Walk-forward optimizer every Sunday 2 AM ---
    scheduler.add_job(
        run_weekly_optimizer,
        CronTrigger(day_of_week="sun", hour=2, minute=0, timezone=IST),
        id="weekly_optimizer",
        name="Walk-Forward Optimizer (Sun 02:00 IST)",
        misfire_grace_time=_GRACE,
    )

    scheduler.add_job(
        run_housekeeping,
        CronTrigger(hour=6, minute=5, timezone=IST),
        id="housekeeping",
        name="Housekeeping (06:05 IST)",
        misfire_grace_time=_GRACE,
    )

    logger.info("=" * 60)
    logger.info("  QUANTEDGE SCHEDULER STARTED  -  17 JOBS")
    logger.info("  GIFT Nifty check   : 08:30 IST (Mon-Fri)")
    logger.info("  Morning digest     : 09:00 IST (Mon-Fri) — regime + top candidates")
    logger.info(f"  NSE morning scan   : {SCAN_TIME_1} IST (Mon-Fri)")
    logger.info(f"  NSE afternoon scan : {SCAN_TIME_2} IST (Mon-Fri)")
    logger.info("  Intraday scan      : hourly 09:30-14:30 (Mon-Fri)")
    logger.info("  Price monitor      : every 15 min 09:15-15:25 (Mon-Fri)")
    logger.info("  F&O paper monitor  : every 15 min 09:15-15:25 (Mon-Fri)")
    logger.info("  Thesis re-eval     : 13:00 IST (Mon-Fri)")
    logger.info("  EOD close          : 15:25 IST (Mon-Fri)")
    logger.info("  Outcome tracker    : 15:30 IST (Mon-Fri)")
    logger.info("  OHLCV store update : 15:45 IST (Mon-Fri)")
    logger.info("  EOD digest         : 18:00 IST (Mon-Fri, all markets)")
    logger.info("  US stocks scan     : 19:00 IST (Mon-Fri)")
    logger.info("  Crypto scan        : every 4h (24/7)")
    logger.info("  Weekly report      : Sunday 20:00 IST")
    logger.info("  Walk-fwd optimizer : Sunday 02:00 IST")
    logger.info("  Housekeeping       : 06:05 IST (daily)")
    logger.info("  Telegram bot       : always-on (command listener)")
    logger.info("=" * 60)

    # Start Telegram bot in background thread
    try:
        from telegram.bot import start_bot_thread as _tg_start
        _tg_start()
        logger.info("Telegram bot thread started")
    except Exception as e:
        logger.warning(f"Telegram bot failed to start: {e}")

    # Start Discord bot in background thread
    try:
        from discord_bot.bot import start_bot_thread as _dc_start
        _dc_start()
        logger.info("Discord bot thread started")
    except Exception as e:
        logger.warning(f"Discord bot failed to start: {e}")

    _sync_state("scheduler_startup")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped")
    finally:
        _release_pid_file()
