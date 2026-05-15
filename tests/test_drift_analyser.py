"""Tests for backtest/drift_analysis.py — DriftAnalyser"""
from __future__ import annotations
import sys, os, sqlite3, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path):
    db = str(tmp_path / "drift.db")
    with sqlite3.connect(db) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol          TEXT,
                action          TEXT DEFAULT 'BUY',
                confidence      REAL,
                ta_score        REAL,
                entry_price     REAL,
                stop_loss       REAL,
                take_profit     REAL,
                outcome         TEXT,
                outcome_price   REAL,
                outcome_date    TEXT,
                days_to_outcome INTEGER,
                regime_tag      TEXT,
                setup_type      TEXT,
                timestamp       TEXT DEFAULT (datetime('now'))
            )
        """)
    return db


def _insert_signal(db, *, symbol="TEST", action="BUY", confidence=0.70,
                   outcome="TP_HIT", timestamp=None):
    ts = timestamp or "2026-01-01T10:00:00"
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO signals (symbol, action, confidence, outcome, timestamp) "
            "VALUES (?,?,?,?,?)",
            (symbol, action, confidence, outcome, ts),
        )


def _analyser(db, backtest_dir=None):
    bd = backtest_dir or ""
    with patch("backtest.drift_analysis.SQLITE_DB_FILE", db), \
         patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", bd):
        from backtest.drift_analysis import DriftAnalyser
        return DriftAnalyser()


# ===========================================================================
# _empty_result
# ===========================================================================

class TestEmptyResult:
    def test_structure(self):
        from backtest.drift_analysis import DriftAnalyser
        r = DriftAnalyser()._empty_result("TEST")
        assert r["total_signals"] == 0
        assert r["drift_score"] == 0.0
        assert r["recommendation"] == "OK"
        assert r["details"]["note"] == "TEST"

    def test_all_rates_zero(self):
        from backtest.drift_analysis import DriftAnalyser
        r = DriftAnalyser()._empty_result("X")
        assert r["direction_match_rate"] == 0.0
        assert r["outcome_match_rate"] == 0.0
        assert r["avg_confidence_gap"] == 0.0


# ===========================================================================
# analyse — no data cases
# ===========================================================================

class TestAnalyseNoData:
    def test_missing_db_returns_ok(self, tmp_path):
        missing = str(tmp_path / "nope.db")
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", missing), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", ""):
            from backtest.drift_analysis import DriftAnalyser
            r = DriftAnalyser().analyse()
        assert r["recommendation"] == "OK"
        assert r["total_signals"] == 0

    def test_empty_db_returns_ok(self, tmp_path):
        db = _make_db(tmp_path)
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", db), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", ""):
            from backtest.drift_analysis import DriftAnalyser
            r = DriftAnalyser().analyse()
        assert r["recommendation"] == "OK"
        assert r["total_signals"] == 0


# ===========================================================================
# analyse — with data
# ===========================================================================

class TestAnalyseWithData:
    def test_direction_match_rate_100pct_all_tp(self, tmp_path):
        db = _make_db(tmp_path)
        for _ in range(5):
            _insert_signal(db, outcome="TP_HIT", confidence=0.70)
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", db), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", ""):
            from backtest.drift_analysis import DriftAnalyser
            r = DriftAnalyser().analyse(lookback_days=9999)
        assert r["direction_match_rate"] == 1.0
        assert r["total_signals"] == 5

    def test_direction_match_rate_0pct_all_sl(self, tmp_path):
        db = _make_db(tmp_path)
        for _ in range(4):
            _insert_signal(db, outcome="SL_HIT", confidence=0.70)
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", db), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", ""):
            from backtest.drift_analysis import DriftAnalyser
            r = DriftAnalyser().analyse(lookback_days=9999)
        assert r["direction_match_rate"] == 0.0

    def test_result_has_required_keys(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_signal(db, outcome="TP_HIT")
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", db), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", ""):
            from backtest.drift_analysis import DriftAnalyser
            r = DriftAnalyser().analyse(lookback_days=9999)
        for key in ("total_signals", "direction_match_rate", "outcome_match_rate",
                    "avg_confidence_gap", "drift_score", "recommendation", "details"):
            assert key in r

    def test_lookback_filters_old_signals(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_signal(db, outcome="TP_HIT", timestamp="2020-01-01T00:00:00")
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", db), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", ""):
            from backtest.drift_analysis import DriftAnalyser
            r = DriftAnalyser().analyse(lookback_days=30)
        assert r["total_signals"] == 0


# ===========================================================================
# Drift score formula
# ===========================================================================

class TestDriftScoreFormula:
    def test_perfect_calibration_low_drift(self, tmp_path):
        """When stated confidence = actual win rate, drift should be near 0."""
        db = _make_db(tmp_path)
        for _ in range(7):
            _insert_signal(db, outcome="TP_HIT", confidence=0.70)
        for _ in range(3):
            _insert_signal(db, outcome="SL_HIT", confidence=0.70)
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", db), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", ""):
            from backtest.drift_analysis import DriftAnalyser
            r = DriftAnalyser().analyse(lookback_days=9999)
        assert r["drift_score"] < 0.20

    def test_severe_overconfidence_high_drift(self, tmp_path):
        """Stated 0.90, win rate 0.20 → large confidence gap → high drift."""
        db = _make_db(tmp_path)
        for _ in range(2):
            _insert_signal(db, outcome="TP_HIT", confidence=0.90)
        for _ in range(8):
            _insert_signal(db, outcome="SL_HIT", confidence=0.90)
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", db), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", ""):
            from backtest.drift_analysis import DriftAnalyser
            r = DriftAnalyser().analyse(lookback_days=9999)
        assert r["drift_score"] >= 0.20

    def test_drift_score_bounded_0_to_1(self, tmp_path):
        db = _make_db(tmp_path)
        for _ in range(3):
            _insert_signal(db, outcome="SL_HIT", confidence=0.99)
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", db), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", ""):
            from backtest.drift_analysis import DriftAnalyser
            r = DriftAnalyser().analyse(lookback_days=9999)
        assert 0.0 <= r["drift_score"] <= 1.0

    def test_confidence_gap_sign(self, tmp_path):
        """When confidence >> win rate, gap is positive (overconfident)."""
        db = _make_db(tmp_path)
        for _ in range(1):
            _insert_signal(db, outcome="TP_HIT", confidence=0.90)
        for _ in range(9):
            _insert_signal(db, outcome="SL_HIT", confidence=0.90)
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", db), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", ""):
            from backtest.drift_analysis import DriftAnalyser
            r = DriftAnalyser().analyse(lookback_days=9999)
        assert r["avg_confidence_gap"] > 0.0


# ===========================================================================
# Recommendation thresholds
# ===========================================================================

class TestRecommendationThresholds:
    def _result_with_score(self, score):
        from backtest.drift_analysis import DriftAnalyser, DRIFT_OK, DRIFT_HALT
        d = DriftAnalyser()
        if score < DRIFT_OK:
            rec = "OK"
        elif score < DRIFT_HALT:
            rec = "RECALIBRATE"
        else:
            rec = "HALT"
        return rec

    def test_score_below_02_is_ok(self):
        assert self._result_with_score(0.15) == "OK"

    def test_score_between_02_and_04_is_recalibrate(self):
        assert self._result_with_score(0.30) == "RECALIBRATE"

    def test_score_at_04_is_halt(self):
        assert self._result_with_score(0.40) == "HALT"

    def test_score_above_04_is_halt(self):
        assert self._result_with_score(0.75) == "HALT"


# ===========================================================================
# _load_backtest_win_rate
# ===========================================================================

class TestLoadBacktestWinRate:
    def test_returns_none_when_file_missing(self, tmp_path):
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", str(tmp_path / "d.db")), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", str(tmp_path)):
            from backtest.drift_analysis import DriftAnalyser
            result = DriftAnalyser()._load_backtest_win_rate("AAPL")
        assert result is None

    def test_returns_win_rate_from_json(self, tmp_path):
        bt_file = tmp_path / "RELIANCE_backtest.json"
        bt_file.write_text(json.dumps({"result": {"win_rate_pct": 62.5}}))
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", str(tmp_path / "d.db")), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", str(tmp_path)):
            from backtest.drift_analysis import DriftAnalyser
            result = DriftAnalyser()._load_backtest_win_rate("RELIANCE")
        assert result == pytest.approx(62.5)

    def test_returns_none_for_corrupt_json(self, tmp_path):
        bt_file = tmp_path / "BAD_backtest.json"
        bt_file.write_text("not valid json{{")
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", str(tmp_path / "d.db")), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", str(tmp_path)):
            from backtest.drift_analysis import DriftAnalyser
            result = DriftAnalyser()._load_backtest_win_rate("BAD")
        assert result is None


# ===========================================================================
# _compute_symbol_outcome_matches
# ===========================================================================

class TestComputeSymbolOutcomeMatches:
    def test_no_backtest_file_skips_comparison(self, tmp_path):
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", str(tmp_path / "d.db")), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", str(tmp_path)):
            from backtest.drift_analysis import DriftAnalyser
            signals = [
                {"symbol": "NOPE", "outcome": "TP_HIT"},
                {"symbol": "NOPE", "outcome": "SL_HIT"},
            ]
            result = DriftAnalyser()._compute_symbol_outcome_matches(signals)
        assert result["compared_symbols"] == 0
        assert len(result["symbol_breakdown"]) == 1

    def test_match_when_live_tp_above_threshold(self, tmp_path):
        bt_file = tmp_path / "TCS_backtest.json"
        bt_file.write_text(json.dumps({"result": {"win_rate_pct": 60.0}}))
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", str(tmp_path / "d.db")), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", str(tmp_path)):
            from backtest.drift_analysis import DriftAnalyser
            # Live TP rate = 1.0 (100%) ≥ threshold = 60.0 * 0.75 / 100 = 0.45
            signals = [{"symbol": "TCS", "outcome": "TP_HIT"}]
            result = DriftAnalyser()._compute_symbol_outcome_matches(signals)
        assert result["matched_symbols"] == 1
        assert result["compared_symbols"] == 1

    def test_no_match_when_live_tp_below_threshold(self, tmp_path):
        bt_file = tmp_path / "INFY_backtest.json"
        bt_file.write_text(json.dumps({"result": {"win_rate_pct": 80.0}}))
        with patch("backtest.drift_analysis.SQLITE_DB_FILE", str(tmp_path / "d.db")), \
             patch("backtest.drift_analysis.BACKTEST_RESULTS_DIR", str(tmp_path)):
            from backtest.drift_analysis import DriftAnalyser
            # Live TP rate = 0.0 (all SL) — threshold = 80 * 0.75 / 100 = 0.60 → fails
            signals = [
                {"symbol": "INFY", "outcome": "SL_HIT"},
                {"symbol": "INFY", "outcome": "SL_HIT"},
            ]
            result = DriftAnalyser()._compute_symbol_outcome_matches(signals)
        assert result["matched_symbols"] == 0
        assert result["compared_symbols"] == 1
