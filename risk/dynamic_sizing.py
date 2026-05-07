# =============================================================================
# risk/dynamic_sizing.py — Dynamic Position Sizing
#
# Sizes positions based on signal conviction level.
# High confidence (>80%) = full 2% risk
# Medium confidence (60-80%) = 1.5% risk
# Low confidence (<60%) = 1% risk
#
# Also applies sector rotation and pattern multipliers.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import yfinance as yf
from dataclasses import dataclass
from config import RISK_PER_TRADE_PCT, REWARD_RISK_RATIO, VIX_HIGH_THRESHOLD, VIX_EXTREME_THRESHOLD, MAX_POSITION_VALUE_PCT
from utils import get_logger

logger = get_logger("DynamicSizing")

_TRADES_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "trades.db")

_vix_cache: dict = {}


def _get_india_vix() -> float:
    """Fetch India VIX. Cached for 15 minutes — reduces stale values during intraday moves."""
    import time
    if _vix_cache.get("ts", 0) and time.time() - _vix_cache["ts"] < 900:
        return _vix_cache["value"]
    try:
        hist = yf.Ticker("^INDIAVIX").history(period="2d", interval="1d")
        vix  = float(hist["Close"].iloc[-1]) if not hist.empty else 15.0
        _vix_cache["value"] = vix
        _vix_cache["ts"]    = time.time()
        logger.debug(f"India VIX: {vix:.1f}")
        return vix
    except Exception:
        return 15.0  # neutral default


def _vix_multiplier() -> float:
    """B3: Scale position sizes down when volatility is high."""
    vix = _get_india_vix()
    if vix >= VIX_EXTREME_THRESHOLD:
        mult = 0.5
    elif vix >= VIX_HIGH_THRESHOLD:
        mult = 0.75
    else:
        mult = 1.0
    logger.debug(f"VIX multiplier: {mult:.2f} (VIX={vix:.1f})")
    return mult


def _kelly_multiplier(setup_type: str) -> float:
    """Half-Kelly fraction based on historical win rate for this setup_type."""
    try:
        conn = sqlite3.connect(_TRADES_DB)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*), SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) "
            "FROM trades WHERE setup_type = ? AND status = 'closed'",
            (setup_type,),
        )
        row = cur.fetchone()
        conn.close()
        total, wins = (row[0] or 0), (row[1] or 0)
        if total < 10:
            return 1.0  # insufficient history — no adjustment
        win_rate = wins / total
        loss_rate = 1 - win_rate
        avg_rr = REWARD_RISK_RATIO
        kelly = (win_rate * avg_rr - loss_rate) / avg_rr
        half_kelly = max(0.3, min(1.5, kelly * 0.5))
        logger.debug(f"Kelly({setup_type}): win={win_rate:.0%} n={total} → half-Kelly={half_kelly:.2f}")
        return round(half_kelly, 2)
    except Exception:
        return 1.0


@dataclass
class SizingResult:
    symbol:          str
    base_risk_pct:   float    # base risk percentage
    adjusted_risk_pct: float  # after all multipliers
    position_size:   int      # final number of shares
    capital_at_risk: float    # Rs. amount at risk
    stop_loss:       float
    take_profit:     float
    multipliers:     dict     # breakdown of each multiplier applied
    reasoning:       str


class DynamicPositionSizer:
    """
    Adjusts position size based on signal quality.
    Better signals = larger positions. Weaker signals = smaller bets.
    """

    def calculate(
        self,
        symbol:           str,
        confidence:       float,
        entry_price:      float,
        atr:              float,
        portfolio_value:  float,
        pattern_bias:     str  = "neutral",
        sr_near_support:  bool = False,
        sector_multiplier:float= 1.0,
        regime_multiplier:float= 1.0,
        fii_score:        float= 5.0,
        setup_type:       str  = "",
        sentiment_modifier: float = 0.0,   # Layer 3 only: ±0.10 additive on position_size_pct
        journal=None,                      # DecisionJournal — votes appended if provided
    ) -> SizingResult:
        """
        Calculate position size with all factors applied.

        Args:
            confidence:        Signal confidence 0-1
            entry_price:       Entry price
            atr:               Average True Range (for stop distance)
            portfolio_value:   Current portfolio value
            pattern_bias:      bullish | neutral | bearish (from pattern engine)
            sr_near_support:   True if price near key support
            sector_multiplier: Hot sector = 1.2, cold = 0.7
            regime_multiplier: Bull = 1.0, sideways = 0.5
            fii_score:         FII signal score 0-10
        """
        multipliers = {}

        # Base risk from confidence
        if confidence >= 0.80:
            base_risk = RISK_PER_TRADE_PCT          # full 2%
            multipliers["confidence"] = 1.0
        elif confidence >= 0.65:
            base_risk = RISK_PER_TRADE_PCT * 0.75   # 1.5%
            multipliers["confidence"] = 0.75
        else:
            base_risk = RISK_PER_TRADE_PCT * 0.5    # 1%
            multipliers["confidence"] = 0.5

        # Pattern multiplier
        if pattern_bias == "bullish":
            multipliers["pattern"] = 1.15
        elif pattern_bias == "bearish":
            multipliers["pattern"] = 0.7
        else:
            multipliers["pattern"] = 1.0

        # Support multiplier
        if sr_near_support:
            multipliers["near_support"] = 1.1
        else:
            multipliers["near_support"] = 1.0

        # Sector multiplier
        multipliers["sector"] = sector_multiplier

        # Market regime multiplier
        multipliers["regime"] = regime_multiplier

        # FII multiplier (scale 0-10 score to 0.8-1.2 multiplier)
        fii_mult = 0.8 + (fii_score / 10) * 0.4
        multipliers["fii"] = round(fii_mult, 2)

        # Kelly Criterion multiplier from historical win rate per setup type
        if setup_type:
            multipliers["kelly"] = _kelly_multiplier(setup_type)

        # B3: India VIX multiplier — reduce size in high-volatility markets
        multipliers["vix"] = _vix_multiplier()

        # Combined multiplier
        combined = 1.0
        for m in multipliers.values():
            combined *= m

        # Cap combined multiplier
        combined = max(0.3, min(1.5, combined))

        adjusted_risk = base_risk * combined

        # Calculate stop loss and position size
        # Widen SL proportionally with VIX (linear interpolation, no hard jump)
        vix      = _get_india_vix()
        atr_mult = 1.5 + max(0.0, (vix - 15) / 15) * 0.5   # 1.5 at VIX≤15, 2.0 at VIX=30
        sl_distance = max(atr_mult * atr, entry_price * 0.02)
        stop_loss   = round(entry_price - sl_distance, 2)
        take_profit = round(entry_price + (REWARD_RISK_RATIO * sl_distance), 2)

        risk_amount  = portfolio_value * adjusted_risk
        position_size= int(risk_amount / sl_distance) if sl_distance > 0 else 0

        # Hard cap: no single position > MAX_POSITION_VALUE_PCT of portfolio
        if entry_price > 0:
            max_shares = int((portfolio_value * MAX_POSITION_VALUE_PCT) / entry_price)
            if position_size > max_shares:
                logger.debug(f"{symbol}: size capped {position_size}→{max_shares} (20% portfolio cap)")
                position_size = max_shares

        capital_risk = round(position_size * sl_distance, 2)

        # Apply sentiment modifier: additive ±10% on position size (Layer 3 only)
        sentiment_modifier = max(-0.10, min(0.10, sentiment_modifier))
        if sentiment_modifier != 0.0:
            position_size = max(0, int(position_size * (1.0 + sentiment_modifier)))
            multipliers["sentiment_modifier"] = round(1.0 + sentiment_modifier, 3)

        capital_risk = round(position_size * sl_distance, 2)

        reasoning = (
            f"Conf {confidence:.0%} → base {base_risk*100:.1f}% risk × "
            f"combined {combined:.2f} multiplier = "
            f"{adjusted_risk*100:.1f}% → {position_size} shares"
        )

        logger.info(f"{symbol}: {reasoning}")

        # Append Layer 3 journal votes
        if journal is not None:
            for name, val in multipliers.items():
                vote = "BUY" if val >= 1.0 else "REDUCE"
                journal.add_vote(3, name, vote, raw_score=val, weight=val,
                                 note=f"{name}={val:.2f}")
            # Populate sizing_rationale
            journal.sizing_rationale.base_risk_pct     = round(base_risk * 100, 2)
            journal.sizing_rationale.confidence_mult   = multipliers.get("confidence", 1.0)
            journal.sizing_rationale.kelly_mult        = multipliers.get("kelly", 1.0)
            journal.sizing_rationale.vix_mult          = multipliers.get("vix", 1.0)
            journal.sizing_rationale.regime_mult       = multipliers.get("regime", 1.0)
            journal.sizing_rationale.pattern_mult      = multipliers.get("pattern", 1.0)
            journal.sizing_rationale.sector_mult       = multipliers.get("sector", 1.0)
            journal.sizing_rationale.fii_mult          = multipliers.get("fii", 1.0)
            journal.sizing_rationale.sentiment_modifier = sentiment_modifier
            journal.sizing_rationale.combined_mult     = combined
            journal.sizing_rationale.final_risk_pct    = round(adjusted_risk * 100, 2)
            journal.sizing_rationale.final_size        = max(0, position_size)

        return SizingResult(
            symbol            = symbol,
            base_risk_pct     = round(base_risk * 100, 2),
            adjusted_risk_pct = round(adjusted_risk * 100, 2),
            position_size     = max(0, position_size),
            capital_at_risk   = capital_risk,
            stop_loss         = stop_loss,
            take_profit       = take_profit,
            multipliers       = {k: round(v, 2) for k, v in multipliers.items()},
            reasoning         = reasoning,
        )
