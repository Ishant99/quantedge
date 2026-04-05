# =============================================================================
# execution/intraday_agent.py — Intraday Signal Generator (15-min charts)
#
# Runs at 9:30, 10:30, 11:30, 12:30, 13:30, 14:30 IST on market days.
# Scans stocks from the daily swing watchlist on 15-min candles.
#
# Strategy (must meet INTRADAY_MIN_CRITERIA of 5):
#   1. EMA9 crosses above EMA21 on 15-min chart
#   2. Price above VWAP (today's session)
#   3. RSI(14) between RSI_LO and RSI_HI (not overbought, has room to run)
#   4. Volume spike >= MIN_VOL_SPIKE × 20-bar average on entry candle
#   5. MACD(12,26,9) bullish crossover on 15-min (fresh, within last bar)
#
# Candidate pool (priority order):
#   1. Symbols from last daily scan's BUY signals (logs/unified_state.json)
#   2. Fallback: top 20 Nifty 50 liquid stocks
#
# Position management:
#   - SL  = low of last 3 candles
#   - TP  = INTRADAY_RR × SL-distance
#   - Position size = INTRADAY_RISK_MULT × normal swing risk
#   - All positions entered via PaperExecutor (treasury-aware, correct schema)
#   - Force-close at 3:15 PM via run_eod_close()
#   - Max INTRADAY_MAX_POSITIONS simultaneously (default 4)
# =============================================================================

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, time as dtime
from dataclasses import dataclass, field
import pytz

from config import (
    VIRTUAL_PORTFOLIO_FILE, VIRTUAL_CAPITAL, RISK_PER_TRADE_PCT,
    INTRADAY_MAX_POSITIONS, INTRADAY_RISK_MULT, INTRADAY_RR,
    INTRADAY_MIN_VOL_SPIKE, INTRADAY_RSI_LO, INTRADAY_RSI_HI,
    INTRADAY_MIN_CRITERIA,
)
from utils import get_logger
from utils.telegram import send

logger = get_logger("IntradayAgent")
IST = pytz.timezone("Asia/Kolkata")

# Fallback candidates when no daily scan data is available
_FALLBACK_CANDIDATES = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "AXISBANK",
    "WIPRO", "TITAN", "LT", "BAJFINANCE", "KOTAKBANK", "ITC", "HINDUNILVR",
    "ASIANPAINT", "MARUTI", "SUNPHARMA", "ULTRACEMCO", "NESTLEIND", "ONGC",
]


@dataclass
class IntradaySignal:
    symbol:         str
    action:         str
    entry_price:    float
    stop_loss:      float
    take_profit:    float
    position_size:  int
    capital_at_risk: float
    confidence:     float
    reasoning:      str
    vwap:           float = 0.0
    rsi_15m:        float = 0.0
    macd_hist:      float = 0.0
    criteria_met:   list  = field(default_factory=list)
    trade_type:     str   = "intraday"


class IntradayAgent:
    """
    Intraday scanner — 15-min EMA crossover + VWAP + RSI + volume + MACD.
    Pulls candidates from the last daily scan via unified_state.json.
    Uses PaperExecutor for trade entry (treasury-aware, correct DB schema).
    """

    def __init__(self):
        self._portfolio_value = self._read_portfolio_value()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def scan_and_trade(self, swing_symbols: list = None) -> list:
        now = datetime.now(IST)
        if not (dtime(9, 25) <= now.time() <= dtime(14, 45) and now.weekday() < 5):
            logger.info("IntradayAgent: outside trading window")
            return []

        # Bear regime guard — skip new long entries when market is bearish
        if self._is_bear_regime():
            logger.info("IntradayAgent: bear regime detected — skipping long entries")
            return []

        open_count = self._count_intraday_positions()
        slots = INTRADAY_MAX_POSITIONS - open_count
        if slots <= 0:
            logger.info(f"IntradayAgent: max {INTRADAY_MAX_POSITIONS} positions reached")
            return []

        candidates = (swing_symbols or self._load_candidates())[:30]
        logger.info(f"IntradayAgent: scanning {len(candidates)} candidates, {slots} slot(s) open")

        signals = []
        for sym in candidates:
            if len(signals) >= slots:
                break
            sig = self._analyse(sym, self._portfolio_value)
            if sig:
                entered = self._enter_trade(sig)
                if entered:
                    signals.append(sig)

        if signals:
            self._send_telegram(signals)
            logger.info(f"IntradayAgent: {len(signals)} entries placed")
        else:
            logger.info("IntradayAgent: no setups found this hour")

        return signals

    # ------------------------------------------------------------------
    # Candidate loading — uses daily scan output, falls back gracefully
    # ------------------------------------------------------------------

    def _load_candidates(self) -> list[str]:
        """Pull BUY symbols from last daily scan via unified_state.json."""
        state_file = os.path.join("logs", "unified_state.json")
        try:
            with open(state_file, encoding="utf-8") as f:
                state = json.load(f)
            signals = state.get("signals", [])
            syms = [
                s["symbol"] for s in signals
                if s.get("action") == "BUY" and s.get("market", "nse").lower() == "nse"
            ]
            if syms:
                deduped = list(dict.fromkeys(syms))[:30]
                logger.info(f"IntradayAgent: {len(deduped)} candidates from unified_state")
                return deduped
        except Exception as e:
            logger.debug(f"IntradayAgent: could not load unified_state ({e}), using fallback")
        logger.info(f"IntradayAgent: using fallback {len(_FALLBACK_CANDIDATES)} candidates")
        return list(_FALLBACK_CANDIDATES)

    # ------------------------------------------------------------------
    # Per-stock 15-min analysis
    # ------------------------------------------------------------------

    def _analyse(self, symbol: str, portfolio_value: float) -> IntradaySignal | None:
        try:
            df = yf.Ticker(f"{symbol}.NS").history(
                period="5d", interval="15m", auto_adjust=True
            )
            if df.empty or len(df) < 30:
                return None

            df.columns = [c.lower() for c in df.columns]
            close  = df["close"]
            high   = df["high"]
            low    = df["low"]
            volume = df["volume"]
            last   = float(close.iloc[-1])

            # ----------------------------------------------------------
            # Criterion 1: EMA 9/21 bullish crossover
            # ----------------------------------------------------------
            ema9  = close.ewm(span=9,  adjust=False).mean()
            ema21 = close.ewm(span=21, adjust=False).mean()
            crossover = (
                float(ema9.iloc[-2]) < float(ema21.iloc[-2]) and
                float(ema9.iloc[-1]) > float(ema21.iloc[-1])
            )

            # ----------------------------------------------------------
            # Criterion 2: Price above today's VWAP
            # ----------------------------------------------------------
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

            # ----------------------------------------------------------
            # Criterion 3: RSI(14) in neutral-bullish zone
            # ----------------------------------------------------------
            delta  = close.diff()
            gain   = delta.clip(lower=0).rolling(14).mean()
            loss   = (-delta.clip(upper=0)).rolling(14).mean()
            rsi_s  = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
            rsi    = float(rsi_s.iloc[-1]) if not np.isnan(rsi_s.iloc[-1]) else 50.0
            rsi_ok = INTRADAY_RSI_LO <= rsi <= INTRADAY_RSI_HI

            # ----------------------------------------------------------
            # Criterion 4: Volume spike
            # ----------------------------------------------------------
            vol_avg   = float(volume.rolling(20).mean().iloc[-1])
            vol_now   = float(volume.iloc[-1])
            vol_spike = (vol_now / vol_avg >= INTRADAY_MIN_VOL_SPIKE) if vol_avg > 0 else False

            # ----------------------------------------------------------
            # Criterion 5: MACD(12,26,9) bullish crossover (15-min)
            # ----------------------------------------------------------
            ema12    = close.ewm(span=12, adjust=False).mean()
            ema26    = close.ewm(span=26, adjust=False).mean()
            macd_l   = ema12 - ema26
            macd_sig = macd_l.ewm(span=9, adjust=False).mean()
            hist_now  = float(macd_l.iloc[-1])  - float(macd_sig.iloc[-1])
            hist_prev = float(macd_l.iloc[-2])  - float(macd_sig.iloc[-2])
            macd_bull = hist_now > 0 and hist_prev <= 0  # fresh crossover above zero

            # ----------------------------------------------------------
            # Gate check: must meet MIN_CRITERIA of 5
            # ----------------------------------------------------------
            criteria_labels = [
                ("EMA9/21 cross",   crossover),
                ("above VWAP",      above_vwap),
                (f"RSI {rsi:.0f}",  rsi_ok),
                (f"vol {vol_now/vol_avg:.1f}x", vol_spike),
                ("MACD cross",      macd_bull),
            ]
            met_labels = [label for label, ok in criteria_labels if ok]
            if len(met_labels) < INTRADAY_MIN_CRITERIA:
                return None

            # ----------------------------------------------------------
            # Position sizing
            # ----------------------------------------------------------
            sl_low  = float(low.iloc[-3:].min())
            sl_dist = last - sl_low
            if sl_dist <= 0:
                return None

            tp  = round(last + INTRADAY_RR * sl_dist, 2)
            sl  = round(sl_low, 2)

            risk_per_trade = portfolio_value * RISK_PER_TRADE_PCT * INTRADAY_RISK_MULT
            qty = max(1, int(risk_per_trade / sl_dist))
            cap_at_risk = round(qty * sl_dist, 2)

            confidence = round(0.45 + len(met_labels) * 0.10, 3)   # 0.75 max (5/5)

            return IntradaySignal(
                symbol          = symbol,
                action          = "BUY",
                entry_price     = round(last, 2),
                stop_loss       = sl,
                take_profit     = tp,
                position_size   = qty,
                capital_at_risk = cap_at_risk,
                confidence      = confidence,
                reasoning       = "INTRADAY 15m: " + ", ".join(met_labels),
                vwap            = round(vwap, 2),
                rsi_15m         = round(rsi, 1),
                macd_hist       = round(hist_now, 4),
                criteria_met    = met_labels,
                trade_type      = "intraday",
            )

        except Exception as e:
            logger.debug(f"{symbol} intraday error: {e}")
            return None

    # ------------------------------------------------------------------
    # Trade entry — routes through PaperExecutor for proper tracking
    # ------------------------------------------------------------------

    def _enter_trade(self, sig: IntradaySignal) -> bool:
        try:
            from execution.executor import get_executor
            from strategy.engine import TradeSignal
            executor = get_executor()
            ts = TradeSignal(
                symbol          = f"INTRA:{sig.symbol}",
                action          = "BUY",
                confidence      = sig.confidence,
                entry_price     = sig.entry_price,
                stop_loss       = sig.stop_loss,
                take_profit     = sig.take_profit,
                position_size   = sig.position_size,
                capital_at_risk = sig.capital_at_risk,
                reasoning       = sig.reasoning,
                ta_score        = 0.0,
            )
            result = executor.execute(ts)
            ok = result.get("status") == "filled"
            if ok:
                logger.info(
                    f"INTRADAY BUY {sig.symbol} x{sig.position_size} "
                    f"@ Rs.{sig.entry_price:,.0f}  SL={sig.stop_loss:,.0f}  "
                    f"TP={sig.take_profit:,.0f}"
                )
            else:
                logger.warning(
                    f"INTRADAY {sig.symbol} rejected: {result.get('reason', 'unknown')}"
                )
            return ok
        except Exception as e:
            logger.error(f"IntradayAgent _enter_trade error ({sig.symbol}): {e}")
            return False

    # ------------------------------------------------------------------
    # Position count — check how many intraday slots are occupied
    # ------------------------------------------------------------------

    def _is_bear_regime(self) -> bool:
        """Read latest regime from logs/market_regime.json. Defaults to False (allow buys) if missing."""
        regime_file = os.path.join("logs", "market_regime.json")
        try:
            with open(regime_file, encoding="utf-8") as f:
                data = json.load(f)
            regime = str(data.get("regime", "")).lower()
            return regime == "bear"
        except Exception:
            return False  # if no regime file, don't block

    def _count_intraday_positions(self) -> int:
        try:
            if os.path.exists(VIRTUAL_PORTFOLIO_FILE):
                with open(VIRTUAL_PORTFOLIO_FILE, encoding="utf-8") as f:
                    pf = json.load(f)
                return sum(
                    1 for sym in pf.get("positions", {})
                    if sym.startswith("INTRA:") or pf["positions"][sym].get("trade_type") == "intraday"
                )
        except Exception:
            pass
        return 0

    def _read_portfolio_value(self) -> float:
        try:
            if os.path.exists(VIRTUAL_PORTFOLIO_FILE):
                with open(VIRTUAL_PORTFOLIO_FILE, encoding="utf-8") as f:
                    pf = json.load(f)
                return float(pf.get("cash", VIRTUAL_CAPITAL))
        except Exception:
            pass
        return float(VIRTUAL_CAPITAL)

    # ------------------------------------------------------------------
    # Telegram alert
    # ------------------------------------------------------------------

    def _send_telegram(self, signals: list):
        now_str = datetime.now(IST).strftime("%H:%M IST")
        lines   = [f"*Intraday Signals — {now_str}*", ""]
        for s in signals:
            rr = round(
                (s.take_profit - s.entry_price) / (s.entry_price - s.stop_loss), 1
            ) if s.entry_price > s.stop_loss else 0
            lines += [
                f"*{s.symbol}*  conf {s.confidence:.0%}  ({len(s.criteria_met)}/5 criteria)",
                f"Entry Rs.{s.entry_price:,.0f} | SL Rs.{s.stop_loss:,.0f} | "
                f"TP Rs.{s.take_profit:,.0f} | R:R {rr}x",
                f"VWAP Rs.{s.vwap:,.0f}  RSI {s.rsi_15m:.0f}  MACD hist {s.macd_hist:+.4f}",
                f"_{', '.join(s.criteria_met)}_",
                "",
            ]
        lines.append("_All positions closed at 15:25 IST_")
        send("\n".join(lines))
