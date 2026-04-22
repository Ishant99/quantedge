# =============================================================================
# analysis/block_deals.py — NSE Block Deals Confidence Booster
#
# Block deals are large institutional transactions (≥ 5 lakh shares or ₹10Cr)
# that must be disclosed on NSE. A large BUY block deal on a stock we're
# already bullish on is a strong confirmation signal.
#
# Effect:
#   Block deal in last 2 days → +0.04 confidence boost if buy side
#   Block deal sell → neutral (informational only)
#
# Data: NSE block deals API (cached daily — block deals are reported EOD)
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import time
import requests
from datetime import datetime, timedelta
from utils import get_logger

logger = get_logger("BlockDeals")

_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "block_deals_cache.json"
)
_CACHE_TTL = 6 * 3600    # refresh every 6 hours

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://www.nseindia.com",
}

BLOCK_DEAL_BOOST = 0.04   # confidence boost for a buy-side block deal


def _fetch_block_deals() -> list[dict]:
    """Fetch NSE block deals for today and yesterday."""
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=_HEADERS, timeout=10)

    deals = []
    for days_back in range(3):
        date_str = (datetime.now() - timedelta(days=days_back)).strftime("%d-%m-%Y")
        try:
            r = session.get(
                f"https://www.nseindia.com/api/block-deal?date={date_str}",
                headers=_HEADERS,
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                for row in data.get("data", []):
                    deals.append({
                        "symbol":   row.get("symbol", "").upper().strip(),
                        "quantity": float(row.get("tdQuantity", 0) or 0),
                        "price":    float(row.get("tdTradePrice", 0) or 0),
                        "side":     "buy" if str(row.get("buySell", "")).upper() == "B" else "sell",
                        "date":     date_str,
                        "client":   row.get("clientName", ""),
                    })
        except Exception as e:
            logger.debug(f"Block deals fetch for {date_str} failed: {e}")

    return deals


def _load_cache() -> tuple[list[dict], float]:
    try:
        with open(_CACHE_FILE) as f:
            obj = json.load(f)
            return obj.get("deals", []), obj.get("timestamp", 0)
    except Exception:
        return [], 0


def _save_cache(deals: list[dict]):
    os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
    with open(_CACHE_FILE, "w") as f:
        json.dump({"deals": deals, "timestamp": time.time()}, f)


def get_block_deals() -> list[dict]:
    """Return recent block deals, using cache when fresh."""
    cached, ts = _load_cache()
    if time.time() - ts < _CACHE_TTL and cached:
        return cached
    try:
        deals = _fetch_block_deals()
        _save_cache(deals)
        logger.info(f"Block deals refreshed: {len(deals)} deals")
        return deals
    except Exception as e:
        logger.warning(f"Block deals fetch failed: {e}")
        return cached


class BlockDealsAnalyser:
    """
    Checks if a symbol had recent institutional block deal activity.
    A buy-side block deal adds +0.04 confidence to an existing BUY signal.
    """

    def __init__(self):
        deals = get_block_deals()
        # Build lookup: symbol → list of recent buy deals
        self._buy_deals: dict[str, list[dict]] = {}
        for d in deals:
            if d["side"] == "buy" and d["symbol"]:
                self._buy_deals.setdefault(d["symbol"], []).append(d)
        logger.info(f"Block deals index: {len(self._buy_deals)} symbols with buy deals")

    def get_boost(self, symbol: str) -> tuple[float, str]:
        """
        Return (confidence_boost, note) for this symbol.
        boost = 0.0 if no block deal found.
        """
        bare = symbol.replace("INTRA:", "").replace(".NS", "").upper()
        deals = self._buy_deals.get(bare, [])
        if not deals:
            return 0.0, ""

        total_qty = sum(d["quantity"] for d in deals)
        dates     = list({d["date"] for d in deals})
        note = (f"Block buy deal: {total_qty:,.0f} shares on {', '.join(dates)}")
        logger.info(f"{symbol}: {note} (+{BLOCK_DEAL_BOOST:.0%} confidence)")
        return BLOCK_DEAL_BOOST, note

    def enrich_signals(self, signals: list) -> list:
        """
        Apply block deal confidence boost to a list of TradeSignal objects.
        Modifies signals in-place and returns the same list.
        """
        for sig in signals:
            boost, note = self.get_boost(sig.symbol)
            if boost > 0:
                sig.confidence = min(0.99, sig.confidence + boost)
                sig.reasoning += f". {note}"
        return signals
