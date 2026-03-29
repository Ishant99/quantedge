# =============================================================================
# analysis/pcr_signal.py — Nifty Options Put/Call Ratio Signal
#
# PCR > 1.3 = market oversold (contrarian BUY signal)
# PCR < 0.7 = market overbought (contrarian SELL signal)
# PCR 0.7-1.3 = neutral
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from dataclasses import dataclass
from utils import get_logger

logger = get_logger("PCRSignal")

NSE_OPTION_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}


@dataclass
class PCRResult:
    pcr:        float     # Put/Call ratio
    signal:     str       # strong_buy | buy | neutral | sell | strong_sell
    score:      float     # 0-10
    total_puts: float     # total put OI
    total_calls:float     # total call OI
    message:    str


class PCRAnalyser:
    """
    Fetches Nifty 50 options Put/Call Ratio from NSE.
    High PCR = lots of put buying = fear = contrarian buy.
    Low PCR  = lots of call buying = greed = contrarian sell.
    """

    def get_signal(self) -> PCRResult:
        """Fetch PCR from NSE options chain."""
        try:
            session = requests.Session()
            session.get("https://www.nseindia.com", headers=HEADERS, timeout=10)
            resp = session.get(NSE_OPTION_CHAIN_URL, headers=HEADERS, timeout=15)

            if resp.status_code == 200:
                data = resp.json()
                return self._parse(data)
        except Exception as e:
            logger.debug(f"NSE PCR fetch failed: {e}")

        return self._estimate_from_vix()

    def _parse(self, data: dict) -> PCRResult:
        """Parse NSE option chain response."""
        try:
            records    = data["records"]["data"]
            total_puts = sum(
                r.get("PE", {}).get("openInterest", 0)
                for r in records if "PE" in r
            )
            total_calls= sum(
                r.get("CE", {}).get("openInterest", 0)
                for r in records if "CE" in r
            )

            if total_calls == 0:
                return self._default()

            pcr = total_puts / total_calls

            signal, score, msg = self._classify(pcr)

            logger.info(f"PCR: {pcr:.2f} — {signal} ({msg})")
            return PCRResult(
                pcr        = round(pcr, 2),
                signal     = signal,
                score      = score,
                total_puts = total_puts,
                total_calls= total_calls,
                message    = msg,
            )
        except Exception as e:
            logger.debug(f"PCR parse error: {e}")
            return self._default()

    def _classify(self, pcr: float) -> tuple[str, float, str]:
        if pcr > 1.5:
            return "strong_buy", 9.0, f"PCR {pcr:.2f} — extreme fear, contrarian buy"
        elif pcr > 1.2:
            return "buy", 7.5, f"PCR {pcr:.2f} — put heavy, market oversold"
        elif pcr > 0.8:
            return "neutral", 5.0, f"PCR {pcr:.2f} — balanced options market"
        elif pcr > 0.6:
            return "sell", 3.5, f"PCR {pcr:.2f} — call heavy, market overbought"
        else:
            return "strong_sell", 2.0, f"PCR {pcr:.2f} — extreme greed, caution"

    def _estimate_from_vix(self) -> PCRResult:
        """Estimate PCR from India VIX as fallback."""
        try:
            import yfinance as yf
            vix = yf.Ticker("^INDIAVIX").history(period="5d")
            if not vix.empty:
                vix_val = float(vix["Close"].iloc[-1])
                # High VIX = high fear = high PCR equivalent
                if vix_val > 20:
                    pcr = 1.3
                elif vix_val > 15:
                    pcr = 1.0
                else:
                    pcr = 0.85
                signal, score, msg = self._classify(pcr)
                msg += f" (estimated from VIX {vix_val:.1f})"
                return PCRResult(pcr=pcr, signal=signal, score=score,
                                 total_puts=0, total_calls=0, message=msg)
        except Exception:
            pass
        return self._default()

    def _default(self) -> PCRResult:
        return PCRResult(pcr=1.0, signal="neutral", score=5.0,
                         total_puts=0, total_calls=0,
                         message="PCR data unavailable — neutral assumption")
