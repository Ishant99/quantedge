# utils/telegram.py — Send alerts to Telegram and Discord
#
# Every alert goes to BOTH channels automatically.
# Messages are written in plain English — no jargon.

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from utils import get_logger
from utils.alert_formatter import chunk_message
import utils.discord as discord_utils

logger = get_logger("Telegram")


def send(text: str) -> bool:
    """
    Send a message to Telegram and Discord.
    Long messages are automatically split into chunks.
    Returns True if at least one channel received it.
    """
    sent_any = False
    chunks   = chunk_message(text, limit=4000)

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        for chunk in chunks:
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id":    TELEGRAM_CHAT_ID,
                        "text":       chunk,
                        "parse_mode": "Markdown",
                    },
                    timeout=10,
                )
                if r.status_code == 200:
                    sent_any = True
                else:
                    logger.warning(f"Telegram failed: {r.status_code} — {r.text[:200]}")
            except Exception as e:
                logger.warning(f"Telegram error: {e}")
    else:
        logger.warning("Telegram not configured — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")

    if discord_utils.send(text):
        sent_any = True

    if not sent_any:
        logger.error("Alert dropped — both Telegram and Discord unavailable")

    return sent_any


def send_signals(signals: list, stats: dict, mode: str = "paper", dry_run: bool = False):
    """
    Send today's top BUY signals.
    Plain English — shows exactly what to watch and why.
    Also sends a rich embed to Discord for each signal.
    """
    from datetime import datetime
    import pytz
    IST      = pytz.timezone("Asia/Kolkata")
    date_str = datetime.now(IST).strftime("%d %b %Y, %I:%M %p")

    mode_tag = "PAPER TRADING" if mode == "paper" else "⚡ LIVE TRADING"
    dry_tag  = " — DRY RUN (no orders placed)" if dry_run else ""

    # ── Telegram message ──────────────────────────────────────────────────
    lines = [
        f"*📊 QuantEdge — Daily Scan*",
        f"_{date_str} IST | {mode_tag}{dry_tag}_",
        "",
    ]

    def _metrics(s):
        sl_risk   = round(s.entry_price - s.stop_loss, 2)
        tp_reward = round(s.take_profit - s.entry_price, 2)
        rr        = round(tp_reward / sl_risk, 1) if sl_risk > 0 else 0
        return sl_risk, tp_reward, rr

    if not signals:
        lines += [
            "No buy signals today.",
            "_Market conditions didn't meet our criteria — staying in cash is the right call._",
        ]
    else:
        lines.append(f"*🟢 {len(signals)} Signal{'s' if len(signals) > 1 else ''} Found:*")
        lines.append("")
        for i, s in enumerate(signals, 1):
            sl_risk, tp_reward, rr = _metrics(s)
            lines += [
                f"*#{i} {s.symbol}* — Confidence {s.confidence:.0%}",
                f"📥 Buy at `₹{s.entry_price:,.0f}`",
                f"🛑 Stop loss: `₹{s.stop_loss:,.0f}` (risk ₹{sl_risk:,.0f}/share)",
                f"🎯 Target: `₹{s.take_profit:,.0f}` (profit ₹{tp_reward:,.0f}/share) — {rr}x reward",
                f"_{s.reasoning[:120]}_",
                "",
            ]

    win_rate   = stats.get("win_rate_pct", 0)
    total      = stats.get("total_trades", 0)
    total_pnl  = stats.get("total_pnl", 0)
    expectancy = stats.get("expectancy", 0)
    lines += [
        "─────────────────",
        f"*All-time:* {total} trades | Win rate {win_rate:.0f}% | "
        f"P&L ₹{total_pnl:+,.0f} | Avg ₹{expectancy:+,.0f}/trade",
    ]

    send("\n".join(lines))

    # ── Discord embed per signal ──────────────────────────────────────────
    if signals:
        for s in signals[:5]:
            sl_risk, tp_reward, rr = _metrics(s)
            discord_utils.send_embed(
                title       = f"🟢 BUY Signal — {s.symbol}",
                description = s.reasoning[:300],
                color       = discord_utils.COLOR_GREEN,
                fields      = [
                    {"name": "Confidence",  "value": f"{s.confidence:.0%}",            "inline": True},
                    {"name": "Entry",       "value": f"₹{s.entry_price:,.0f}",         "inline": True},
                    {"name": "Stop Loss",   "value": f"₹{s.stop_loss:,.0f} (-₹{sl_risk:,.0f})", "inline": True},
                    {"name": "Target",      "value": f"₹{s.take_profit:,.0f} (+₹{tp_reward:,.0f})", "inline": True},
                    {"name": "Risk:Reward", "value": f"{rr}x",                         "inline": True},
                    {"name": "Mode",        "value": mode_tag,                          "inline": True},
                ],
                footer = date_str,
            )


def test_connection() -> bool:
    """Quick test — sends a test message to verify setup."""
    return send(
        "*✅ QuantEdge Alert Test*\n"
        "_Telegram and Discord are connected and working!_"
    )
