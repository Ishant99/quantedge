import requests

from utils import get_logger

logger = get_logger("Discord")


def _cfg(key: str, default=None):
    try:
        import settings.manager as S
        return S.get(key, default)
    except Exception:
        return default


def send(text: str) -> bool:
    """Send a plain text message to Discord. Returns True if sent."""
    token = _cfg("DISCORD_BOT_TOKEN", "")
    channel_id = _cfg("DISCORD_CHANNEL_ID", "")
    if not token or not channel_id:
        return False

    try:
        r = requests.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
            },
            json={"content": text[:1900]},
            timeout=10,
        )
        if r.status_code in (200, 201):
            logger.info("Discord alert sent")
            return True
        logger.warning(f"Discord failed: {r.status_code} — {r.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"Discord error: {e}")
        return False
