# =============================================================================
# discord_bot/bot.py — QuantEdge Discord Bot
#
# Listens for !commands in a Discord channel and replies instantly.
# Runs as a background thread inside scheduler.py.
# Uses discord.py library (pip install discord.py).
#
# Commands (prefix !):
#   !start / !help  — welcome + command list
#   !status         — portfolio value + combined P&L all markets
#   !pnl            — today's P&L breakdown by market
#   !signals        — latest buy signals
#   !positions      — open NSE positions
#   !crypto         — open crypto positions
#   !us             — open US stock positions
#   !fno            — open F&O positions
#   !regime         — market regime + RSI + PCR + FII
#   !run            — trigger a manual NSE scan now
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import sqlite3
import json
import threading
from datetime import datetime
from utils import get_logger

logger = get_logger("DiscordBot")


def _cfg(key, default=None):
    import settings.manager as S
    return S.get(key, default)

def _load_json(path, default=None):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default or {}


# ── Response builders (shared logic with Telegram bot) ───────────────────────

def _build_status() -> str:
    pf   = _load_json("logs/virtual_portfolio.json")
    cash = pf.get("cash", _cfg("VIRTUAL_CAPITAL", 1_000_000))
    vc   = _cfg("VIRTUAL_CAPITAL", 1_000_000)
    nse_pnl = cash - vc
    pos  = pf.get("positions", {})
    inr  = _cfg("INR_PER_USD", 83.0)

    fno_pnl = cry_pnl = us_pnl = 0.0
    try:
        from execution.brokers.fno_paper_broker import FNOPaperBroker
        fno_pnl = FNOPaperBroker().get_stats().get("total_pnl", 0) or 0
    except Exception: pass
    try:
        from execution.brokers.crypto_paper_broker import CryptoPaperBroker
        cry_pnl = (CryptoPaperBroker().get_stats().get("total_pnl_usdt", 0) or 0) * inr
    except Exception: pass
    try:
        from execution.brokers.us_paper_broker import USPaperBroker
        us_pnl = (USPaperBroker().get_stats().get("total_pnl_usd", 0) or 0) * inr
    except Exception: pass

    combined = nse_pnl + fno_pnl + cry_pnl + us_pnl
    s = lambda v: "+" if v >= 0 else ""
    return (
        f"**Portfolio Status** — {datetime.now().strftime('%d %b %Y %H:%M IST')}\n"
        f"```\n"
        f"NSE Equity : Rs.{cash:,.0f}\n"
        f"NSE P&L    : Rs.{s(nse_pnl)}{nse_pnl:,.0f}\n"
        f"F&O P&L    : Rs.{s(fno_pnl)}{fno_pnl:,.0f}\n"
        f"Crypto P&L : Rs.{s(cry_pnl)}{cry_pnl:,.0f}\n"
        f"US P&L     : Rs.{s(us_pnl)}{us_pnl:,.0f}\n"
        f"──────────────────────────\n"
        f"Combined   : Rs.{s(combined)}{combined:,.0f}\n"
        f"Open Pos   : {len(pos)} NSE\n"
        f"```"
    )


def _build_pnl() -> str:
    from config import SQLITE_DB_FILE, INR_PER_USD, INR_PER_USDT
    today = datetime.now().strftime("%Y-%m-%d")
    counts = {"nse": 0, "fno": 0, "crypto": 0, "us": 0}
    pnls   = {"nse": 0.0, "fno": 0.0, "crypto": 0.0, "us": 0.0}

    if os.path.exists(SQLITE_DB_FILE):
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            for key, table, col in [
                ("nse",    "trades",        "pnl"),
                ("fno",    "fno_trades",    "pnl"),
                ("crypto", "crypto_trades", "pnl_usdt"),
                ("us",     "us_trades",     "pnl_usd"),
            ]:
                try:
                    row = conn.execute(
                        f"SELECT COUNT(*), COALESCE(SUM({col}),0) FROM {table} "
                        f"WHERE status='closed' AND exit_time LIKE ?",
                        (f"{today}%",)
                    ).fetchone()
                    counts[key], pnls[key] = row[0], row[1]
                except Exception:
                    pass

    combined = (pnls["nse"] + pnls["fno"] +
                pnls["crypto"] * INR_PER_USDT + pnls["us"] * INR_PER_USD)
    s = lambda v: "+" if v >= 0 else ""
    date_label = datetime.now().strftime("%d %b %Y")
    return (
        f"**Daily P&L — {date_label}**\n"
        f"```\n"
        f"NSE Equity : {counts['nse']} trades | Rs.{s(pnls['nse'])}{pnls['nse']:,.0f}\n"
        f"F&O Paper  : {counts['fno']} trades | Rs.{s(pnls['fno'])}{pnls['fno']:,.0f}\n"
        f"Crypto     : {counts['crypto']} trades | {s(pnls['crypto'])}{pnls['crypto']:.2f} USDT\n"
        f"US Stocks  : {counts['us']} trades | ${s(pnls['us'])}{pnls['us']:.2f}\n"
        f"──────────────────────────\n"
        f"Combined   : Rs.{s(combined)}{combined:,.0f}\n"
        f"```"
    )


def _build_signals() -> str:
    from memory.portfolio_memory import PortfolioMemory
    from config import MIN_CONFIDENCE
    sigs  = PortfolioMemory().get_recent_signals(limit=20)
    today = datetime.now().strftime("%Y-%m-%d")
    today_sigs = [s for s in sigs
                  if s["timestamp"].startswith(today)
                  and s["action"] == "BUY"
                  and s.get("confidence", 0) >= MIN_CONFIDENCE]
    if not today_sigs:
        today_sigs = [s for s in sigs if s["action"] == "BUY"][:5]
    if not today_sigs:
        return "No buy signals today. Use `!run` to trigger a scan."

    lines = [f"**Buy Signals — {len(today_sigs)} found**"]
    for s in today_sigs[:5]:
        ep = s.get("entry_price", 0) or 0
        sl = s.get("stop_loss", 0) or 0
        tp = s.get("take_profit", 0) or 0
        lines.append(
            f"```{s['symbol']}  Conf {s['confidence']:.0%}  TA {s['ta_score']}/10\n"
            f"Entry Rs.{ep:,.0f}  SL Rs.{sl:,.0f}  TP Rs.{tp:,.0f}```"
        )
    return "\n".join(lines)


def _build_positions() -> str:
    pf  = _load_json("logs/virtual_portfolio.json")
    pos = pf.get("positions", {})
    if not pos:
        return "No open NSE positions. Agent is fully in cash."
    lines = [f"**Open NSE Positions ({len(pos)})**"]
    for sym, p in pos.items():
        lines.append(
            f"```{sym}  {p.get('qty',0)} shares\n"
            f"Entry Rs.{p.get('entry',0):,.2f}  "
            f"SL Rs.{p.get('stop_loss',0):,.0f}  "
            f"TP Rs.{p.get('take_profit',0):,.0f}```"
        )
    return "\n".join(lines)


def _build_crypto() -> str:
    from execution.brokers.crypto_paper_broker import CryptoPaperBroker
    broker = CryptoPaperBroker()
    stats  = broker.get_stats()
    open_p = broker.get_open_positions()
    lines  = [
        f"**Crypto Paper — {stats.get('open_positions',0)} open**",
        f"Win Rate: {stats.get('win_rate',0):.0f}%  |  "
        f"Total P&L: {stats.get('total_pnl_usdt',0):+.2f} USDT",
    ]
    if open_p:
        for p in open_p[:5]:
            entry = p["entry_price"]
            curr  = p["current_price"] or entry
            pnl   = p["pnl_usdt"] or 0
            chg   = (curr - entry) / entry * 100 if entry else 0
            lines.append(
                f"```{p['symbol']}  {p['direction']}\n"
                f"Entry {entry:.4f} → Now {curr:.4f}  "
                f"P&L {pnl:+.2f} USDT ({chg:+.1f}%)```"
            )
    else:
        lines.append("No open crypto positions.")
    return "\n".join(lines)


def _build_us() -> str:
    from execution.brokers.us_paper_broker import USPaperBroker
    broker = USPaperBroker()
    stats  = broker.get_stats()
    open_p = broker.get_open_positions()
    lines  = [
        f"**US Stocks Paper — {stats.get('open_positions',0)} open**",
        f"Win Rate: {stats.get('win_rate',0):.0f}%  |  "
        f"Total P&L: ${stats.get('total_pnl_usd',0):+.2f}",
    ]
    if open_p:
        for p in open_p[:5]:
            entry = p["entry_price"]
            curr  = p["current_price"] or entry
            pnl   = p["pnl_usd"] or 0
            chg   = (curr - entry) / entry * 100 if entry else 0
            lines.append(
                f"```{p['symbol']}  {p['direction']}\n"
                f"Entry ${entry:.2f} → Now ${curr:.2f}  "
                f"P&L ${pnl:+.2f} ({chg:+.1f}%)```"
            )
    else:
        lines.append("No open US positions.")
    return "\n".join(lines)


def _build_fno() -> str:
    from execution.brokers.fno_paper_broker import FNOPaperBroker
    broker = FNOPaperBroker()
    stats  = broker.get_stats()
    open_p = broker.get_open_positions()
    lines  = [
        f"**F&O Paper — {stats.get('open_positions',0)} open**",
        f"Win Rate: {stats.get('win_rate',0):.0f}%  |  "
        f"Total P&L: Rs.{stats.get('total_pnl',0):+,.0f}",
    ]
    if open_p:
        for p in open_p[:5]:
            entry = p["entry_premium"]
            curr  = p["current_premium"] or entry
            pnl   = p["pnl"] or 0
            lines.append(
                f"```{p['instrument']}  {p['option_type']}  Strike {p['strike']}\n"
                f"Entry Rs.{entry:.1f} → Now Rs.{curr:.1f}  P&L Rs.{pnl:+,.0f}```"
            )
    else:
        lines.append("No open F&O positions.")
    return "\n".join(lines)


def _build_regime() -> str:
    reg = _load_json("logs/market_regime.json")
    pcr = _load_json("logs/pcr_signal.json")
    fii = _load_json("logs/fii_signal.json")
    rg      = reg.get("regime", "unknown").upper() if reg else "---"
    rsi     = reg.get("rsi", 0) if reg else 0
    ret_1m  = reg.get("ret_1m", 0) if reg else 0
    allow   = reg.get("allow_buys", True) if reg else True
    pcr_val = pcr.get("pcr", 0) if pcr else 0
    pcr_sig = pcr.get("signal", "neutral").upper() if pcr else "---"
    fii_net = fii.get("fii_net", 0) if fii else 0
    fii_sig = fii.get("signal", "neutral").upper() if fii else "---"
    icon    = "🟢" if rg == "BULL" else "🔴" if rg == "BEAR" else "🟡"
    status  = "✅ TRADING ACTIVE" if allow else "🚫 TRADING BLOCKED"
    return (
        f"**Market Regime** {icon}\n"
        f"```\n"
        f"Regime   : {rg}  —  {status}\n"
        f"Nifty RSI: {rsi:.1f}\n"
        f"Nifty 1M : {ret_1m:+.2f}%\n"
        f"PCR      : {pcr_val:.2f}  ({pcr_sig})\n"
        f"FII Net  : Rs.{fii_net:+,.0f}Cr  ({fii_sig})\n"
        f"```"
    )


HELP_TEXT = (
    "**QuantEdge Pro — Commands**\n"
    "```\n"
    "!status    — Portfolio + combined P&L\n"
    "!pnl       — Today's P&L by market\n"
    "!signals   — Latest buy signals\n"
    "!positions — Open NSE positions\n"
    "!crypto    — Open crypto positions\n"
    "!us        — Open US stock positions\n"
    "!fno       — Open F&O positions\n"
    "!regime    — Market regime + RSI + PCR + FII\n"
    "!run       — Run NSE scan now\n"
    "!help      — Show this list\n"
    "```"
)


# ── Discord bot setup ─────────────────────────────────────────────────────────

def run_bot():
    """Start the Discord bot. Runs forever in its own event loop thread."""
    try:
        import discord
    except ImportError:
        logger.error("discord.py not installed — run: pip install discord.py")
        return

    token      = _cfg("DISCORD_BOT_TOKEN", "")
    channel_id = _cfg("DISCORD_CHANNEL_ID", "")

    if not token or not channel_id:
        logger.warning("Discord bot: DISCORD_BOT_TOKEN or DISCORD_CHANNEL_ID not set — bot disabled")
        return

    try:
        channel_id = int(channel_id)
    except (ValueError, TypeError):
        logger.error(f"DISCORD_CHANNEL_ID must be a number, got: {channel_id}")
        return

    intents          = discord.Intents.default()
    intents.message_content = True
    client           = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        logger.info(f"Discord bot logged in as {client.user}")
        ch = client.get_channel(channel_id)
        if ch:
            await ch.send("**QuantEdge Pro** online ✅  Type `!help` for commands.")

    @client.event
    async def on_message(message):
        # Ignore own messages and wrong channels
        if message.author == client.user:
            return
        if message.channel.id != channel_id:
            return

        cmd = message.content.strip().split()[0].lower() if message.content.strip() else ""

        async def reply(text: str):
            # Discord has 2000 char limit per message — split if needed
            for chunk in [text[i:i+1900] for i in range(0, len(text), 1900)]:
                await message.channel.send(chunk)

        try:
            if cmd in ("!help", "!start"):
                await reply(HELP_TEXT)
            elif cmd == "!status":
                await reply(_build_status())
            elif cmd == "!pnl":
                await reply(_build_pnl())
            elif cmd == "!signals":
                await reply(_build_signals())
            elif cmd == "!positions":
                await reply(_build_positions())
            elif cmd == "!crypto":
                await reply(_build_crypto())
            elif cmd == "!us":
                await reply(_build_us())
            elif cmd == "!fno":
                await reply(_build_fno())
            elif cmd == "!regime":
                await reply(_build_regime())
            elif cmd == "!run":
                await reply("⚙️ Running NSE scan... ~30 seconds.")
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, _do_run)
                await reply(result)
            elif cmd.startswith("!"):
                await reply(f"Unknown command: `{cmd}`\nType `!help` for the full list.")
        except Exception as e:
            logger.error(f"Discord command {cmd} failed: {e}")
            await reply(f"❌ Error: `{e}`")

    def _do_run() -> str:
        try:
            from main import run_agent
            sigs = run_agent(dry_run=False)
            buys = [s for s in sigs if getattr(s, "action", "") == "BUY"]
            return f"✅ Scan complete — {len(buys)} buy signal(s) found.\nUse `!signals` to see them."
        except Exception as e:
            return f"❌ Scan failed: `{e}`"

    logger.info("Discord bot starting...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(client.start(token))
    except Exception as e:
        logger.error(f"Discord bot error: {e}")
    finally:
        loop.close()


def start_bot_thread() -> threading.Thread:
    """Start Discord bot in a daemon thread. Returns the thread."""
    t = threading.Thread(target=run_bot, name="DiscordBot", daemon=True)
    t.start()
    return t
