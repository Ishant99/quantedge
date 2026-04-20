# utils/telegram.py — Send Telegram alerts from anywhere in the agent

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from utils import get_logger
from utils.discord import send as send_discord

logger = get_logger("Telegram")


def send(text: str) -> bool:
    """Send a plain text message to Telegram and Discord. Returns True if any send succeeds."""
    sent_any = False

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id":    TELEGRAM_CHAT_ID,
                    "text":       text,
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
            if r.status_code == 200:
                logger.info("Telegram alert sent")
                sent_any = True
            else:
                logger.warning(f"Telegram failed: {r.status_code} — {r.text}")
        except Exception as e:
            logger.warning(f"Telegram error: {e}")
    else:
        logger.warning("Telegram not configured — check .env for TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")

    if send_discord(text):
        sent_any = True

    if not sent_any:
        logger.error("Alert dropped — both Telegram and Discord unavailable")

    return sent_any


def send_signals(signals: list, stats: dict, mode: str = "paper", dry_run: bool = False):
    """Format and send today's top signals."""
    from datetime import datetime
    import pytz
    IST      = pytz.timezone("Asia/Kolkata")
    date_str = datetime.now(IST).strftime("%d %b %Y %I:%M %p")

    lines = [
        f"*NSE Trading Agent*",
        f"_{date_str} IST_",
        f"Mode: `{mode.upper()}` {'(DRY RUN)' if dry_run else ''}",
        "",
    ]

    if not signals:
        lines.append("No BUY signals today.")
    else:
        lines.append(f"*Top {len(signals)} Signal{'s' if len(signals)>1 else ''}:*")
        lines.append("")
        for i, s in enumerate(signals, 1):
            lines += [
                f"*#{i} {s.symbol}* — {s.action}",
                f"Confidence: `{s.confidence:.0%}` | TA Score: `{s.ta_score}/10`",
                f"Entry: `Rs.{s.entry_price:,.0f}` | SL: `Rs.{s.stop_loss:,.0f}` | TP: `Rs.{s.take_profit:,.0f}`",
                f"_{s.reasoning[:100]}_",
                "",
            ]

    lines += [
        "---",
        f"Trades: {stats.get('total_trades',0)} | "
        f"Win Rate: {stats.get('win_rate_pct',0):.0f}% | "
        f"P&L: Rs.{stats.get('total_pnl',0):+,.0f}",
    ]

    send("\n".join(lines))


def test_connection() -> bool:
    """Quick test — sends a test message to verify setup."""
    return send("*Trading Agent* — Telegram connected successfully!")
