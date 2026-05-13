# =============================================================================
# analysis/market_regime.py — Market Regime Filter
#
# Checks if the overall market (Nifty 50) is in a bull, bear, or sideways
# regime BEFORE scanning individual stocks.
#
# In a bear regime  → block all BUY signals, only allow SELL
# In a sideways     → raise confidence threshold, reduce position size
# In a bull regime  → full normal operation
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
import yfinance as yf
from utils import get_logger

logger = get_logger("MarketRegime")

_PROJECT_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REGIME_STATE_FILE = os.path.join(_PROJECT_ROOT, "logs", "regime_state.json")
_STABILITY_REQUIRED = 2   # consecutive scans before committing a regime change


@dataclass
class RegimeResult:
    regime:         str     # bull | bear | sideways | recovery
    nifty_trend:    str     # up | down | flat
    nifty_rsi:      float
    nifty_above_200ema: bool
    nifty_1m_return:float   # 1 month return %
    nifty_3m_return:float   # 3 month return %
    allow_buys:     bool
    allow_shorts:   bool    # True in bear/sideways — scan for bearish setups
    position_size_multiplier: float   # 1.0 = normal, 0.5 = cautious, 0.0 = blocked
    message:        str
    stability_count: int = 0  # how many consecutive scans in committed regime


class MarketRegimeFilter:
    """
    Checks Nifty 50 trend before allowing buy signals.
    Protects portfolio from buying into falling markets.
    Uses a 2-scan stability gate to prevent rapid regime flipping on volatile opens.
    """

    NIFTY_TICKER = "^NSEI"

    def __init__(self):
        self._state = self._load_state()

    def _load_state(self) -> dict:
        os.makedirs(os.path.join(_PROJECT_ROOT, "logs"), exist_ok=True)
        if os.path.exists(_REGIME_STATE_FILE):
            try:
                with open(_REGIME_STATE_FILE) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"committed_regime": "bull", "pending_regime": None, "stability_count": 0}

    def _save_state(self):
        with open(_REGIME_STATE_FILE, "w") as f:
            json.dump(self._state, f, indent=2)

    def _apply_hysteresis(self, raw_regime: str) -> tuple[str, int]:
        """
        Returns (committed_regime, stability_count).
        Only commits a new regime after _STABILITY_REQUIRED consecutive matching scans.
        """
        committed = self._state.get("committed_regime", "bull")
        pending   = self._state.get("pending_regime")
        count     = self._state.get("stability_count", 0)

        if raw_regime == committed:
            # Stable — reset any pending transition
            self._state["pending_regime"]  = None
            self._state["stability_count"] = 0
        elif raw_regime == pending:
            # Same pending regime as last scan — increment counter
            count += 1
            self._state["stability_count"] = count
            if count >= _STABILITY_REQUIRED:
                logger.info(f"[REGIME] Committing regime change: {committed} → {raw_regime} "
                            f"(stable for {count} scans)")
                self._state["committed_regime"] = raw_regime
                self._state["pending_regime"]   = None
                self._state["stability_count"]  = 0
                committed = raw_regime
        else:
            # New candidate regime — start fresh counter
            self._state["pending_regime"]  = raw_regime
            self._state["stability_count"] = 1
            logger.debug(f"[REGIME] Pending transition: {committed} → {raw_regime} (1/{_STABILITY_REQUIRED})")

        self._save_state()
        return committed, self._state.get("stability_count", 0)

    def get_regime(self) -> RegimeResult:
        """Fetch Nifty 50 data and determine current market regime."""
        try:
            df = yf.Ticker(self.NIFTY_TICKER).history(
                period="1y", interval="1d", auto_adjust=True
            )
            if df.empty or len(df) < 50:
                logger.warning("Could not fetch Nifty data — assuming bull regime")
                return self._default_bull()

            close = df["Close"] if "Close" in df.columns else df["close"]
            last  = float(close.iloc[-1])

            # EMA 200
            ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
            ema50  = float(close.ewm(span=50,  adjust=False).mean().iloc[-1])
            ema20  = float(close.ewm(span=20,  adjust=False).mean().iloc[-1])

            # RSI — Wilder's RMA
            delta     = close.diff()
            gain      = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
            loss      = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
            last_loss = float(loss.iloc[-1])
            if last_loss == 0:
                rsi = 100.0
            else:
                rsi = float(100 - (100 / (1 + gain / loss)).iloc[-1])

            # Returns
            ret_1m = float((last / close.iloc[-22] - 1) * 100) if len(close) >= 22 else 0
            ret_3m = float((last / close.iloc[-66] - 1) * 100) if len(close) >= 66 else 0

            above_200 = last > ema200

            # ----------------------------------------------------------
            # Regime classification
            # ----------------------------------------------------------
            # Recovery: market bouncing off lows (1M up, 3M still negative)
            recovering = ret_1m > 2.0 and ret_3m < 0

            if (last > ema200 and last > ema50 and ema20 > ema50
                    and ret_1m > -3 and rsi > 40 and ret_3m > 0):
                regime       = "bull"
                trend        = "up"
                allow        = True
                allow_shorts = False
                ps_mult      = 1.0
                message      = "Bull market — Nifty above all EMAs, positive trend. Normal trading."

            elif recovering and rsi > 45:
                # Recovery phase: short-term bounce in medium-term downtrend
                # Allow selective buys at reduced size — these are high-quality setups only
                regime       = "recovery"
                trend        = "up"
                allow        = True
                allow_shorts = False
                ps_mult      = 0.7
                message      = (f"Recovery phase — Nifty bouncing {ret_1m:+.1f}% this month "
                                f"(3M still {ret_3m:+.1f}%). Selective buys only, 70% sizes.")

            elif (last < ema200 and ret_1m < -5 and ret_3m < -8) or rsi < 35:
                regime       = "bear"
                trend        = "down"
                allow        = False
                allow_shorts = True
                ps_mult      = 0.0
                message      = (f"Bear market — Nifty down {ret_1m:.1f}% this month, RSI {rsi:.0f}. "
                                f"BUY signals BLOCKED. Scanning for SELL/SHORT setups.")

            elif last < ema200 or (ret_1m < -2 and ret_3m < -5):
                regime       = "sideways"
                trend        = "flat"
                allow        = True
                allow_shorts = True
                ps_mult      = 0.5
                message      = ("Sideways/weak market — position sizes halved, "
                                "only high-confidence signals allowed.")
            else:
                regime       = "bull"
                trend        = "up"
                allow        = True
                allow_shorts = False
                ps_mult      = 0.8
                message      = "Mild bull — slightly cautious, 80% position sizes."

            # B1: Apply market breadth adjustment to position_size_multiplier
            try:
                from analysis.market_breadth import MarketBreadthAnalyser
                breadth = MarketBreadthAnalyser().get_breadth()
                ps_mult = round(ps_mult * breadth.position_size_mult, 2)
                ps_mult = max(0.0, min(1.5, ps_mult))
                message += f" Breadth: {breadth.breadth_signal} (A/D {breadth.ad_ratio})."
                logger.info(f"[BREADTH] {breadth.message} — ps_mult adjusted to {ps_mult:.2f}")
            except Exception as _be:
                logger.debug(f"Breadth adjustment skipped: {_be}")

            # Apply hysteresis — only commit regime after 2 consecutive matching scans
            committed_regime, stability_count = self._apply_hysteresis(regime)
            if committed_regime != regime:
                logger.info(f"[REGIME] Hysteresis active: using committed={committed_regime} "
                            f"(raw={regime}, stability={stability_count}/{_STABILITY_REQUIRED})")
                # Revert allow/shorts/ps_mult to committed regime values
                committed_map = {
                    "bull":     (True,  False, min(ps_mult, 1.0)),
                    "recovery": (True,  False, min(ps_mult, 0.7)),
                    "sideways": (True,  True,  min(ps_mult, 0.5)),
                    "bear":     (False, True,  0.0),
                }
                allow, allow_shorts, ps_mult = committed_map.get(committed_regime,
                                                                  (True, False, ps_mult))
                regime = committed_regime

            result = RegimeResult(
                regime                   = regime,
                nifty_trend              = trend,
                nifty_rsi                = round(rsi, 1),
                nifty_above_200ema       = above_200,
                nifty_1m_return          = round(ret_1m, 2),
                nifty_3m_return          = round(ret_3m, 2),
                allow_buys               = allow,
                allow_shorts             = allow_shorts,
                position_size_multiplier = ps_mult,
                message                  = message,
                stability_count          = stability_count,
            )

            logger.info(f"Market regime: {regime.upper()} | "
                        f"Nifty RSI: {rsi:.0f} | "
                        f"1M: {ret_1m:+.1f}% | "
                        f"Above EMA200: {above_200} | "
                        f"Allow buys: {allow}")
            return result

        except Exception as e:
            logger.warning(f"Regime check failed: {e} — assuming bull")
            return self._default_bull()

    def _default_bull(self) -> RegimeResult:
        return RegimeResult(
            regime="bull", nifty_trend="up", nifty_rsi=50.0,
            nifty_above_200ema=True, nifty_1m_return=0.0,
            nifty_3m_return=0.0, allow_buys=True, allow_shorts=False,
            position_size_multiplier=1.0,
            message="Market data unavailable — proceeding with normal operation."
        )
