# =============================================================================
# analysis/fno_ban.py — NSE F&O Ban List Checker
#
# Fetches the daily NSE F&O ban list and blocks signals for banned stocks.
# Banned stocks have artificially high OI / volatility — not worth trading.
#
# NSE publishes the list daily at market open.
# We cache it for 6 hours so the scheduler doesn't hammer NSE on every scan.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import time
import requests
from datetime import datetime
from utils import get_logger

logger = get_logger("FnOBan")

_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "fno_ban_cache.json"
)
_CACHE_TTL = 6 * 3600   # refresh every 6 hours

# Headers required by NSE (they block plain requests)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://www.nseindia.com",
}

# Known fallback ban list (as of early 2025) — used if NSE API is unreachable
_FALLBACK_BAN: list[str] = []


def _fetch_from_nse() -> list[str]:
    """Fetch current F&O ban list from NSE API."""
    session = requests.Session()
    # Warm up session cookie
    session.get("https://www.nseindia.com", headers=_HEADERS, timeout=10)
    r = session.get(
        "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O%20BAN%20PERIOD",
        headers=_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    symbols = [row["symbol"] for row in data.get("data", []) if "symbol" in row]
    return symbols


def _load_cache() -> tuple[list[str], float]:
    """Load cached ban list. Returns (symbols, timestamp)."""
    try:
        with open(_CACHE_FILE) as f:
            obj = json.load(f)
            return obj.get("symbols", []), obj.get("timestamp", 0)
    except Exception:
        return [], 0


def _save_cache(symbols: list[str]):
    os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
    with open(_CACHE_FILE, "w") as f:
        json.dump({"symbols": symbols, "timestamp": time.time()}, f)


def get_ban_list() -> list[str]:
    """
    Return current F&O ban list (symbols only, uppercase).
    Uses cache if fresh; fetches from NSE if stale.
    """
    cached, ts = _load_cache()
    if time.time() - ts < _CACHE_TTL and cached:
        logger.debug(f"F&O ban list from cache: {len(cached)} symbols")
        return cached

    try:
        symbols = _fetch_from_nse()
        _save_cache(symbols)
        logger.info(f"F&O ban list refreshed: {len(symbols)} symbols")
        return symbols
    except Exception as e:
        logger.warning(f"F&O ban list fetch failed: {e} — using cache/fallback")
        return cached or _FALLBACK_BAN


class FnOBanFilter:
    """
    Blocks signals for stocks currently on the NSE F&O ban list.
    Call once per session (ban list is cached).
    """

    def __init__(self):
        self._banned: set[str] = set(get_ban_list())
        logger.info(f"F&O ban filter loaded: {len(self._banned)} banned symbols")

    def is_banned(self, symbol: str) -> bool:
        bare = symbol.replace("INTRA:", "").replace(".NS", "").upper()
        return bare in self._banned

    def filter_signals(self, signals: list) -> tuple[list, list]:
        """
        Filter a list of TradeSignal objects.
        Returns (allowed, blocked).
        """
        allowed, blocked = [], []
        for sig in signals:
            if self.is_banned(sig.symbol):
                logger.info(f"F&O ban: blocking {sig.symbol} (on ban list)")
                blocked.append(sig)
            else:
                allowed.append(sig)
        if blocked:
            logger.info(f"F&O ban filter: {len(blocked)} blocked, {len(allowed)} allowed")
        return allowed, blocked

    def filter_symbols(self, symbols: list[str]) -> tuple[list[str], list[str]]:
        """Filter a plain list of symbol strings."""
        allowed = [s for s in symbols if not self.is_banned(s)]
        blocked = [s for s in symbols if self.is_banned(s)]
        return allowed, blocked
