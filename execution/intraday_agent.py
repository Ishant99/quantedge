# =============================================================================
# execution/intraday_agent.py — Intraday Trading Module
#
# Separate from swing agent. Uses tighter parameters:
#   - 5-min chart confirmation (instead of daily)
#   - Smaller position size (10% of swing allocation)
#   - Tighter stop loss (1× ATR instead of 1.5×)
#   - Must close by 3:25 PM (no overnight holds)
#   - Only trades when market regime is BULL
#   - Max 2 intraday positions at once
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass
from config import VIRTUAL_PORTFOLIO_FILE, VIRTUAL_CAPITAL
from utils import get_logger
from utils.telegram import send

logger = get_logger("IntradayAgent")

MAX_INTRADAY_POSITIONS = 2
INTRADAY_RISK_PCT      = 0.005   # 0.5% risk per intraday trade (vs 2% swing)
MIN_INTRADAY_TA_SCORE  = 7.0     # stricter than swing (6.0)


@dataclass
class IntradaySignal:
    symbol:         str
    action:         str      # BUY | SKIP
    entry_price:    float
    stop_loss:      float
    take_profit:    float
    position_size:  int
    capital_at_risk:float
    confidence:     float
    reasoning:      str
    trade_type:     str = "intraday"


class IntradayAgent:
    """
    Intraday trading module — buys on morning strength,
    closes all positions by end of day.
    Allocation: 20% of portfolio (separate from 80% swing).
    """

    INTRADAY_ALLOCATION = 0.20   # 20% of portfolio for intraday

    def __init__(self):
        self.portfolio = self._load_portfolio()

    def scan_and_trade(self, swing_symbols: list[str] = None) -> list[IntradaySignal]:
        """
        Run intraday scan. Uses same stock universe as swing
        but with tighter criteria and 5-min data.

        Args:
            swing_symbols: Symbols already analysed by swing agent.
                          Intraday only trades stocks the swing agent likes too.
        """
        if self._count_intraday_positions() >= MAX_INTRADAY_POSITIONS:
            logger.info(f"Max intraday positions ({MAX_INTRADAY_POSITIONS}) reached")
            return []

        # If no swing symbols provided, use default watchlist
        if not swing_symbols:
            swing_symbols = ["RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK",
                             "AXISBANK","SBIN","KOTAKBANK","BRITANNIA","TITAN"]

        # Only take intraday on stocks swing agent also likes
        signals = []
        slots   = MAX_INTRADAY_POSITIONS - self._count_intraday_positions()

        for sym in swing_symbols[:15]:   # check top 15 swing candidates
            if slots <= 0:
                break
            sig = self._analyse_intraday(sym)
            if sig and sig.action == "BUY":
                signals.append(sig)
                slots -= 1

        # Execute
        for sig in signals:
            self._enter_trade(sig)

        return signals

    def _analyse_intraday(self, symbol: str) -> IntradaySignal | None:
        """
        Analyse a stock for intraday entry using 5-minute data.
        Criteria:
          1. Opening range breakout — price breaks above first 15-min high
          2. Volume surge (2× average)
          3. RSI on 5-min chart > 55
          4. Price above VWAP
        """
        try:
            # Fetch today's 5-min data
            ticker = yf.Ticker(f"{symbol}.NS")
            df5    = ticker.history(period="1d", interval="5m", auto_adjust=True)

            if df5.empty or len(df5) < 10:
                return None

            df5.columns = [c.lower() for c in df5.columns]
            close  = df5["close"]
            high   = df5["high"]
            low    = df5["low"]
            volume = df5["volume"]
            last   = float(close.iloc[-1])

            # Opening range (first 15 mins = first 3 × 5-min bars)
            opening_high = float(high.iloc[:3].max())
            opening_low  = float(low.iloc[:3].min())

            # ORB breakout: price breaks above opening range high
            orb_breakout = last > opening_high * 1.002   # 0.2% above

            # Volume surge
            avg_vol  = float(volume.mean())
            curr_vol = float(volume.iloc[-1])
            vol_surge= curr_vol > avg_vol * 1.8

            # RSI on 5-min
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rsi   = float((100 - 100/(1 + gain/loss.replace(0, np.nan))).iloc[-1])
            rsi_ok= rsi > 55

            # VWAP
            vwap  = float((df5["close"] * df5["volume"]).cumsum() /
                          df5["volume"].cumsum()).iloc[-1]
            above_vwap = last > vwap

            # Score
            score = sum([orb_breakout, vol_surge, rsi_ok, above_vwap])
            if score < 3:
                return None   # need at least 3/4 criteria

            # Position sizing
            portfolio_value = self.portfolio.get("cash", VIRTUAL_CAPITAL)
            intraday_capital= portfolio_value * self.INTRADAY_ALLOCATION
            atr  = self._atr_5m(df5)
            sl   = round(last - atr, 2)           # tighter: 1× ATR
            tp   = round(last + (1.5 * atr), 2)   # 1.5:1 R/R for intraday
            sl_d = last - sl
            risk = intraday_capital * INTRADAY_RISK_PCT
            qty  = int(risk / sl_d) if sl_d > 0 else 0

            if qty <= 0:
                return None

            conf = 0.5 + score * 0.12   # 0.74 max

            reasons = []
            if orb_breakout: reasons.append("ORB breakout")
            if vol_surge:    reasons.append(f"Volume surge {curr_vol/avg_vol:.1f}×")
            if rsi_ok:       reasons.append(f"RSI {rsi:.0f}")
            if above_vwap:   reasons.append("Above VWAP")

            return IntradaySignal(
                symbol         = symbol,
                action         = "BUY",
                entry_price    = round(last, 2),
                stop_loss      = sl,
                take_profit    = tp,
                position_size  = qty,
                capital_at_risk= round(qty * sl_d, 2),
                confidence     = round(conf, 2),
                reasoning      = "Intraday: " + " + ".join(reasons),
                trade_type     = "intraday",
            )

        except Exception as e:
            logger.debug(f"Intraday analysis failed for {symbol}: {e}")
            return None

    def _enter_trade(self, sig: IntradaySignal):
        """Enter an intraday paper trade."""
        cost = sig.entry_price * sig.position_size
        if cost > self.portfolio.get("cash", 0):
            logger.warning(f"Insufficient cash for intraday {sig.symbol}")
            return

        self.portfolio["cash"] -= cost
        self.portfolio.setdefault("positions", {})[sig.symbol] = {
            "qty":        sig.position_size,
            "entry":      sig.entry_price,
            "stop_loss":  sig.stop_loss,
            "take_profit":sig.take_profit,
            "trade_type": "intraday",
            "timestamp":  datetime.now().isoformat(),
        }
        self._save_portfolio()

        logger.info(f"INTRADAY BUY {sig.symbol} × {sig.position_size} "
                    f"@ Rs.{sig.entry_price:,.2f} | "
                    f"SL: Rs.{sig.stop_loss:,.2f} | TP: Rs.{sig.take_profit:,.2f}")

        send(f"📈 *Intraday Entry*\n"
             f"Stock: `{sig.symbol}`\n"
             f"Entry: `Rs.{sig.entry_price:,.2f}`\n"
             f"SL: `Rs.{sig.stop_loss:,.2f}` | TP: `Rs.{sig.take_profit:,.2f}`\n"
             f"Qty: `{sig.position_size}` | Risk: `Rs.{sig.capital_at_risk:,.0f}`\n"
             f"Reason: _{sig.reasoning}_")

    def _count_intraday_positions(self) -> int:
        positions = self.portfolio.get("positions", {})
        return sum(1 for p in positions.values() if p.get("trade_type") == "intraday")

    def _atr_5m(self, df: pd.DataFrame, period: int = 14) -> float:
        hi, lo, cl = df["high"], df["low"], df["close"]
        tr = pd.concat([hi-lo, (hi-cl.shift()).abs(), (lo-cl.shift()).abs()], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    def _load_portfolio(self) -> dict:
        os.makedirs("logs", exist_ok=True)
        if os.path.exists(VIRTUAL_PORTFOLIO_FILE):
            with open(VIRTUAL_PORTFOLIO_FILE) as f:
                return json.load(f)
        return {"cash": VIRTUAL_CAPITAL, "positions": {}}

    def _save_portfolio(self):
        with open(VIRTUAL_PORTFOLIO_FILE, "w") as f:
            json.dump(self.portfolio, f, indent=2)
