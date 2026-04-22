# =============================================================================
# analysis/gift_nifty.py — GIFT Nifty Pre-Market Gap Analyser
#
# Checks GIFT Nifty (traded on NSE IFSC before Indian market opens) to gauge
# the expected opening gap for Nifty 50.
#
# A large positive gap → expect a strong open → lean bullish on morning signals
# A large negative gap → expect weak open → raise confidence bar or skip
#
# Uses yfinance Singapore Nifty proxy if available; falls back to US futures.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass
from utils import get_logger

logger = get_logger("GiftNifty")

# Gap thresholds
GAP_STRONG_UP   =  0.5   # > +0.5% → strong positive
GAP_MILD_UP     =  0.2   # > +0.2% → mild positive
GAP_MILD_DOWN   = -0.2   # < -0.2% → mild negative
GAP_STRONG_DOWN = -0.5   # < -0.5% → strong negative


@dataclass
class GiftNiftyResult:
    gap_pct:     float     # expected gap % vs previous Nifty close
    signal:      str       # strong_up | mild_up | flat | mild_down | strong_down
    nifty_prev:  float     # previous Nifty 50 close
    gift_level:  float     # GIFT Nifty / proxy level
    source:      str       # data source used
    message:     str


class GiftNiftyAnalyser:
    """
    Estimates market opening gap from GIFT Nifty or proxy data.
    Called once pre-market (around 8:30 AM IST) from the scheduler.
    """

    def get_signal(self) -> GiftNiftyResult:
        """Fetch GIFT Nifty level and classify opening gap."""
        try:
            import yfinance as yf

            # Fetch previous Nifty 50 close
            nifty = yf.Ticker("^NSEI")
            hist  = nifty.history(period="5d", interval="1d")
            if hist.empty:
                return self._flat("No Nifty data")

            nifty_prev = float(hist["Close"].iloc[-1])

            # Try GIFT Nifty futures (NSE IFSC trades as NI=F on some providers)
            # yfinance doesn't directly support GIFT Nifty yet — use SGX proxy
            # The best available free proxy is Nifty futures continuation on CME
            gift_level = None
            source     = ""

            for ticker, label in [("NI=F", "GIFT Nifty"), ("ES=F", "S&P500 fut")]:
                try:
                    t    = yf.Ticker(ticker)
                    data = t.history(period="1d", interval="5m")
                    if not data.empty:
                        gift_level = float(data["Close"].iloc[-1])
                        source     = label
                        # Scale S&P proxy to Nifty (very rough correlation ~0.85)
                        if ticker == "ES=F":
                            sp_hist = yf.Ticker("^GSPC").history(period="5d", interval="1d")
                            if not sp_hist.empty:
                                sp_prev = float(sp_hist["Close"].iloc[-1])
                                sp_now  = gift_level
                                sp_chg  = (sp_now - sp_prev) / sp_prev
                                gift_level = nifty_prev * (1 + sp_chg * 0.85)
                                source = "S&P futures proxy"
                        break
                except Exception:
                    continue

            if gift_level is None or gift_level <= 0:
                return self._flat("GIFT Nifty data unavailable")

            gap_pct = (gift_level - nifty_prev) / nifty_prev * 100

            if gap_pct >= GAP_STRONG_UP:
                signal  = "strong_up"
                message = (f"GIFT Nifty: strong positive gap +{gap_pct:.2f}% "
                           f"({source}). Market likely to open strongly — "
                           f"favour long setups.")
            elif gap_pct >= GAP_MILD_UP:
                signal  = "mild_up"
                message = (f"GIFT Nifty: mild positive gap +{gap_pct:.2f}% "
                           f"({source}). Slightly positive open expected.")
            elif gap_pct <= GAP_STRONG_DOWN:
                signal  = "strong_down"
                message = (f"GIFT Nifty: strong negative gap {gap_pct:.2f}% "
                           f"({source}). Weak open expected — raise confidence bar.")
            elif gap_pct <= GAP_MILD_DOWN:
                signal  = "mild_down"
                message = (f"GIFT Nifty: mild negative gap {gap_pct:.2f}% "
                           f"({source}). Slightly negative open expected.")
            else:
                signal  = "flat"
                message = (f"GIFT Nifty: flat gap {gap_pct:+.2f}% ({source}). "
                           f"Normal open expected.")

            logger.info(f"[GIFT] {message}")
            return GiftNiftyResult(
                gap_pct    = round(gap_pct, 3),
                signal     = signal,
                nifty_prev = round(nifty_prev, 2),
                gift_level = round(gift_level, 2),
                source     = source,
                message    = message,
            )

        except Exception as e:
            logger.warning(f"GIFT Nifty check failed: {e}")
            return self._flat(f"Error: {e}")

    def _flat(self, reason: str) -> GiftNiftyResult:
        return GiftNiftyResult(
            gap_pct=0.0, signal="flat", nifty_prev=0.0,
            gift_level=0.0, source="unavailable",
            message=f"GIFT Nifty unavailable — {reason}. Proceeding normally."
        )
