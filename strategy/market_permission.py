# =============================================================================
# strategy/market_permission.py — Layer 2: Market Permission
#
# Answers: "Does the current market environment permit this trade?"
# Inputs: regime, PCR, FII/DII, sector rotation, breadth, earnings, F&O ban
# Outputs: permission (ALLOW | REDUCE | BLOCK) + reason + reduction_factor
#
# This layer does NOT score the trade. It gates or scales it.
# =============================================================================

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass
from utils import get_logger
from config import REGIME_STABILITY_GATE

logger = get_logger("MarketPermission")


@dataclass
class PermissionResult:
    permission:        str    # ALLOW | REDUCE | BLOCK
    reason:            str
    reduction_factor:  float  # 1.0 = no change, 0.7 = 30% smaller, 0.0 = block
    block_reasons:     list   # list of individual checks that fired


class MarketPermission:
    """
    Layer 2 gating logic.
    Called once per signal after Layer 1 (setup quality) produces a candidate.
    """

    def evaluate(
        self,
        symbol:          str,
        action:          str,
        regime:          str,
        regime_stability: int,
        pcr_signal:      str  = "neutral",     # buy | sell | strong_sell | neutral
        fii_signal:      str  = "neutral",     # buy | sell | strong_sell | neutral
        sector_signal:   str  = "neutral",     # bullish | neutral | bearish
        breadth_signal:  str  = "neutral",     # strong | moderate | weak | very_weak
        earnings_days:   int  = 999,           # days until next earnings
        fno_banned:      bool = False,
        journal=None,                          # DecisionJournal — votes appended if provided
    ) -> PermissionResult:
        """
        Returns PermissionResult.
        Also appends Layer 2 ModuleVote entries to journal if provided.
        """
        blocks    = []
        reductions = []
        reduction_factor = 1.0

        def _vote(module, vote, raw_score, note):
            if journal is not None:
                journal.add_vote(2, module, vote, raw_score=raw_score,
                                 weight=1.0, note=note)

        # ------------------------------------------------------------------
        # BLOCK conditions — stop the trade entirely
        # ------------------------------------------------------------------
        if action == "BUY" and regime == "bear":
            blocks.append("regime_bear_blocks_buy")
            _vote("market_regime", "BLOCK", 0.0, f"regime={regime} blocks new BUYs")

        if action == "BUY" and regime == "sideways":
            blocks.append("regime_sideways_blocks_buy")
            _vote("market_regime", "BLOCK", 0.0,
                  "sideways market — no new BUY positions")

        if earnings_days <= 3:
            blocks.append(f"earnings_in_{earnings_days}d")
            _vote("earnings_guard", "BLOCK", 0.0,
                  f"earnings in {earnings_days} day(s) — event risk")

        if fno_banned:
            blocks.append("fno_ban_active")
            _vote("fno_ban", "BLOCK", 0.0, "symbol on F&O ban list")

        # ------------------------------------------------------------------
        # REDUCE conditions — allow trade at smaller size
        # ------------------------------------------------------------------
        # Regime-based reduction
        if regime == "recovery" and action == "BUY":
            reductions.append(("regime_recovery", 0.80))
            _vote("market_regime", "REDUCE", 0.5,
                  "recovery phase — 80% size. Selective buys only.")

        # FII + PCR double bearish
        fii_bearish = fii_signal in ("sell", "strong_sell")
        pcr_bearish = pcr_signal in ("sell", "strong_sell")
        if fii_bearish and pcr_bearish and action == "BUY":
            reductions.append(("fii_pcr_double_bearish", 0.70))
            _vote("fii_dii", "REDUCE", 0.3,
                  f"FII={fii_signal} + PCR={pcr_signal} both bearish")
        elif fii_bearish and action == "BUY":
            reductions.append(("fii_bearish", 0.85))
            _vote("fii_dii", "REDUCE", 0.4, f"FII net selling: {fii_signal}")

        # Weak breadth
        if breadth_signal == "very_weak" and action == "BUY":
            reductions.append(("breadth_very_weak", 0.75))
            _vote("market_breadth", "REDUCE", 0.3,
                  "advance/decline breadth very weak")
        elif breadth_signal == "weak" and action == "BUY":
            reductions.append(("breadth_weak", 0.85))
            _vote("market_breadth", "REDUCE", 0.45, "breadth weak")

        # Sector bearish
        if sector_signal == "bearish" and action == "BUY":
            reductions.append(("sector_bearish", 0.85))
            _vote("sector_rotation", "REDUCE", 0.4,
                  f"sector rotation bearish")

        # Regime transition uncertainty
        if 0 < regime_stability < REGIME_STABILITY_GATE and action == "BUY":
            reductions.append(("regime_transition", 0.80))
            _vote("market_regime", "REDUCE", 0.5,
                  f"regime transition in progress ({regime_stability}/{REGIME_STABILITY_GATE} scans)")

        # Allow votes when clear
        if not blocks and not reductions:
            _vote("market_permission", "ALLOW", 1.0, "all conditions clear")

        # ------------------------------------------------------------------
        # Combine reductions (multiplicative)
        # ------------------------------------------------------------------
        for _, factor in reductions:
            reduction_factor *= factor
        reduction_factor = round(max(0.0, min(1.0, reduction_factor)), 3)

        if blocks:
            reason = "; ".join(blocks)
            perm   = "BLOCK"
            reduction_factor = 0.0
            logger.info(f"{symbol}: Layer 2 BLOCK — {reason}")
        elif reductions:
            perm   = "REDUCE"
            reason = "; ".join(f"{name}×{f:.2f}" for name, f in reductions)
            logger.debug(f"{symbol}: Layer 2 REDUCE ×{reduction_factor:.2f} — {reason}")
        else:
            perm   = "ALLOW"
            reason = "all clear"

        return PermissionResult(
            permission       = perm,
            reason           = reason,
            reduction_factor = reduction_factor,
            block_reasons    = blocks,
        )
