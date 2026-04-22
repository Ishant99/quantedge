# utils/discord.py — Discord alert delivery
#
# Supports:
#   • Plain text messages (auto-converts Telegram markdown → Discord markdown)
#   • Rich embeds (coloured cards for key alerts)
#   • Automatic chunking at 2000-char limit

import requests
from utils import get_logger
from utils.alert_formatter import tg_to_discord, chunk_message

logger = get_logger("Discord")

# Discord embed colour palette
COLOR_GREEN  = 0x2ECC71   # BUY signal, profit
COLOR_RED    = 0xE74C3C   # SL hit, loss, circuit breaker
COLOR_ORANGE = 0xE67E22   # Warning (SL approaching, circuit breaker warning)
COLOR_BLUE   = 0x3498DB   # Info (daily report, weekly summary)
COLOR_PURPLE = 0x9B59B6   # F&O / options
COLOR_GREY   = 0x95A5A6   # Neutral / no signals


def _cfg(key: str, default=None):
    try:
        import settings.manager as S
        return S.get(key, default)
    except Exception:
        return default


def _get_credentials() -> tuple[str, str]:
    """Return (token, channel_url) — both empty strings if not configured."""
    token      = _cfg("DISCORD_BOT_TOKEN", "") or ""
    channel_id = _cfg("DISCORD_CHANNEL_ID", "") or ""
    url        = f"https://discord.com/api/v10/channels/{channel_id}/messages" if channel_id else ""
    return token, url


def _headers(token: str) -> dict:
    return {"Authorization": f"Bot {token}", "Content-Type": "application/json"}


def send(text: str) -> bool:
    """
    Send a plain text message to Discord.
    Automatically converts Telegram markdown and chunks long messages.
    Returns True if all chunks sent successfully.
    """
    token, url = _get_credentials()
    if not token or not url:
        return False

    discord_text = tg_to_discord(text)
    chunks  = chunk_message(discord_text, limit=1990)
    headers = _headers(token)
    all_ok  = True

    for chunk in chunks:
        try:
            r = requests.post(url, headers=headers, json={"content": chunk}, timeout=10)
            if r.status_code not in (200, 201):
                logger.warning(f"Discord failed: {r.status_code} — {r.text[:200]}")
                all_ok = False
        except Exception as e:
            logger.warning(f"Discord error: {e}")
            all_ok = False

    if all_ok:
        logger.info("Discord message sent")
    return all_ok


def send_embed(
    title:       str,
    description: str,
    color:       int            = COLOR_BLUE,
    fields:      list | None    = None,
    footer:      str            = "",
) -> bool:
    """Send a rich Discord embed (coloured card) for key alerts — signals, SL hits, etc."""
    token, url = _get_credentials()
    if not token or not url:
        return False

    embed: dict = {
        "title":       title[:256],
        "description": description[:4096],
        "color":       color,
    }
    if fields:
        embed["fields"] = [
            {"name": f["name"][:256], "value": f["value"][:1024], "inline": f.get("inline", False)}
            for f in fields[:25]
        ]
    if footer:
        embed["footer"] = {"text": footer[:2048]}

    try:
        r = requests.post(url, headers=_headers(token), json={"embeds": [embed]}, timeout=10)
        if r.status_code in (200, 201):
            logger.info("Discord embed sent")
            return True
        logger.warning(f"Discord embed failed: {r.status_code} — {r.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"Discord embed error: {e}")
        return False
