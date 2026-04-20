# =============================================================================
# telegram/bot.py — QuantEdge Telegram Bot
#
# Listens for commands from the user and replies instantly.
# Runs as a background thread inside scheduler.py (long-polling).
#
# Commands:
#   /start    — welcome + command list
#   /status   — portfolio value + combined P&L all markets
#   /pnl      — today's P&L breakdown by market
#   /signals  — latest buy signals
#   /positions — open NSE positions
#   /crypto   — open crypto positions
#   /us       — open US stock positions
#   /fno      — open F&O positions
#   /regime   — market regime + RSI + PCR + FII
#   /run      — trigger a manual NSE scan now
#   /help     — command list
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import json
import sqlite3
import time
import threading
from datetime import datetime
from utils import get_logger

logger = get_logger("TelegramBot")

# ── Helpers ───────────────────────────────────────────────────────────────────

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

def _send(token: str, chat_id: str, text: str):
    """Send a reply message."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Bot send error: {e}")


# ── Command handlers ──────────────────────────────────────────────────────────

def _cmd_start(token, chat_id):
    _send(token, chat_id, """*QuantEdge Pro — Bot Commands*

/status    — Portfolio + combined P&L
/pnl       — Today's P&L by market
/signals   — Latest buy signals
/positions — Open NSE positions
/crypto    — Open crypto positions
/us        — Open US stock positions
/fno       — Open F&O positions
/regime    — Market regime + RSI + PCR + FII
/run       — Run NSE scan now
/help      — Show this list
""")


def _cmd_status(token, chat_id):
    try:
        from execution.executor import get_executor

        exec_ = get_executor()
        vc = _cfg("VIRTUAL_CAPITAL", 1_000_000)
        portfolio_value = exec_.get_portfolio_value()
        open_positions = exec_.get_open_positions_count()
        pf = _load_json("logs/virtual_portfolio.json")
        cash = pf.get("cash", portfolio_value)
        deployed = max(0, portfolio_value - cash)
        nse_pnl = portfolio_value - vc

        inr = _cfg("INR_PER_USD", 83.0)
        fno_pnl = cry_pnl = us_pnl = 0.0
        try:
            from execution.brokers.fno_paper_broker import FNOPaperBroker
            fno_pnl = FNOPaperBroker().get_stats().get("total_pnl", 0) or 0
        except Exception as e:
            logger.warning(f"F&O broker stats unavailable: {e}")
        try:
            from execution.brokers.crypto_paper_broker import CryptoPaperBroker
            cry_pnl = (CryptoPaperBroker().get_stats().get("total_pnl_usdt", 0) or 0) * inr
        except Exception as e:
            logger.warning(f"Crypto broker stats unavailable: {e}")
        try:
            from execution.brokers.us_paper_broker import USPaperBroker
            us_pnl = (USPaperBroker().get_stats().get("total_pnl_usd", 0) or 0) * inr
        except Exception as e:
            logger.warning(f"US broker stats unavailable: {e}")

        combined = nse_pnl + fno_pnl + cry_pnl + us_pnl
        sign = lambda v: "+" if v >= 0 else ""

        _send(token, chat_id, f"""*Portfolio Status*
_Updated: {datetime.now().strftime('%d %b %Y %H:%M IST')}_

💼 *NSE Equity:* Rs.{portfolio_value:,.0f}
Cash: Rs.{cash:,.0f} | Deployed: Rs.{deployed:,.0f}
📈 NSE P&L:    Rs.{sign(nse_pnl)}{nse_pnl:,.0f}
📊 F&O P&L:    Rs.{sign(fno_pnl)}{fno_pnl:,.0f}
₿  Crypto P&L: Rs.{sign(cry_pnl)}{cry_pnl:,.0f}
🇺🇸 US P&L:    Rs.{sign(us_pnl)}{us_pnl:,.0f}

*Combined P&L: Rs.{sign(combined)}{combined:,.0f}*
Open positions: {open_positions} NSE""")
    except Exception as e:
        _send(token, chat_id, f"Error fetching status: `{e}`")


def _cmd_pnl(token, chat_id):
    try:
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
        sign = lambda v: "+" if v >= 0 else ""
        date_label = datetime.now().strftime("%d %b %Y")

        _send(token, chat_id, f"""*Daily P&L — {date_label}*

NSE Equity : {counts['nse']} trades | Rs.{sign(pnls['nse'])}{pnls['nse']:,.0f}
F&O Paper  : {counts['fno']} trades | Rs.{sign(pnls['fno'])}{pnls['fno']:,.0f}
Crypto     : {counts['crypto']} trades | {sign(pnls['crypto'])}{pnls['crypto']:.2f} USDT
US Stocks  : {counts['us']} trades | ${sign(pnls['us'])}{pnls['us']:.2f}

*Combined: Rs.{sign(combined)}{combined:,.0f}*""")
    except Exception as e:
        _send(token, chat_id, f"Error fetching P&L: `{e}`")


def _cmd_signals(token, chat_id):
    try:
        from memory.portfolio_memory import PortfolioMemory
        from config import MIN_CONFIDENCE
        sigs = PortfolioMemory().get_recent_signals(limit=20)
        today = datetime.now().strftime("%Y-%m-%d")
        today_sigs = [s for s in sigs
                      if s["timestamp"].startswith(today)
                      and s["action"] == "BUY"
                      and s.get("confidence", 0) >= MIN_CONFIDENCE]
        if not today_sigs:
            today_sigs = [s for s in sigs if s["action"] == "BUY"][:5]

        if not today_sigs:
            _send(token, chat_id, "No buy signals today. Run /run to trigger a scan.")
            return

        lines = [f"*Buy Signals — {len(today_sigs)} found*", ""]
        for s in today_sigs[:5]:
            ep = s.get("entry_price", 0) or 0
            sl = s.get("stop_loss", 0) or 0
            tp = s.get("take_profit", 0) or 0
            lines.append(
                f"*{s['symbol']}* — Conf `{s['confidence']:.0%}` TA `{s['ta_score']}/10`\n"
                f"Entry `Rs.{ep:,.0f}` | SL `Rs.{sl:,.0f}` | TP `Rs.{tp:,.0f}`"
            )
            lines.append("")
        _send(token, chat_id, "\n".join(lines))
    except Exception as e:
        _send(token, chat_id, f"Error fetching signals: `{e}`")


def _cmd_positions(token, chat_id):
    try:
        pf  = _load_json("logs/virtual_portfolio.json")
        pos = pf.get("positions", {})
        if not pos:
            _send(token, chat_id, "No open NSE positions. Agent is fully in cash.")
            return
        lines = [f"*Open NSE Positions ({len(pos)})*", ""]
        for sym, p in pos.items():
            entry = p.get("entry", 0)
            qty   = p.get("qty", 0)
            sl    = p.get("stop_loss", 0)
            tp    = p.get("take_profit", 0)
            lines.append(
                f"*{sym}* — {qty} shares\n"
                f"Entry `Rs.{entry:,.2f}` | SL `Rs.{sl:,.0f}` | TP `Rs.{tp:,.0f}`"
            )
            lines.append("")
        _send(token, chat_id, "\n".join(lines))
    except Exception as e:
        _send(token, chat_id, f"Error: `{e}`")


def _cmd_crypto(token, chat_id):
    try:
        from execution.brokers.crypto_paper_broker import CryptoPaperBroker
        broker = CryptoPaperBroker()
        stats  = broker.get_stats()
        open_p = broker.get_open_positions()

        lines = [
            f"*Crypto Paper — {stats.get('open_positions', 0)} open*",
            f"Win Rate: {stats.get('win_rate', 0):.0f}% | "
            f"Total P&L: {stats.get('total_pnl_usdt', 0):+.2f} USDT",
            "",
        ]
        if open_p:
            for p in open_p[:5]:
                entry = p["entry_price"]
                curr  = p["current_price"] or entry
                pnl   = p["pnl_usdt"] or 0
                chg   = (curr - entry) / entry * 100 if entry else 0
                lines.append(
                    f"*{p['symbol']}* {p['direction']}\n"
                    f"Entry `{entry:.4f}` → Now `{curr:.4f}` | P&L `{pnl:+.2f} USDT ({chg:+.1f}%)`"
                )
                lines.append("")
        else:
            lines.append("No open crypto positions.")
        _send(token, chat_id, "\n".join(lines))
    except Exception as e:
        _send(token, chat_id, f"Error: `{e}`")


def _cmd_us(token, chat_id):
    try:
        from execution.brokers.us_paper_broker import USPaperBroker
        broker = USPaperBroker()
        stats  = broker.get_stats()
        open_p = broker.get_open_positions()

        lines = [
            f"*US Stocks Paper — {stats.get('open_positions', 0)} open*",
            f"Win Rate: {stats.get('win_rate', 0):.0f}% | "
            f"Total P&L: ${stats.get('total_pnl_usd', 0):+.2f}",
            "",
        ]
        if open_p:
            for p in open_p[:5]:
                entry = p["entry_price"]
                curr  = p["current_price"] or entry
                pnl   = p["pnl_usd"] or 0
                chg   = (curr - entry) / entry * 100 if entry else 0
                lines.append(
                    f"*{p['symbol']}* {p['direction']}\n"
                    f"Entry `${entry:.2f}` → Now `${curr:.2f}` | P&L `${pnl:+.2f} ({chg:+.1f}%)`"
                )
                lines.append("")
        else:
            lines.append("No open US positions.")
        _send(token, chat_id, "\n".join(lines))
    except Exception as e:
        _send(token, chat_id, f"Error: `{e}`")


def _cmd_fno(token, chat_id):
    try:
        from execution.brokers.fno_paper_broker import FNOPaperBroker
        broker = FNOPaperBroker()
        stats  = broker.get_stats()
        open_p = broker.get_open_positions()

        lines = [
            f"*F&O Paper — {stats.get('open_positions', 0)} open*",
            f"Win Rate: {stats.get('win_rate', 0):.0f}% | "
            f"Total P&L: Rs.{stats.get('total_pnl', 0):+,.0f}",
            "",
        ]
        if open_p:
            for p in open_p[:5]:
                entry  = p["entry_premium"]
                curr   = p["current_premium"] or entry
                pnl    = p["pnl"] or 0
                lines.append(
                    f"*{p['instrument']}* {p['option_type']} {p['strike']}\n"
                    f"Entry `Rs.{entry:.1f}` → Now `Rs.{curr:.1f}` | P&L `Rs.{pnl:+,.0f}`"
                )
                lines.append("")
        else:
            lines.append("No open F&O positions.")
        _send(token, chat_id, "\n".join(lines))
    except Exception as e:
        _send(token, chat_id, f"Error: `{e}`")


def _cmd_regime(token, chat_id):
    try:
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

        regime_icon = "🟢" if rg == "BULL" else "🔴" if rg == "BEAR" else "🟡"
        trade_status = "✅ TRADING ACTIVE" if allow else "🚫 TRADING BLOCKED"

        _send(token, chat_id, f"""*Market Regime*

{regime_icon} Regime: *{rg}* — {trade_status}
📊 Nifty RSI: `{rsi:.1f}`
📈 Nifty 1M Return: `{ret_1m:+.2f}%`

PCR: `{pcr_val:.2f}` — {pcr_sig}
FII: `Rs.{fii_net:+,.0f}Cr` — {fii_sig}""")
    except Exception as e:
        _send(token, chat_id, f"Error: `{e}`")


def _cmd_run(token, chat_id):
    _send(token, chat_id, "⚙️ Running NSE scan... please wait ~30 seconds.")
    try:
        from main import run_agent
        sigs = run_agent(dry_run=False)
        actionable = [s for s in sigs if getattr(s, "action", "") == "BUY"]
        _send(token, chat_id,
              f"✅ Scan complete — {len(actionable)} buy signal(s) found.\n"
              f"Use /signals to see them.")
    except Exception as e:
        _send(token, chat_id, f"❌ Scan failed: `{e}`")


# ── Command dispatcher ────────────────────────────────────────────────────────

COMMANDS = {
    "/start":     _cmd_start,
    "/help":      _cmd_start,
    "/status":    _cmd_status,
    "/pnl":       _cmd_pnl,
    "/signals":   _cmd_signals,
    "/positions": _cmd_positions,
    "/crypto":    _cmd_crypto,
    "/us":        _cmd_us,
    "/fno":       _cmd_fno,
    "/regime":    _cmd_regime,
    "/run":       _cmd_run,
}


def _handle_update(update: dict, token: str, chat_id: str):
    """Process a single Telegram update."""
    msg = update.get("message", {})
    text = msg.get("text", "").strip()
    from_chat = str(msg.get("chat", {}).get("id", ""))

    # Only respond to our own chat (security: ignore strangers)
    if from_chat != str(chat_id):
        return

    # Extract command (strip @botname suffix if any)
    cmd = text.split("@")[0].lower() if text else ""

    handler = COMMANDS.get(cmd)
    if handler:
        logger.info(f"Bot command: {cmd}")
        try:
            handler(token, chat_id)
        except Exception as e:
            logger.error(f"Command {cmd} failed: {e}")
            _send(token, chat_id, f"❌ Error: `{e}`")
    elif text.startswith("/"):
        _send(token, chat_id,
              f"Unknown command: `{cmd}`\nSend /help for the full list.")


# ── Long-polling loop ─────────────────────────────────────────────────────────

def run_bot():
    """
    Start the Telegram bot polling loop.
    Call this in a background thread — it runs forever.
    """
    import settings.manager as S
    token   = S.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = S.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.warning("Bot: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — bot disabled")
        return

    logger.info("Telegram bot started — listening for commands")
    offset = 0

    while True:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": offset, "timeout": 30, "allowed_updates": ["message"]},
                timeout=35,
            )
            if resp.status_code != 200:
                time.sleep(5)
                continue

            updates = resp.json().get("result", [])
            for upd in updates:
                offset = upd["update_id"] + 1
                try:
                    _handle_update(upd, token, chat_id)
                except Exception as e:
                    logger.error(f"Update handler error: {e}")

        except requests.exceptions.Timeout:
            pass  # normal — long poll timed out, just loop again
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
            time.sleep(10)


def start_bot_thread() -> threading.Thread:
    """Start bot in a daemon thread. Returns the thread."""
    t = threading.Thread(target=run_bot, name="TelegramBot", daemon=True)
    t.start()
    return t
