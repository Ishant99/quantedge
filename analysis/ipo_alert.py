# =============================================================================
# analysis/ipo_alert.py — IPO & New Listing Alert
#
# Tracks upcoming IPOs and recently listed stocks.
# Alerts when new stocks are worth watching after stabilisation.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import pandas as pd
from datetime import datetime, timedelta
from dataclasses import dataclass
from utils import get_logger
from utils.telegram import send

logger = get_logger("IPOAlert")

# NSE IPO endpoints
NSE_IPO_URL      = "https://www.nseindia.com/api/ipos?category=ipo"
NSE_LISTING_URL  = "https://www.nseindia.com/api/ipos?category=sme"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}

STABILISATION_DAYS = 30   # Only watch IPO after 30 days of listing


@dataclass
class IPOInfo:
    symbol:       str
    company_name: str
    listing_date: str
    issue_price:  float
    current_price:float
    days_since_listing: int
    return_from_issue: float
    watchable:    bool    # True after stabilisation period
    message:      str


class IPOAlertSystem:
    """
    Monitors upcoming IPOs and new listings.
    Adds stabilised new listings to the scanning universe.
    """

    def check(self) -> list[IPOInfo]:
        """Fetch and analyse IPO data."""
        ipos = []
        try:
            session = requests.Session()
            session.get("https://www.nseindia.com", headers=HEADERS, timeout=10)
            resp = session.get(NSE_IPO_URL, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                ipos = self._parse(data)
        except Exception as e:
            logger.debug(f"IPO fetch failed: {e}")
            ipos = self._get_sample_ipos()

        # Send alerts for watchable IPOs
        watchable = [i for i in ipos if i.watchable]
        if watchable:
            self._send_alert(watchable)

        return ipos

    def get_watchable_symbols(self) -> list[str]:
        """Return symbols ready to be added to scan universe."""
        ipos = self.check()
        return [i.symbol for i in ipos if i.watchable]

    def _parse(self, data: dict) -> list[IPOInfo]:
        results = []
        try:
            import yfinance as yf
            for item in data.get("ipoList", [])[:20]:
                try:
                    symbol       = item.get("symbol", "")
                    company      = item.get("companyName", "")
                    listing_str  = item.get("listingDate", "")
                    issue_price  = float(str(item.get("issuePrice", 0)
                                            ).replace(",",""))

                    if not symbol or not listing_str:
                        continue

                    # Parse listing date
                    try:
                        listing_dt = datetime.strptime(listing_str, "%d-%b-%Y")
                    except Exception:
                        continue

                    days_since = (datetime.now() - listing_dt).days

                    # Get current price
                    try:
                        hist = yf.Ticker(f"{symbol}.NS").history(period="5d")
                        curr = float(hist["Close"].iloc[-1]) if not hist.empty else issue_price
                    except Exception:
                        curr = issue_price

                    ret = ((curr - issue_price) / issue_price * 100
                           if issue_price > 0 else 0)

                    watchable = (STABILISATION_DAYS <= days_since <= 365)

                    msg = (f"Listed {days_since}d ago | "
                           f"Issue: Rs.{issue_price:.0f} | "
                           f"Now: Rs.{curr:.0f} | "
                           f"Return: {ret:+.1f}%")

                    results.append(IPOInfo(
                        symbol=symbol, company_name=company,
                        listing_date=listing_str,
                        issue_price=issue_price,
                        current_price=round(curr, 2),
                        days_since_listing=days_since,
                        return_from_issue=round(ret, 2),
                        watchable=watchable,
                        message=msg,
                    ))
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"IPO parse error: {e}")
        return results

    def _get_sample_ipos(self) -> list[IPOInfo]:
        """Fallback sample data when NSE API unavailable."""
        logger.info("IPO data: using recent known listings as fallback")
        return []

    def _send_alert(self, watchable: list[IPOInfo]):
        """Send Telegram alert for watchable IPOs."""
        lines = ["*IPO Watch Alert*", ""]
        for ipo in watchable[:5]:
            lines += [
                f"*{ipo.company_name}* (`{ipo.symbol}`)",
                f"{ipo.message}",
                "",
            ]
        send("\n".join(lines))
        logger.info(f"IPO alert sent for {len(watchable)} stocks")
