# utils/alert_formatter.py — Central alert formatting engine
import json
import os
import re
import time

_LOGS_DIR      = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
_COOLDOWN_FILE = os.path.join(_LOGS_DIR, "sl_alert_cooldown.json")
_COOLDOWN_SECS = 1800   # 30 min between SL-approach alerts per symbol

# In-memory cooldown cache — populated at first use, only flushed to disk on actual alert sends.
# This avoids a JSON file read on every price-check cycle (every 5 min × N positions).
_cooldown_cache: dict[str, float] = {}
_cache_loaded = False


# ── Markdown conversion ───────────────────────────────────────────────────────

def tg_to_discord(text: str) -> str:
    """Convert Telegram Markdown (*bold*) to Discord Markdown (**bold**)."""
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"**\1**", text)
    text = text.replace("\n---\n", "\n─────────────────\n")
    return text


# ── Message chunking ──────────────────────────────────────────────────────────

def chunk_message(text: str, limit: int = 4000) -> list[str]:
    """Split a message into chunks ≤ limit chars, breaking at newlines where possible."""
    if len(text) <= limit:
        return [text]

    chunks:  list[str] = []
    current: str       = ""

    for line in text.split("\n"):
        candidate = f"{current}\n{line}".lstrip("\n") if current else line
        if len(candidate) > limit:
            if current:
                chunks.append(current)
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line
        else:
            current = candidate

    if current:
        chunks.append(current)
    return chunks


# ── SL approach cooldown ──────────────────────────────────────────────────────

def _ensure_cache_loaded() -> None:
    global _cache_loaded
    if _cache_loaded:
        return
    try:
        with open(_COOLDOWN_FILE) as f:
            _cooldown_cache.update(json.load(f))
    except Exception:
        pass
    _cache_loaded = True


def _persist_cache() -> None:
    try:
        os.makedirs(_LOGS_DIR, exist_ok=True)
        with open(_COOLDOWN_FILE, "w") as f:
            json.dump(_cooldown_cache, f)
    except Exception:
        pass


def sl_alert_allowed(symbol: str) -> bool:
    """
    Return True if an SL-approaching alert may be sent for this symbol.
    Enforces a 30-minute per-symbol cooldown. Uses in-memory cache; only
    hits disk when an alert is actually allowed (at most once per 30 min).
    """
    _ensure_cache_loaded()
    now  = time.time()
    last = _cooldown_cache.get(symbol, 0)
    if now - last < _COOLDOWN_SECS:
        return False
    _cooldown_cache[symbol] = now
    _persist_cache()
    return True


# ── Emoji helpers ─────────────────────────────────────────────────────────────

def pnl_icon(pnl: float) -> str:
    return "✅" if pnl > 0 else "🔴"


def trend_icon(pct: float) -> str:
    if pct > 0:
        return "📈"
    if pct < 0:
        return "📉"
    return "➡️"
