# =============================================================================
# analysis/fii_dii.py — F5: FII/DII Flow Tracker
# Tracks institutional buying/selling. FII net buyers 3 days = bullish.
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests, json
from datetime import datetime, timedelta
from dataclasses import dataclass
from utils import get_logger

logger = get_logger("FIIDIITracker")
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_FILE = os.path.join(_PROJECT_ROOT, "logs", "fii_dii_cache.json")

@dataclass
class FlowData:
    date:          str
    fii_net:       float   # crores, positive = buying
    dii_net:       float
    combined_net:  float
    signal:        str     # bullish | bearish | neutral
    consecutive_days: int  # consecutive days of same direction

class FIIDIITracker:
    """
    F5 — Tracks FII and DII flows. Strong institutional buying = extra
    confidence in BUY signals. Strong selling = caution flag.
    """

    def get_flow(self) -> FlowData:
        data = self._load_or_fetch()
        if not data:
            return self._neutral()

        recent = data[-5:]   # last 5 days
        latest = recent[-1]

        fii_net = latest.get("fii_net", 0)
        dii_net = latest.get("dii_net", 0)
        combined= fii_net + dii_net

        # Count consecutive buying/selling days
        consec = 1
        direction = "buy" if fii_net > 0 else "sell"
        for day in reversed(recent[:-1]):
            d = "buy" if day.get("fii_net", 0) > 0 else "sell"
            if d == direction:
                consec += 1
            else:
                break

        if combined > 500 and consec >= 2:
            signal = "bullish"
        elif combined < -500 and consec >= 2:
            signal = "bearish"
        else:
            signal = "neutral"

        return FlowData(
            date=latest.get("date",""),
            fii_net=round(fii_net, 2),
            dii_net=round(dii_net, 2),
            combined_net=round(combined, 2),
            signal=signal,
            consecutive_days=consec,
        )

    def get_signal(self):
        """
        Returns an object compatible with main.py expectations:
          .signal  — "buy" | "strong_buy" | "sell" | "strong_sell" | "neutral"
          .message — human-readable summary
          .score   — float in [-1, 1]  (used for dynamic sizing)
        """
        from types import SimpleNamespace
        flow = self.get_flow()

        # Map internal bullish/bearish → buy/sell vocabulary
        # score is 0-10 scale (used by DynamicPositionSizer: 5 = neutral)
        if flow.signal == "bullish" and flow.consecutive_days >= 3:
            sig, score = "strong_buy", 9.0
        elif flow.signal == "bullish":
            sig, score = "buy", 7.0
        elif flow.signal == "bearish" and flow.consecutive_days >= 3:
            sig, score = "strong_sell", 1.0
        elif flow.signal == "bearish":
            sig, score = "sell", 3.0
        else:
            sig, score = "neutral", 5.0

        msg = (
            f"FII net Rs.{flow.fii_net:+,.0f} Cr | "
            f"DII net Rs.{flow.dii_net:+,.0f} Cr | "
            f"{flow.consecutive_days}d streak -> {sig.upper()}"
        )
        return SimpleNamespace(signal=sig, score=score, message=msg, flow=flow)

    def get_multiplier(self) -> float:
        """Returns position size multiplier based on FII/DII flow."""
        flow = self.get_flow()
        if flow.signal == "bullish":
            return 1.2    # 20% larger positions when institutions buying
        elif flow.signal == "bearish":
            return 0.7    # 30% smaller when selling
        return 1.0

    def _neutral(self) -> FlowData:
        return FlowData(date="", fii_net=0, dii_net=0,
                       combined_net=0, signal="neutral", consecutive_days=0)

    def _load_or_fetch(self) -> list:
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE) as f:
                    cached = json.load(f)
                age = (datetime.now() -
                       datetime.fromisoformat(cached.get("cached_at","2000-01-01"))).total_seconds()
                if age < 86400:
                    return cached.get("data", [])
            except Exception:
                pass
        return self._fetch()

    def _fetch(self) -> list:
        """Fetch FII/DII data from NSE."""
        try:
            headers = {"User-Agent": "Mozilla/5.0",
                       "Referer": "https://www.nseindia.com"}
            s = requests.Session()
            s.get("https://www.nseindia.com", headers=headers, timeout=5)
            url = "https://www.nseindia.com/api/fiidiiTradeReact"
            r   = s.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                raw  = r.json()
                data = []
                for item in raw[:10]:
                    try:
                        data.append({
                            "date":    item.get("date",""),
                            "fii_net": float(str(item.get("fiiNet","0")).replace(",","")),
                            "dii_net": float(str(item.get("diiNet","0")).replace(",","")),
                        })
                    except Exception:
                        pass
                if data:
                    os.makedirs(os.path.join(_PROJECT_ROOT, "logs"), exist_ok=True)
                    with open(CACHE_FILE, "w") as f:
                        json.dump({"cached_at": datetime.now().isoformat(),
                                   "data": data}, f, indent=2)
                    logger.info(f"FII/DII data fetched: {len(data)} days")
                    return data
        except Exception as e:
            logger.warning(f"FII/DII fetch failed: {e}")
        return []

# Alias for backward compatibility
FIIDIIAnalyser = FIIDIITracker
