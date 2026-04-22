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
from dataclasses import dataclass
from config import RISK_PER_TRADE_PCT, REWARD_RISK_RATIO
from utils import get_logger

logger = get_logger("DynamicSizing")

_TRADES_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "trades.db")


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

        # Combined multiplier
        combined = 1.0
        for m in multipliers.values():
            combined *= m

        # Cap combined multiplier
        combined = max(0.3, min(1.5, combined))

        adjusted_risk = base_risk * combined

        # Calculate stop loss and position size
        sl_distance = max(1.5 * atr, entry_price * 0.02)
        stop_loss   = round(entry_price - sl_distance, 2)
        take_profit = round(entry_price + (REWARD_RISK_RATIO * sl_distance), 2)

        risk_amount  = portfolio_value * adjusted_risk
        position_size= int(risk_amount / sl_distance) if sl_distance > 0 else 0
        capital_risk = round(position_size * sl_distance, 2)

        reasoning = (
            f"Conf {confidence:.0%} → base {base_risk*100:.1f}% risk × "
            f"combined {combined:.2f} multiplier = "
            f"{adjusted_risk*100:.1f}% → {position_size} shares"
        )

        logger.info(f"{symbol}: {reasoning}")

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
