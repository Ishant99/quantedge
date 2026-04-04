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
    "PAPER_MAX_ALLOC_NSE_PCT": 0.40,
    "PAPER_MAX_ALLOC_FNO_PCT": 0.30,
    "PAPER_MAX_ALLOC_US_PCT": 0.20,
    "PAPER_MAX_ALLOC_CRYPTO_PCT": 0.10,

    # ---- Strategy ----
    "MIN_TA_SCORE":          5.0,
    "MIN_CONFIDENCE":        0.60,
    "TA_SIGNAL_BULLISH":     6.5,   # score >= this → bullish signal
    "TA_SIGNAL_BEARISH":     4.0,   # score <= this → bearish signal
    "TA_MIN_TREND_ADX":      18.0,
    "TA_MAX_BUY_STOCH":      88.0,
    "TOP_N_SIGNALS":      10,
    "TA_WEIGHT":          0.50,
    "SENTIMENT_WEIGHT":   0.30,
    "STRATEGY_QUALITY_MIN_RESOLVED": 3,
    "STRATEGY_QUALITY_WEAK_SYMBOL_TP_PCT": 35.0,
    "STRATEGY_QUALITY_STRONG_SYMBOL_TP_PCT": 60.0,
    "STRATEGY_QUALITY_SETUP_WEIGHT": 0.20,
    "STRATEGY_QUALITY_SYMBOL_WEIGHT": 0.20,
    "STRATEGY_QUALITY_CONF_BUCKET_WEIGHT": 0.10,
    "STRATEGY_QUALITY_REGIME_WEIGHT": 0.10,
    "STRATEGY_QUALITY_BLOCK_WEAK_SYMBOLS": True,
    "STRATEGY_QUALITY_MAX_PENALTY": 0.20,
    "STRATEGY_QUALITY_MAX_BOOST": 0.12,

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
    "FNO_FUT_MARGIN_PCT":    0.15,
    "FNO_SELL_RESERVE_MULT": 2.5,
    "FNO_MAX_STRUCTURES_PER_UNDERLYING": 2,
    "FNO_MAX_UNDERLYING_EXPOSURE_NIFTY_PCT": 0.15,
    "FNO_MAX_UNDERLYING_EXPOSURE_BANKNIFTY_PCT": 0.15,
    "FNO_BLOCK_DUPLICATE_FUT_SHORT_WITH_STRADDLE": True,
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
