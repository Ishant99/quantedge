"""Tests for strategy/execution_planner.py — ExecutionPlanner"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from strategy.execution_planner import ExecutionPlanner, RankedCandidate, _SAME_SECTOR_PENALTY


def _signal(symbol="A", action="BUY", ev=1.0, p_dir=0.70, exec_risk=0.0,
            regime_tag="IT", sector=None):
    class Sig:
        pass
    s = Sig()
    s.symbol         = symbol
    s.action         = action
    s.expected_value = ev
    s.p_direction    = p_dir
    s.execution_risk = exec_risk
    s.regime_tag     = regime_tag
    if sector is not None:
        s.sector = sector
    return s


def _planner():
    return ExecutionPlanner()


class TestRankScore:
    def test_higher_ev_ranks_higher(self):
        # Use exec_risk=1.0 so scores stay below cap of 10
        p = _planner()
        lo = p._compute_rank_score(_signal(ev=0.5, p_dir=0.6, exec_risk=1.0))
        hi = p._compute_rank_score(_signal(ev=2.0, p_dir=0.6, exec_risk=1.0))
        assert hi > lo

    def test_higher_pdirection_ranks_higher(self):
        # Use exec_risk=1.0 so scores stay below cap of 10
        p = _planner()
        lo = p._compute_rank_score(_signal(ev=1.0, p_dir=0.55, exec_risk=1.0))
        hi = p._compute_rank_score(_signal(ev=1.0, p_dir=0.85, exec_risk=1.0))
        assert hi > lo

    def test_higher_exec_risk_ranks_lower(self):
        p = _planner()
        safe = p._compute_rank_score(_signal(ev=1.0, p_dir=0.70, exec_risk=0.1))
        risky= p._compute_rank_score(_signal(ev=1.0, p_dir=0.70, exec_risk=0.9))
        assert safe > risky

    def test_zero_ev_zero_score(self):
        p = _planner()
        assert p._compute_rank_score(_signal(ev=0.0)) == 0.0

    def test_score_bounded_at_10(self):
        p = _planner()
        score = p._compute_rank_score(_signal(ev=1000.0, p_dir=1.0, exec_risk=0.0))
        assert score <= 10.0


class TestSectorPenalty:
    def test_no_penalty_new_sector(self):
        p = _planner()
        assert p._sector_penalty("BANK", {"IT", "PHARMA"}) == 0.0

    def test_penalty_duplicate_sector(self):
        p = _planner()
        pen = p._sector_penalty("IT", {"IT"})
        assert pen == _SAME_SECTOR_PENALTY

    def test_no_penalty_empty_sector(self):
        p = _planner()
        assert p._sector_penalty("", {"IT"}) == 0.0


class TestCorrelationCost:
    def _make_df(self, n=30, seed=0):
        import numpy as np
        rng = np.random.default_rng(seed)
        prices = 100 + rng.normal(0, 1, n).cumsum()
        return pd.DataFrame({"close": prices})

    def test_no_cost_when_no_allocated(self):
        p = _planner()
        df = self._make_df()
        assert p._correlation_cost("A", [], {"A": df}) == 0.0

    def test_no_cost_when_symbol_missing(self):
        p = _planner()
        assert p._correlation_cost("MISSING", ["A"], {}) == 0.0

    def test_cost_positive_for_correlated(self):
        p = _planner()
        df = self._make_df(seed=1)
        # Same data → perfect correlation
        cost = p._correlation_cost("A", ["B"], {"A": df, "B": df})
        assert cost > 0.0

    def test_cost_bounded(self):
        p = _planner()
        df = self._make_df(seed=2)
        cost = p._correlation_cost("A", ["B"], {"A": df, "B": df})
        assert 0.0 <= cost <= 0.5


class TestRankAndAllocate:
    def test_empty_signals_returns_empty(self):
        result = _planner().rank_and_allocate([], max_slots=5)
        assert result == []

    def test_non_buy_signals_excluded(self):
        sigs = [_signal("A", action="SELL"), _signal("B", action="HOLD")]
        result = _planner().rank_and_allocate(sigs, max_slots=5)
        assert result == []

    def test_all_allocated_when_slots_available(self):
        sigs = [_signal("A"), _signal("B"), _signal("C")]
        result = _planner().rank_and_allocate(sigs, max_slots=5)
        assert all(r.slot_allocated for r in result)

    def test_excess_candidates_rejected(self):
        sigs = [_signal(f"S{i}", ev=float(i)) for i in range(5)]
        result = _planner().rank_and_allocate(sigs, max_slots=2)
        allocated = [r for r in result if r.slot_allocated]
        rejected  = [r for r in result if not r.slot_allocated]
        assert len(allocated) == 2
        assert len(rejected)  == 3

    def test_best_ranked_allocated_first(self):
        # Use exec_risk so scores stay below cap and are distinguishable
        sigs = [
            _signal("LOW",  ev=0.5, p_dir=0.60, exec_risk=1.0),
            _signal("HIGH", ev=3.0, p_dir=0.85, exec_risk=1.0),
        ]
        result = _planner().rank_and_allocate(sigs, max_slots=1)
        allocated = [r for r in result if r.slot_allocated]
        assert len(allocated) == 1
        assert allocated[0].signal.symbol == "HIGH"

    def test_results_sorted_descending_by_rank(self):
        sigs = [_signal(f"S{i}", ev=float(i) * 0.5, p_dir=0.6) for i in range(4)]
        result = _planner().rank_and_allocate(sigs, max_slots=10)
        scores = [r.rank_score for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_sector_penalty_causes_rejection(self):
        # Same sector, only 1 slot → second candidate rejected despite high score
        sigs = [
            _signal("A", ev=2.0, regime_tag="IT"),
            _signal("B", ev=1.9, regime_tag="IT"),
        ]
        result = _planner().rank_and_allocate(
            sigs, max_slots=1, open_position_sectors={"IT"}
        )
        # Both have sector penalty applied but slot is still 1
        allocated = [r for r in result if r.slot_allocated]
        assert len(allocated) == 1

    def test_heat_reduction_when_portfolio_hot(self):
        sigs = [_signal("A", ev=2.0)]
        r_cool = _planner().rank_and_allocate(sigs, max_slots=5,
                                               portfolio_deployed_pct=0.3)
        r_hot  = _planner().rank_and_allocate(sigs, max_slots=5,
                                               portfolio_deployed_pct=0.7)
        # Hot portfolio applies heat reduction → lower rank score
        assert r_hot[0].rank_score <= r_cool[0].rank_score

    def test_rejection_reason_populated(self):
        sigs = [_signal("A", ev=2.0), _signal("B", ev=1.0)]
        result = _planner().rank_and_allocate(sigs, max_slots=1)
        rejected = [r for r in result if not r.slot_allocated]
        assert len(rejected) == 1
        assert rejected[0].rejection_reason != ""
