# =============================================================================
# execution/intraday_agent.py — Intraday Signal Generator (15-min charts)
#
# Runs at 9:30, 10:30, 11:30, 12:30, 13:30, 14:30 IST on market days.
# Scans stocks from the daily swing watchlist on 15-min candles.
#
# Strategy (must meet 3 of 4):
#   1. EMA9 crosses above EMA21 on 15-min chart
#   2. Price above VWAP (session)
#   3. RSI(14) between 40-65 (not overbought, has room to run)
#   4. Volume spike >= 1.5x 20-bar average on entry candle
#
# Position management:
#   - SL  = low of last 3 candles
#   - TP  = 1.5 x SL-distance (1.5 R:R intraday)
#   - Position size = 50% of normal swing size (0.5% risk per trade)
#   - Force-close at 3:15 PM (EOD) via price_monitor.close_all_intraday()
#   - Max 2 intraday positions simultaneously
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, time as dtime
from dataclasses import dataclass
import pytz

from config import VIRTUAL_PORTFOLIO_FILE, VIRTUAL_CAPITAL, RISK_PER_TRADE_PCT
from utils import get_logger
from utils.telegram import send

logger = get_logger("IntradayAgent")
IST = pytz.timezone("Asia/Kolkata")

MAX_INTRADAY_POSITIONS = 2
INTRADAY_RISK_MULT     = 0.50   # 50% of normal swing risk per trade
INTRADAY_RR            = 1.5    # reward:risk ratio
MIN_VOL_SPIKE          = 1.5    # current bar volume vs 20-bar avg
INTRADAY_RSI_LO        = 40
INTRADAY_RSI_HI        = 65
MIN_CRITERIA           = 3      # must meet N out of 4 criteria


@dataclass
class IntradaySignal:
    symbol:         str
    action:         str          # BUY
    entry_price:    float
    stop_loss:      float
    take_profit:    float
    position_size:  int
    capital_at_risk:float
    confidence:     float
    reasoning:      str
    vwap:           float = 0.0
    rsi_15m:        float = 0.0
    trade_type:     str   = "intraday"


class IntradayAgent:
    """
    Intraday scanner — 15-min EMA crossover + VWAP + RSI + volume.
    Only trades stocks already passing the daily momentum filter.
    Force-closes all positions at 3:15 PM via EOD close job.
    """

    def __init__(self):
        self.portfolio = self._load_portfolio()

    def scan_and_trade(self, swing_symbols: list = None) -> list:
        """
        Run intraday scan. Returns list of IntradaySignal.
        swing_symbols = stocks already vetted by daily pipeline.
        """
        now = datetime.now(IST)
        if not (dtime(9, 25) <= now.time() <= dtime(14, 45) and now.weekday() < 5):
            logger.info("IntradayAgent: outside trading window")
            return []

        open_intraday = self._count_intraday_positions()
        slots = MAX_INTRADAY_POSITIONS - open_intraday
        if slots <= 0:
            logger.info(f"IntradayAgent: max {MAX_INTRADAY_POSITIONS} positions reached")
            return []

        candidates = (swing_symbols or [
            "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY",
            "SBIN", "AXISBANK", "WIPRO", "TITAN", "LT",
        ])[:20]

        portfolio_value = self.portfolio.get("cash", VIRTUAL_CAPITAL)
        signals = []

        for sym in candidates:
            if len(signals) >= slots:
                break
            sig = self._analyse(sym, portfolio_value)
            if sig:
                signals.append(sig)
                self._enter_trade(sig)

        if signals:
            self._send_telegram(signals)
            logger.info(f"IntradayAgent: {len(signals)} entries placed")

        return signals

    # ------------------------------------------------------------------
    # Per-stock analysis
    # ------------------------------------------------------------------

    def _analyse(self, symbol: str, portfolio_value: float) -> IntradaySignal | None:
        try:
            df = yf.Ticker(f"{symbol}.NS").history(
                period="5d", interval="15m", auto_adjust=True
            )
            if df.empty or len(df) < 25:
                return None

            df.columns = [c.lower() for c in df.columns]
            close  = df["close"]
            high   = df["high"]
            low    = df["low"]
            volume = df["volume"]
            last   = float(close.iloc[-1])

            # EMA 9 / 21
            ema9       = close.ewm(span=9,  adjust=False).mean()
            ema21      = close.ewm(span=21, adjust=False).mean()
            crossover  = (float(ema9.iloc[-2]) < float(ema21.iloc[-2]) and
                          float(ema9.iloc[-1]) > float(ema21.iloc[-1]))

            # VWAP (today's session only)
            try:
                idx_tz = df.index.tz_convert(IST)
                today  = datetime.now(IST).date()
                mask   = idx_tz.date == today
                td     = df[mask] if mask.any() else df.tail(26)
            except Exception:
                td = df.tail(26)

            denom = td["volume"].sum()
            vwap  = float(
                ((td["high"] + td["low"] + td["close"]) / 3 * td["volume"]).sum() / denom
            ) if denom > 0 else last
            above_vwap = last > vwap

            # RSI(14) on 15-min
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rsi   = float(100 - 100 / (1 + gain / loss.replace(0, np.nan)).iloc[-1])
            rsi_ok = INTRADAY_RSI_LO <= rsi <= INTRADAY_RSI_HI

            # Volume spike
            vol_avg = float(volume.rolling(20).mean().iloc[-1])
            vol_now = float(volume.iloc[-1])
            vol_spike = (vol_now / vol_avg >= MIN_VOL_SPIKE) if vol_avg > 0 else False

            # Count how many criteria met
            criteria  = [crossover, above_vwap, rsi_ok, vol_spike]
            met       = sum(criteria)
            if met < MIN_CRITERIA:
                return None

            # SL = low of last 3 candles
            sl_low  = float(low.iloc[-3:].min())
            sl_dist = last - sl_low
            if sl_dist <= 0:
                return None

            tp  = round(last + INTRADAY_RR * sl_dist, 2)
            sl  = round(sl_low, 2)

            risk_per_trade = portfolio_value * RISK_PER_TRADE_PCT * INTRADAY_RISK_MULT
            qty = max(1, int(risk_per_trade / sl_dist))
            cap_at_risk = round(qty * sl_dist, 2)

            confidence = 0.50 + met * 0.10   # 0.80 max (4/4)

            reasons = []
            if crossover:  reasons.append("EMA9/21 crossover")
            if above_vwap: reasons.append(f"above VWAP {vwap:,.0f}")
            if rsi_ok:     reasons.append(f"RSI {rsi:.0f}")
            if vol_spike:  reasons.append(f"vol {vol_now/vol_avg:.1f}x spike")

            return IntradaySignal(
                symbol          = symbol,
                action          = "BUY",
                entry_price     = round(last, 2),
                stop_loss       = sl,
                take_profit     = tp,
                position_size   = qty,
                capital_at_risk = cap_at_risk,
                confidence      = round(confidence, 3),
                reasoning       = "INTRADAY 15m: " + ", ".join(reasons),
                vwap            = round(vwap, 2),
                rsi_15m         = round(rsi, 1),
                trade_type      = "intraday",
            )

        except Exception as e:
            logger.debug(f"{symbol} intraday error: {e}")
            return None

    # ------------------------------------------------------------------
    # Trade management
    # ------------------------------------------------------------------

    def _enter_trade(self, sig: IntradaySignal):
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
        logger.info(f"INTRADAY BUY {sig.symbol} x{sig.position_size} "
                    f"@ Rs.{sig.entry_price:,.0f} SL={sig.stop_loss:,.0f} TP={sig.take_profit:,.0f}")

    def _count_intraday_positions(self) -> int:
        return sum(
            1 for p in self.portfolio.get("positions", {}).values()
            if p.get("trade_type") == "intraday"
        )

    # ------------------------------------------------------------------
    # Telegram
    # ------------------------------------------------------------------

    def _send_telegram(self, signals: list):
        now_str = datetime.now(IST).strftime("%H:%M IST")
        lines   = [f"*Intraday Signals — {now_str}*", ""]
        for s in signals:
            rr = round((s.take_profit - s.entry_price) /
                       (s.entry_price - s.stop_loss), 1) if s.entry_price > s.stop_loss else 0
            lines += [
                f"*{s.symbol}*  conf {s.confidence:.0%}",
                f"Entry Rs.{s.entry_price:,.0f} | SL Rs.{s.stop_loss:,.0f} | "
                f"TP Rs.{s.take_profit:,.0f} | R:R {rr}x",
                f"VWAP Rs.{s.vwap:,.0f}  RSI {s.rsi_15m:.0f}",
                f"_{s.reasoning}_",
                "",
            ]
        lines.append("_All positions closed at 15:15 IST_")
        send("\n".join(lines))

    # ------------------------------------------------------------------
    # Portfolio helpers
    # ------------------------------------------------------------------

    def _load_portfolio(self) -> dict:
        os.makedirs("logs", exist_ok=True)
        if os.path.exists(VIRTUAL_PORTFOLIO_FILE):
            with open(VIRTUAL_PORTFOLIO_FILE) as f:
                return json.load(f)
        return {"cash": VIRTUAL_CAPITAL, "positions": {}}

    def _save_portfolio(self):
        with open(VIRTUAL_PORTFOLIO_FILE, "w") as f:
            json.dump(self.portfolio, f, indent=2)
