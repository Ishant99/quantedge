# =============================================================================
# settings/manager.py — Persistent user settings
#
# Reads/writes logs/user_settings.json so the dashboard can configure
# everything without SSH access. Values here override config.py defaults.
#
# Usage:
#   from settings.manager import load, save, get, set_value
# =============================================================================

import json
import os
import threading

_LOCK = threading.Lock()

# Absolute path — works whether called from root, dashboard/, scheduler/ etc.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(_ROOT, "logs", "user_settings.json")

# All user-configurable settings with their defaults.
# These match config.py names so config.py can defer to this.
DEFAULTS: dict = {
    # ---- API & Integration ----
    "TELEGRAM_BOT_TOKEN":  "",
    "TELEGRAM_CHAT_ID":    "",
    "DISCORD_BOT_TOKEN":   "",
    "DISCORD_CHANNEL_ID":  "",
    "KITE_API_KEY":        "",
    "KITE_API_SECRET":     "",

    # ---- Mode ----
    "TRADING_MODE": "paper",       # paper | live
    "AGENT_MODE":   "copilot",     # copilot | autopilot

    # ---- Capital ----
    "VIRTUAL_CAPITAL": 1_000_000,

    # ---- Strategy ----
    "MIN_TA_SCORE":          5.0,
    "MIN_CONFIDENCE":        0.60,
    "TA_SIGNAL_BULLISH":     6.5,   # score >= this → bullish signal
    "TA_SIGNAL_BEARISH":     4.0,   # score <= this → bearish signal
    "TOP_N_SIGNALS":      10,
    "TA_WEIGHT":          0.50,
    "SENTIMENT_WEIGHT":   0.30,

    # ---- Risk ----
    "RISK_PER_TRADE_PCT":  0.02,
    "MAX_OPEN_POSITIONS":  5,
    "REWARD_RISK_RATIO":   2.0,
    "ATR_SL_MULTIPLIER":   1.5,
    "TRAIL_PCT":           0.02,
    "MAX_DAILY_LOSS_PCT":  0.03,
    "MAX_WEEKLY_LOSS_PCT": 0.07,
    "MAX_SAME_SECTOR":     2,
    "CORRELATION_THRESHOLD": 0.75,
    "SECTOR_HOT_MULT":     1.2,
    "SECTOR_COLD_MULT":    0.7,

    # ---- Scheduler ----
    "SCAN_TIME_1": "09:15",    # first daily scan (IST)
    "SCAN_TIME_2": "15:00",    # second daily scan (IST)

    # ---- F&O settings ----
    "FNO_TP_MULT":           2.0,
    "FNO_SL_MULT":           0.50,
    "FNO_MAX_POSITIONS":     6,
    "FNO_HV_STRADDLE":       18.0,
    "FNO_HV_STRANGLE":       12.0,
    "FNO_SELL_DAYS":         "tue,wed,thu",
    "FNO_CHAIN_CACHE_MIN":   5,
    "FUTURES_RISK_FREE_RATE":0.065,
    "FUTURES_DEFAULT_DTE":   15,
    "FUTURES_SL_PCT":        0.02,
    "FUTURES_TP_PCT":        0.03,
    "INR_PER_USD":           83.0,
    "INR_PER_USDT":          83.0,

    # ---- Crypto paper trading ----
    "CRYPTO_USDT_PER_TRADE": 100.0,
    "CRYPTO_TP_PCT":         0.08,
    "CRYPTO_SL_PCT":         0.04,

    # ---- US stocks paper trading ----
    "US_USD_PER_TRADE":      500.0,
    "US_TP_PCT":             0.06,
    "US_SL_PCT":             0.03,

    # ---- Dashboard ----
    "DASHBOARD_REFRESH_SEC": 30,
}

_cache: dict | None = None


def _ensure_dir():
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)


def load() -> dict:
    """Load settings from disk, merged with DEFAULTS. Cached until reload() is called."""
    global _cache
    if _cache is not None:
        return _cache
    with _LOCK:
        if _cache is not None:
            return _cache
        _ensure_dir()
        try:
            with open(SETTINGS_FILE) as f:
                saved = json.load(f)
            _cache = {**DEFAULTS, **saved}
        except (FileNotFoundError, json.JSONDecodeError):
            _cache = dict(DEFAULTS)
    return _cache


def save(updates: dict) -> None:
    """Merge updates into current settings and write to disk."""
    global _cache
    current = load()
    current.update(updates)
    _ensure_dir()
    with _LOCK:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(current, f, indent=2)
        _cache = current


def get(key: str, default=None):
    """Get a single setting value."""
    val = load().get(key)
    if val is None or val == "":
        return default if default is not None else DEFAULTS.get(key)
    return val


def set_value(key: str, value) -> None:
    """Set a single setting and persist immediately."""
    save({key: value})


def reload() -> dict:
    """Invalidate cache and reload from disk."""
    global _cache
    _cache = None
    return load()


def all_settings() -> dict:
    """Return a copy of all current settings."""
    return dict(load())
