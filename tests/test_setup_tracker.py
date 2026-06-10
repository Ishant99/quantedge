# =============================================================================
# tests/test_setup_tracker.py — Phase-3 statistical gate unit tests
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import pytest
from analysis.setup_tracker import (
    SetupTracker, SetupStats,
    wilson_lower_bound, breakeven_win_rate,
    _MIN_TRADES_KILL, _MIN_TRADES_PROBATION, _MIN_TRADES_SCALE, _SCALE_MARGIN,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_db(tmp_path, rows):
    """Create a minimal signals DB with the given outcome/setup_type rows."""
    db = str(tmp_path / "trades.db")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE signals "
            "(outcome TEXT, setup_type TEXT, pnl REAL)"
        )
        conn.executemany("INSERT INTO signals VALUES (?,?,?)", rows)
    return db


def _wins_losses(wins, losses, setup="technical_base", win_pnl=200.0, loss_pnl=100.0):
    """Build row list: wins TP_HIT rows + losses SL_HIT rows."""
    rows  = [(  "TP_HIT", setup, win_pnl)  for _ in range(wins)]
    rows += [("SL_HIT",  setup, -loss_pnl) for _ in range(losses)]
    return rows


# ---------------------------------------------------------------------------
# wilson_lower_bound
# ---------------------------------------------------------------------------

class TestWilsonLB:
    def test_zero_n(self):
        assert wilson_lower_bound(0, 0) == 0.0

    def test_all_wins(self):
        lb = wilson_lower_bound(100, 100)
        assert lb > 0.94

    def test_all_losses(self):
        lb = wilson_lower_bound(0, 50)
        assert lb == pytest.approx(0.0, abs=0.01)

    def test_50pct_lower_bound(self):
        # With 100 trades at 50% WR the 95% LB should be comfortably below 50%
        lb = wilson_lower_bound(50, 100)
        assert 0.40 < lb < 0.50


# ---------------------------------------------------------------------------
# breakeven_win_rate
# ---------------------------------------------------------------------------

class TestBreakevenWR:
    def test_2to1_rr(self):
        assert breakeven_win_rate(2.0) == pytest.approx(1 / 3, rel=1e-6)

    def test_1to1_rr(self):
        assert breakeven_win_rate(1.0) == pytest.approx(0.5, rel=1e-6)

    def test_near_zero_rr(self):
        # Should not divide by zero
        assert breakeven_win_rate(0.0) <= 1.0


# ---------------------------------------------------------------------------
# SetupTracker — empty / missing DB
# ---------------------------------------------------------------------------

class TestSetupTrackerEmpty:
    def test_returns_insufficient_when_no_db(self, tmp_path):
        nonexistent = str(tmp_path / "no.db")
        tracker = SetupTracker(db_path=nonexistent)
        stats = tracker.evaluate("technical_base")
        assert stats.verdict == "INSUFFICIENT_DATA"
        assert stats.n_trades == 0

    def test_returns_insufficient_with_zero_trades(self, tmp_path):
        db = _make_db(tmp_path, [])
        tracker = SetupTracker(db_path=db)
        stats = tracker.evaluate("technical_base")
        assert stats.verdict == "INSUFFICIENT_DATA"

    def test_evaluate_all_covers_known_setups(self, tmp_path):
        db = _make_db(tmp_path, [])
        tracker = SetupTracker(db_path=db)
        all_stats = tracker.evaluate_all()
        assert "technical_base" in all_stats
        assert "breakout_52w" in all_stats
        assert "rsi2_mean_reversion" in all_stats


# ---------------------------------------------------------------------------
# INSUFFICIENT_DATA gate (< 20 trades)
# ---------------------------------------------------------------------------

class TestInsufficientData:
    def test_19_trades_returns_insufficient(self, tmp_path):
        rows = _wins_losses(10, 9)
        db = _make_db(tmp_path, rows)
        stats = SetupTracker(db_path=db).evaluate("technical_base")
        assert stats.n_trades == 19
        assert stats.verdict == "INSUFFICIENT_DATA"

    def test_exactly_20_trades_leaves_insufficient(self, tmp_path):
        # 20 trades at 50% WR → expectancy = 0 (not < -0.1R), not KILL or SCALE → OK
        rows = _wins_losses(10, 10)
        db = _make_db(tmp_path, rows)
        stats = SetupTracker(db_path=db).evaluate("technical_base")
        assert stats.n_trades == 20
        assert stats.verdict in ("OK", "PROBATION")   # past INSUFFICIENT threshold


# ---------------------------------------------------------------------------
# KILL gate (≥ 30 trades, Wilson LB < breakeven)
# ---------------------------------------------------------------------------

class TestKillGate:
    def test_kill_fires_at_30_trades_with_low_wr(self, tmp_path):
        # 8 wins out of 30 → WR = 26.7%, Wilson LB ≈ 12% — below breakeven 33%
        rows = _wins_losses(8, 22)
        db = _make_db(tmp_path, rows)
        stats = SetupTracker(db_path=db).evaluate("technical_base")
        assert stats.n_trades == 30
        assert stats.verdict == "KILL"
        assert "Wilson" in stats.verdict_reason

    def test_kill_does_not_fire_at_29_trades(self, tmp_path):
        rows = _wins_losses(8, 21)           # 29 trades
        db = _make_db(tmp_path, rows)
        stats = SetupTracker(db_path=db).evaluate("technical_base")
        assert stats.n_trades == 29
        assert stats.verdict != "KILL"

    def test_no_kill_with_healthy_wr(self, tmp_path):
        # 20 wins out of 30 → WR = 66.7%, Wilson LB ≈ 49% > breakeven 33%
        rows = _wins_losses(20, 10)
        db = _make_db(tmp_path, rows)
        stats = SetupTracker(db_path=db).evaluate("technical_base")
        assert stats.verdict != "KILL"


# ---------------------------------------------------------------------------
# PROBATION gate (≥ 20 trades, expectancy < -0.1R)
# ---------------------------------------------------------------------------

class TestProbationGate:
    def test_probation_fires_on_negative_expectancy(self, tmp_path):
        # 8 wins / 22 losses at 2:1 R:R → expectancy = 0.267*2 - 0.733 = -0.2R
        # But we need ≥ 20 and < 30 for just PROBATION to fire (not KILL)
        rows = _wins_losses(6, 15)   # 21 trades, WR=28.6%, exp ≈ -0.14R < -0.1
        db = _make_db(tmp_path, rows)
        stats = SetupTracker(db_path=db).evaluate("technical_base")
        assert stats.verdict == "PROBATION"
        assert stats.expectancy_r < -0.1

    def test_no_probation_on_positive_expectancy(self, tmp_path):
        # 14 wins / 8 losses at 2:1 → expectancy = +0.63R
        rows = _wins_losses(14, 8)   # 22 trades
        db = _make_db(tmp_path, rows)
        stats = SetupTracker(db_path=db).evaluate("technical_base")
        assert stats.verdict not in ("PROBATION", "KILL")


# ---------------------------------------------------------------------------
# SCALE gate (≥ 50 trades, Wilson LB > breakeven + 5pp)
# ---------------------------------------------------------------------------

class TestScaleGate:
    def test_scale_fires_with_high_wr_and_50_trades(self, tmp_path):
        # 35 wins / 15 losses → WR=70%, Wilson LB ≈ 56% > breakeven(33%)+5pp=38%
        rows = _wins_losses(35, 15)   # 50 trades
        db = _make_db(tmp_path, rows)
        stats = SetupTracker(db_path=db).evaluate("technical_base")
        assert stats.n_trades == 50
        assert stats.verdict == "SCALE"
        assert stats.wilson_lb_95 > breakeven_win_rate(stats.avg_rr) + _SCALE_MARGIN

    def test_scale_needs_at_least_50_trades(self, tmp_path):
        rows = _wins_losses(35, 14)   # 49 trades, same WR
        db = _make_db(tmp_path, rows)
        stats = SetupTracker(db_path=db).evaluate("technical_base")
        assert stats.n_trades == 49
        assert stats.verdict != "SCALE"


# ---------------------------------------------------------------------------
# OK verdict
# ---------------------------------------------------------------------------

class TestOKVerdict:
    def test_ok_with_moderate_wr_and_30_trades(self, tmp_path):
        # 17 wins / 13 losses → WR=56.7%, Wilson LB ≈ 39% > breakeven 33.3%
        # n=30 → KILL gate would need Wilson LB < breakeven (not the case)
        # n=30 < 50 → SCALE doesn't fire; expectancy ≈ +0.7R → no PROBATION
        rows = _wins_losses(17, 13)
        db = _make_db(tmp_path, rows)
        stats = SetupTracker(db_path=db).evaluate("technical_base")
        assert stats.verdict == "OK"
        assert stats.expectancy_r > 0


# ---------------------------------------------------------------------------
# Multiple setups in the same DB
# ---------------------------------------------------------------------------

class TestMultipleSetups:
    def test_evaluate_all_separates_setups(self, tmp_path):
        rows = (
            _wins_losses(10, 5, setup="technical_base")
            + _wins_losses(8, 22, setup="breakout_52w")
        )
        db = _make_db(tmp_path, rows)
        all_stats = SetupTracker(db_path=db).evaluate_all()
        assert all_stats["technical_base"].n_trades == 15
        assert all_stats["breakout_52w"].n_trades == 30

    def test_missing_setup_returns_insufficient(self, tmp_path):
        rows = _wins_losses(10, 5, setup="technical_base")
        db = _make_db(tmp_path, rows)
        all_stats = SetupTracker(db_path=db).evaluate_all()
        # rsi2_mean_reversion has no trades in this DB
        assert all_stats["rsi2_mean_reversion"].verdict == "INSUFFICIENT_DATA"
        assert all_stats["rsi2_mean_reversion"].n_trades == 0
