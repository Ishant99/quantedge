# =============================================================================
# strategy/execution_planner.py — Candidate ranking + slot allocation
#
# Ranks BUY signals by expected value, then allocates portfolio slots while
# respecting sector diversity and inter-symbol correlation constraints.
#
# Ranking formula:
#   rank_score = (expected_value × p_direction) / max(execution_risk + 0.01, 0.01)
#
# Adjustments applied before final ranking:
#   • Sector diversity  — 30 % penalty for same-sector duplication
#   • Correlation cost  — up to 50 % penalty for highly correlated pairs
#   • Portfolio heat    — scale down when >60 % capital is already deployed
# =============================================================================

from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import get_logger

logger = get_logger("ExecutionPlanner")

# Penalty / heat constants
_SAME_SECTOR_PENALTY    = 0.30   # 30 % penalty for same-sector duplication
_MAX_CORRELATION_COST   = 0.50   # max correlation penalty
_HEAT_THRESHOLD         = 0.60   # deployed-capital fraction that triggers heat reduction
_HEAT_REDUCTION_FACTOR  = 0.80   # multiply rank_score by this when portfolio is hot
_MIN_CORRELATION_WINDOW = 20     # minimum data points needed for correlation calculation


@dataclass
class RankedCandidate:
    """A BUY signal that has been scored and (possibly) allocated a slot."""

    signal:           Any    # TradeSignal from strategy.engine
    rank_score:       float  # composite ranking score (higher = better)
    slot_allocated:   bool   # True if this candidate was given a live slot
    rejection_reason: str = ""


class ExecutionPlanner:
    """
    Candidate competition: ranks BUY signals by a composite score,
    then allocates available portfolio slots.

    Ranking formula:
        rank_score = (expected_value × p_direction) / max(execution_risk + 0.01, 0.01)

    Adjustments:
        • Sector diversity  — penalise if a slot already holds the same sector.
        • Correlation cost  — penalise highly correlated pairs (uses market_data).
        • Portfolio heat    — reduce allocation when >60 % capital is deployed.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rank_and_allocate(
        self,
        signals: list,                          # list[TradeSignal]
        max_slots: int,
        open_position_sectors: Optional[set] = None,
        market_data: Optional[dict] = None,     # {symbol: pd.DataFrame}
        portfolio_deployed_pct: float = 0.0,
    ) -> list[RankedCandidate]:
        """
        Score each BUY signal, apply diversity / correlation / heat adjustments,
        sort descending, and allocate up to *max_slots* slots.

        Args:
            signals:                BUY-action TradeSignal objects to compete.
            max_slots:              Maximum number of new positions to open.
            open_position_sectors:  Sectors already represented in the portfolio.
            market_data:            OHLCV DataFrames keyed by symbol for
                                    correlation computation.
            portfolio_deployed_pct: Fraction of total capital currently deployed
                                    (0.0–1.0).

        Returns:
            List of RankedCandidate, sorted by rank_score descending.
            All candidates are included; only the top *max_slots* will have
            slot_allocated=True.
        """
        if open_position_sectors is None:
            open_position_sectors = set()
        if market_data is None:
            market_data = {}

        hot_portfolio = portfolio_deployed_pct > _HEAT_THRESHOLD

        # ---- Step 1: compute base rank scores --------------------------------
        scored: list[tuple[float, Any]] = []
        for sig in signals:
            if sig.action != "BUY":
                continue
            base = self._compute_rank_score(sig)
            if hot_portfolio:
                base *= _HEAT_REDUCTION_FACTOR
            scored.append((base, sig))

        # Sort by base score descending before diversity/correlation pass
        scored.sort(key=lambda x: x[0], reverse=True)

        # ---- Step 2: greedy allocation with diversity / correlation ----------
        allocated_sectors: set[str] = set(open_position_sectors)
        allocated_symbols: list[str] = []
        results: list[RankedCandidate] = []
        slots_used = 0

        for base_score, sig in scored:
            symbol = getattr(sig, "symbol", "")
            sector = getattr(sig, "regime_tag", "") or ""  # regime_tag used as sector tag
            # Prefer a dedicated sector field if it exists
            if hasattr(sig, "sector"):
                sector = sig.sector or sector

            # Apply sector and correlation adjustments
            sec_pen  = self._sector_penalty(sector, allocated_sectors)
            corr_pen = self._correlation_cost(symbol, allocated_symbols, market_data)
            adj_score = round(base_score * (1.0 - sec_pen) * (1.0 - corr_pen), 4)

            if slots_used < max_slots:
                results.append(RankedCandidate(
                    signal=sig,
                    rank_score=adj_score,
                    slot_allocated=True,
                ))
                allocated_sectors.add(sector)
                allocated_symbols.append(symbol)
                slots_used += 1
                logger.debug(
                    f"Allocated slot {slots_used}/{max_slots}: {symbol} "
                    f"rank={adj_score:.4f} sec_pen={sec_pen:.2f} corr_pen={corr_pen:.2f}"
                )
            else:
                rejection = "No slots available"
                if sec_pen > 0:
                    rejection = f"Sector duplicate penalty {sec_pen:.0%} — no slots"
                results.append(RankedCandidate(
                    signal=sig,
                    rank_score=adj_score,
                    slot_allocated=False,
                    rejection_reason=rejection,
                ))

        # Re-sort the final list by adjusted rank_score so callers see best first
        results.sort(key=lambda r: r.rank_score, reverse=True)

        n_alloc = sum(1 for r in results if r.slot_allocated)
        logger.info(
            f"ExecutionPlanner: {len(results)} candidates scored, "
            f"{n_alloc} slots allocated (max={max_slots})"
        )
        return results

    # ------------------------------------------------------------------
    # Score computation
    # ------------------------------------------------------------------

    def _compute_rank_score(self, signal) -> float:
        """
        rank_score = (EV × p_direction) / (exec_risk + 0.01)
        Result is bounded to [0, 10].

        Fields read (all default safely to 0.0 if absent):
            expected_value, p_direction, execution_risk
        """
        ev         = max(0.0, float(getattr(signal, "expected_value",  0.0)))
        p_dir      = max(0.0, float(getattr(signal, "p_direction",     0.0)))
        exec_risk  = max(0.0, float(getattr(signal, "execution_risk",  0.0)))

        raw = (ev * p_dir) / max(exec_risk + 0.01, 0.01)
        return round(min(raw, 10.0), 4)

    # ------------------------------------------------------------------
    # Adjustment helpers
    # ------------------------------------------------------------------

    def _sector_penalty(self, sector: str, allocated_sectors: set) -> float:
        """
        Return a 0.0–0.5 penalty if the sector is already represented.

        A 30 % penalty is applied on the first duplication; no additional
        stacking beyond that (sector is a binary membership check).
        """
        if not sector:
            return 0.0
        if sector in allocated_sectors:
            return _SAME_SECTOR_PENALTY
        return 0.0

    def _correlation_cost(
        self,
        symbol: str,
        allocated_symbols: list[str],
        market_data: dict,
    ) -> float:
        """
        Compute average Pearson correlation of *symbol*'s close returns against
        every already-allocated symbol's close returns.

        Returns a cost in [0.0, 0.5] — higher means more correlated (less
        desirable because it adds portfolio risk without diversification).

        Falls back to 0.0 gracefully if data is missing or insufficient.
        """
        if not allocated_symbols or symbol not in market_data:
            return 0.0

        df_sym = market_data[symbol]
        if "close" not in df_sym.columns or len(df_sym) < _MIN_CORRELATION_WINDOW:
            return 0.0

        ret_sym = df_sym["close"].pct_change().dropna()
        correlations: list[float] = []

        for alloc_sym in allocated_symbols:
            if alloc_sym not in market_data:
                continue
            df_alloc = market_data[alloc_sym]
            if "close" not in df_alloc.columns:
                continue

            ret_alloc = df_alloc["close"].pct_change().dropna()

            # Align on common index
            common = ret_sym.index.intersection(ret_alloc.index)
            if len(common) < _MIN_CORRELATION_WINDOW:
                continue

            try:
                corr = float(
                    ret_sym.loc[common].corr(ret_alloc.loc[common])
                )
                if pd.isna(corr):
                    continue
                # corr is in [-1, 1]; we care about positive correlation
                correlations.append(max(0.0, corr))
            except Exception:
                continue

        if not correlations:
            return 0.0

        avg_corr = sum(correlations) / len(correlations)
        # Map avg_corr [0, 1] → cost [0, 0.5]
        cost = round(avg_corr * _MAX_CORRELATION_COST, 4)
        return cost
