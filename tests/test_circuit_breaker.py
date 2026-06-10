# =============================================================================
# tests/test_circuit_breaker.py — daily / weekly / monthly / drawdown gates
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import date
from unittest.mock import patch

import pytest


def _make_cb(tmp_path, **limits):
    """Build a CircuitBreaker with isolated state file and patched limits."""
    import risk.circuit_breaker as cb_mod
    state_file = str(tmp_path / "circuit_breaker.json")
    patches = [
        patch.object(cb_mod, "CIRCUIT_BREAKER_FILE", state_file),
        patch.object(cb_mod, "send", lambda *a, **k: None),
    ]
    for name, value in limits.items():
        patches.append(patch.object(cb_mod, name, value))
    for p in patches:
        p.start()
    cb = cb_mod.CircuitBreaker()
    # Neutralise weekly check (depends on portfolio snapshots)
    cb._weekly_loss = lambda current: 0.0
    return cb, patches


def _stop(patches):
    for p in patches:
        p.stop()


class TestDrawdownGate:
    def test_allows_when_no_drawdown(self, tmp_path):
        cb, patches = _make_cb(tmp_path)
        try:
            allowed, reason = cb.check(1_000_000)
            assert allowed
        finally:
            _stop(patches)

    def test_blocks_beyond_max_drawdown_from_peak(self, tmp_path):
        cb, patches = _make_cb(tmp_path, MAX_DRAWDOWN_PCT=0.10,
                               MAX_DAILY_LOSS_PCT=0.99, MAX_WEEKLY_LOSS_PCT=0.99,
                               MAX_MONTHLY_LOSS_PCT=0.99)
        try:
            cb.check(1_000_000)              # sets high-water mark
            allowed, reason = cb.check(880_000)  # -12% from peak
            assert not allowed
            assert "drawdown" in reason.lower()
        finally:
            _stop(patches)

    def test_high_water_mark_ratchets_up(self, tmp_path):
        cb, patches = _make_cb(tmp_path, MAX_DRAWDOWN_PCT=0.10,
                               MAX_DAILY_LOSS_PCT=0.99, MAX_WEEKLY_LOSS_PCT=0.99,
                               MAX_MONTHLY_LOSS_PCT=0.99)
        try:
            cb.check(1_000_000)
            cb.check(1_200_000)              # new peak
            # -8% from the NEW peak (1.104M) but +10% above the old one
            allowed, _ = cb.check(1_104_000)
            assert allowed
            # -12% from the new peak → blocked
            allowed, _ = cb.check(1_050_000)
            assert not allowed
        finally:
            _stop(patches)

    def test_hwm_persists_across_daily_reset(self, tmp_path):
        cb, patches = _make_cb(tmp_path, MAX_DRAWDOWN_PCT=0.10,
                               MAX_DAILY_LOSS_PCT=0.99, MAX_WEEKLY_LOSS_PCT=0.99,
                               MAX_MONTHLY_LOSS_PCT=0.99)
        try:
            cb.check(1_000_000)
            # Simulate a new day: force date back so check() triggers _reset
            cb.state["date"] = "2000-01-01"
            cb._save_state()
            allowed, _ = cb.check(880_000)   # still -12% from persisted peak
            assert not allowed
        finally:
            _stop(patches)


class TestMonthlyGate:
    def test_blocks_beyond_monthly_loss_limit(self, tmp_path):
        cb, patches = _make_cb(tmp_path, MAX_MONTHLY_LOSS_PCT=0.15,
                               MAX_DRAWDOWN_PCT=0.99,
                               MAX_DAILY_LOSS_PCT=0.99, MAX_WEEKLY_LOSS_PCT=0.99)
        try:
            cb.check(1_000_000)              # sets month opening value
            allowed, reason = cb.check(840_000)  # -16% this month
            assert not allowed
            assert "month" in reason.lower()
        finally:
            _stop(patches)

    def test_monthly_baseline_resets_on_month_change(self, tmp_path):
        cb, patches = _make_cb(tmp_path, MAX_MONTHLY_LOSS_PCT=0.15,
                               MAX_DRAWDOWN_PCT=0.99,
                               MAX_DAILY_LOSS_PCT=0.99, MAX_WEEKLY_LOSS_PCT=0.99)
        try:
            cb.check(1_000_000)
            # Force a different month into state — next check re-baselines
            cb.state["month"] = "1999-01"
            cb._save_state()
            allowed, _ = cb.check(840_000)   # becomes the new month baseline
            assert allowed
        finally:
            _stop(patches)


class TestDailyGateStillWorks:
    def test_daily_loss_blocks(self, tmp_path):
        cb, patches = _make_cb(tmp_path, MAX_DAILY_LOSS_PCT=0.03,
                               MAX_DRAWDOWN_PCT=0.99, MAX_WEEKLY_LOSS_PCT=0.99,
                               MAX_MONTHLY_LOSS_PCT=0.99)
        try:
            cb.check(1_000_000)              # opening value for the day
            allowed, reason = cb.check(950_000)  # -5% intraday
            assert not allowed
            assert "daily" in reason.lower()
        finally:
            _stop(patches)

    def test_status_includes_new_fields(self, tmp_path):
        cb, patches = _make_cb(tmp_path)
        try:
            cb.check(1_000_000)
            status = cb.get_status()
            for key in ("monthly_loss_pct", "drawdown_pct", "high_water_mark",
                        "max_monthly_pct", "max_drawdown_pct"):
                assert key in status
        finally:
            _stop(patches)
