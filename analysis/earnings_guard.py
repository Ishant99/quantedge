# =============================================================================
# analysis/earnings_guard.py — F4: Earnings Calendar Guard
# Checks if a stock has results in the next N days and blocks BUY if so.
# Uses NSE BSE announcements + simple date-based heuristic.
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import json
from datetime import datetime, timedelta
from utils import get_logger

logger = get_logger("EarningsGuard")
EARNINGS_CACHE = "logs/earnings_calendar.json"
BLOCK_DAYS     = 5   # block trades N days before results

class EarningsGuard:
    """
    F4 — Prevents buying stocks with quarterly results coming soon.
    Earnings surprises are the #1 killer of swing trades.
    Data source: NSE corporate announcements API (free, no key needed).
    """

    def __init__(self):
        os.makedirs("logs", exist_ok=True)
        self.calendar = self._load_or_fetch()

    def is_safe(self, symbol: str) -> tuple[bool, str]:
        """
        Returns (True, "") if safe to trade.
        Returns (False, reason) if earnings too close.
        """
        result_date = self.calendar.get(symbol.upper())
        if not result_date:
            return True, ""

        try:
            rd   = datetime.strptime(result_date, "%Y-%m-%d")
            days = (rd - datetime.today()).days
            if 0 <= days <= BLOCK_DAYS:
                return False, f"Results in {days} day(s) on {result_date} — skipping"
            return True, ""
        except Exception:
            return True, ""

    def filter_signals(self, signals: list) -> tuple[list, list]:
        """Filter out signals where earnings are too close."""
        safe = []
        blocked = []
        for sig in signals:
            ok, reason = self.is_safe(sig.symbol)
            if ok:
                safe.append(sig)
            else:
                logger.info(f"EarningsGuard blocked {sig.symbol}: {reason}")
                blocked.append(sig)
        return safe, blocked

    def _load_or_fetch(self) -> dict:
        """Load cached calendar or fetch fresh from NSE."""
        # Try loading cache (valid for 24 hours)
        if os.path.exists(EARNINGS_CACHE):
            try:
                with open(EARNINGS_CACHE) as f:
                    data = json.load(f)
                cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
                if (datetime.now() - cached_at).total_seconds() < 86400:
                    return data.get("calendar", {})
            except Exception:
                pass

        return self._fetch_from_nse()

    def _fetch_from_nse(self) -> dict:
        """Fetch upcoming board meeting / results dates from NSE."""
        calendar = {}
        try:
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": "https://www.nseindia.com",
            }
            session = requests.Session()
            session.get("https://www.nseindia.com", headers=headers, timeout=5)

            url = "https://www.nseindia.com/api/corporate-announcements?index=equities&category=boardmeeting&type=Board+Meeting"
            r   = session.get(url, headers=headers, timeout=10)

            if r.status_code == 200:
                data = r.json()
                for item in data:
                    sym  = item.get("symbol", "")
                    date = item.get("bm_date", "")
                    desc = item.get("bm_desc", "").lower()
                    if sym and date and ("result" in desc or "financial" in desc or "quarterly" in desc):
                        try:
                            dt = datetime.strptime(date, "%d-%b-%Y")
                            calendar[sym] = dt.strftime("%Y-%m-%d")
                        except Exception:
                            pass
                logger.info(f"Earnings calendar: {len(calendar)} upcoming results fetched")
            else:
                logger.warning(f"NSE earnings fetch failed: {r.status_code}")

        except Exception as e:
            logger.warning(f"Earnings calendar fetch failed: {e} — all stocks allowed")

        # Cache it
        try:
            with open(EARNINGS_CACHE, "w") as f:
                json.dump({"cached_at": datetime.now().isoformat(),
                           "calendar": calendar}, f, indent=2)
        except Exception:
            pass

        return calendar
