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

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TRADING_MODE,
    US_USD_PER_TRADE, CRYPTO_USDT_PER_TRADE,
)
from utils import get_logger
from utils.discord import send as send_discord_message_raw

logger = get_logger("Scheduler")
IST = pytz.timezone("Asia/Kolkata")
PID_FILE = "logs/scheduler.pid"


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
    logger.info(f"Scheduled scan triggered at {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
    if not _preflight_check():
        send_telegram_message("*Pre-flight FAILED* — skipping scan (network issue?)")
        return
    try:
        from main import run_agent
        from memory.portfolio_memory import PortfolioMemory

        # dry_run=False: paper mode simulates trades virtually (correct behavior)
        # dry_run=True is only for: python main.py --dry-run (manual override)
        signals = run_agent(dry_run=False)  # main.py saves signals internally

        memory  = PortfolioMemory()
        summary = memory.get_stats()
        send_telegram_alert(signals, summary)

        # Options signals — Nifty/BankNifty weekly CE/PE ideas
        _run_options_signals()
        # Futures signals — Nifty/BankNifty directional futures
        _run_futures_signals()
        # Options selling — straddle/strangle (Tue/Wed/Thu only)
        _run_selling_signals()

    except Exception as e:
        logger.error(f"Scheduled scan failed: {e}")
        send_telegram_message(f"*Agent ERROR (scan)*\n`{e}`")


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
            status = f"Paper #{trade_id}" if trade_id else "Could not fetch premium"
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
    except Exception as e:
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


def _run_futures_signals():
    """Generate Nifty/BankNifty futures signals and open paper positions."""
    try:
        from analysis.futures_signals import FuturesSignalGenerator
        from execution.brokers.fno_paper_broker import FNOPaperBroker
        signals = FuturesSignalGenerator().run()
        if not signals:
            return
        broker = FNOPaperBroker()
        lines  = ["*Nifty/BankNifty Futures — Paper Trade*", ""]
        for s in signals:
            tid = broker.open_futures(
                index=s.index, direction=s.direction,
                expiry=s.expiry, lots=1, reasoning=s.reasoning,
            )
            arrow = "▲ LONG" if s.direction == "LONG" else "▼ SHORT"
            lines += [
                f"*{s.index} FUT {arrow}* | Expiry {s.expiry}",
                f"Entry {s.entry_price:,.0f} | SL {s.sl_price:,.0f} | Target {s.target_price:,.0f}",
                f"Conf {s.confidence:.0%} | {'Paper #'+str(tid) if tid else 'Skip'}",
                "",
            ]
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
            lines = ["*EOD Intraday Close — 15:25 IST*", ""]
            for r in results:
                emoji = "✅" if r.pnl > 0 else "❌"
                lines.append(f"{emoji} {r.symbol} | EOD | Rs.{r.pnl:+,.0f}")
            lines.append(f"\nIntraday P&L today: Rs.{total_pnl:+,.0f}")
            send_telegram_message("\n".join(lines))
        else:
            logger.info("EOD: no intraday positions to close")
    except Exception as e:
        logger.error(f"EOD close failed: {e}")


# =============================================================================
# JOB — Daily EOD digest at 6:00 PM (all markets)
# =============================================================================

def run_eod_digest():
    """Send 6 PM all-market daily summary to Telegram."""
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
                    except Exception:
                        pass

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
    except Exception as e:
        logger.error(f"EOD digest failed: {e}")


# =============================================================================
# JOB 5 — Intraday scan every hour 9:30–14:30 (Mon-Fri)
# =============================================================================

def run_intraday_scan():
    """15-min EMA/VWAP intraday signals on top swing candidates."""
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
    except Exception as e:
        logger.error(f"Intraday scan failed: {e}")


# =============================================================================
# JOB 6 — Signal outcome tracker at 3:30 PM (after market close, Mon-Fri)
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

def run_us_scan():
    """US stocks scan — runs at 7:00 PM IST (US market open), Mon-Fri."""
    logger.info(f"US scan at {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
    try:
        from data.us_scanner import USScanner
        from analysis.technical_agent import TechnicalAgent
        from execution.brokers.us_paper_broker import USPaperBroker
        from config import MIN_CONFIDENCE

        scanner     = USScanner()
        market_data = scanner.run(max_workers=15)
        if not market_data:
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
        logger.info(f"US scan: {len(new_signals)} new | open={stats['open_positions']}")
    except Exception as e:
        logger.error(f"US scan failed: {e}")


def run_crypto_scan():
    """Crypto market scan — runs every 4 hours, 24/7."""
    logger.info(f"Crypto scan at {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}")
    try:
        from data.crypto_scanner import CryptoScanner
        from analysis.technical_agent import TechnicalAgent
        from execution.brokers.crypto_paper_broker import CryptoPaperBroker

        scanner     = CryptoScanner()
        market_data = scanner.run(max_workers=10)
        if not market_data:
            logger.warning("Crypto scan: no data returned")
            return

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
        logger.info(f"Crypto scan done: {len(new_signals)} new | "
                    f"open={stats['open_positions']} | "
                    f"total P&L={stats['total_pnl_usdt']:+.2f} USDT")
    except Exception as e:
        logger.error(f"Crypto scan failed: {e}")
        send_telegram_message(f"*Agent ERROR (crypto)*\n`{e}`")


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

    from config import MIN_CONFIDENCE
    # Only show signals that meet the confidence threshold
    actionable = [s for s in signals
                  if getattr(s, "confidence", 0) >= MIN_CONFIDENCE]

    date_str = datetime.now(IST).strftime("%d %b %Y")
    lines = [f"*NSE Agent — {date_str}*", f"Mode: {TRADING_MODE.upper()}", ""]

    if not actionable:
        lines.append("No actionable signals today (all below confidence threshold).")
    else:
        for i, s in enumerate(actionable[:5], 1):
            lines += [
                f"*#{i} {s.symbol}* — {s.action} ({s.confidence:.0%})",
                f"Entry: Rs.{s.entry_price:,.0f} | "
                f"SL: Rs.{s.stop_loss:,.0f} | "
                f"TP: Rs.{s.take_profit:,.0f}",
                f"_{s.reasoning[:80]}_",
                "",
            ]

    lines.append(
        f"Win Rate: {stats.get('win_rate_pct', 0):.0f}% | "
        f"Trades: {stats.get('total_trades', 0)}"
    )
    send_telegram_message("\n".join(lines))


def send_telegram_message(text: str):
    sent_any = False
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
            logger.info("Telegram message sent")
            sent_any = True
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")

    if send_discord_message_raw(text):
        sent_any = True

    if not sent_any:
        logger.info("No Telegram/Discord alert channel available for this message")


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

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

    # --- Job 3b: F&O paper position monitor every 15 min ---
    scheduler.add_job(
        run_fno_monitor,
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-15",
            minute="15,30,45,0",
            timezone=IST,
        ),
        id="fno_monitor",
        name="F&O Paper Monitor (every 15 min)",
    )

    # --- Job 4: EOD close at 3:25 PM ---
    scheduler.add_job(
        run_eod_close,
        CronTrigger(hour=15, minute=25, day_of_week="mon-fri", timezone=IST),
        id="eod_close",
        name="EOD Close (15:25 IST)",
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
    )

    # --- Job 6: Outcome tracker at 3:30 PM (after market close) ---
    scheduler.add_job(
        run_outcome_tracker,
        CronTrigger(hour=15, minute=30, day_of_week="mon-fri", timezone=IST),
        id="outcome_tracker",
        name="Signal Outcome Tracker (15:30 IST)",
    )

    # --- Job 7: US stocks scan at 7:00 PM IST (US market open) ---
    scheduler.add_job(
        run_us_scan,
        CronTrigger(hour=19, minute=0, day_of_week="mon-fri", timezone=IST),
        id="us_scan",
        name="US Stocks Scan (19:00 IST)",
    )

    # --- Job 8: Crypto scan every 4 hours (24/7) ---
    scheduler.add_job(
        run_crypto_scan,
        CronTrigger(hour="0,4,8,12,16,20", minute=0, timezone=IST),
        id="crypto_scan",
        name="Crypto Scan (every 4h)",
    )

    # --- Job 9: EOD daily digest at 6:00 PM (all markets) ---
    scheduler.add_job(
        run_eod_digest,
        CronTrigger(hour=18, minute=0, day_of_week="mon-fri", timezone=IST),
        id="eod_digest",
        name="EOD Digest (18:00 IST)",
    )

    # --- Job 10: Weekly summary every Sunday 8 PM ---
    scheduler.add_job(
        run_weekly_summary,
        CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=IST),
        id="weekly_summary",
        name="Weekly Summary (Sun 20:00 IST)",
    )

    logger.info("=" * 60)
    logger.info("  QUANTEDGE SCHEDULER STARTED  —  10 JOBS")
    logger.info(f"  NSE morning scan   : {SCAN_TIME_1} IST (Mon-Fri)")
    logger.info(f"  NSE afternoon scan : {SCAN_TIME_2} IST (Mon-Fri)")
    logger.info("  Intraday scan      : hourly 09:30-14:30 (Mon-Fri)")
    logger.info("  Price monitor      : every 15 min 09:15-15:25 (Mon-Fri)")
    logger.info("  F&O paper monitor  : every 15 min 09:15-15:25 (Mon-Fri)")
    logger.info("  EOD close          : 15:25 IST (Mon-Fri)")
    logger.info("  Outcome tracker    : 15:30 IST (Mon-Fri)")
    logger.info("  US stocks scan     : 19:00 IST (Mon-Fri)")
    logger.info("  Crypto scan        : every 4h (24/7)")
    logger.info("  EOD digest         : 18:00 IST (Mon-Fri, all markets)")
    logger.info("  Weekly report      : Sunday 20:00 IST")
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

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped")
