"""Tests for analysis/calibration.py — ConfidenceCalibrator & CalibrationReport"""
from __future__ import annotations
import sys, os, sqlite3, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path):
    """Create minimal SQLite DB with signals, decision_journals, calibration_reports."""
    db = str(tmp_path / "cal.db")
    with sqlite3.connect(db) as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT,
                action      TEXT,
                confidence  REAL,
                outcome     TEXT,
                regime_tag  TEXT,
                setup_type  TEXT,
                timestamp   TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS decision_journals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id   INTEGER,
                json_blob   TEXT,
                outcome_exit REAL,
                outcome_5d  REAL
            );
            CREATE TABLE IF NOT EXISTS calibration_reports (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT NOT NULL,
                json_blob    TEXT NOT NULL
            );
        """)
    return db


def _insert_signal(db, *, symbol="TEST", action="BUY", confidence=0.70,
                   outcome="TP_HIT", regime="bull", setup="technical_base"):
    with sqlite3.connect(db) as c:
        cur = c.execute(
            "INSERT INTO signals (symbol, action, confidence, outcome, regime_tag, setup_type) "
            "VALUES (?,?,?,?,?,?)",
            (symbol, action, confidence, outcome, regime, setup),
        )
        return cur.lastrowid


def _insert_journal(db, signal_id, blob=None, outcome_exit=None, outcome_5d=None):
    blob_text = json.dumps(blob or {})
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO decision_journals (signal_id, json_blob, outcome_exit, outcome_5d) "
            "VALUES (?,?,?,?)",
            (signal_id, blob_text, outcome_exit, outcome_5d),
        )


def _calibrator(db):
    with patch("analysis.calibration.SQLITE_DB_FILE", db):
        from analysis.calibration import ConfidenceCalibrator
        return ConfidenceCalibrator()


# ===========================================================================
# CalibrationReport
# ===========================================================================

class TestCalibrationReport:
    def test_to_dict_roundtrip(self):
        from analysis.calibration import CalibrationReport
        r = CalibrationReport(
            module_stats={"bull": {"tech": {"win_rate": 0.6, "n_trades": 50, "edge": 0.1}}},
            confidence_bands={"0.70-0.79": {"stated_p": 0.745, "actual_win_rate": 0.65,
                                            "correction_factor": 0.87, "n_trades": 30}},
            overconfident_pairs=[("bull", "breakout")],
        )
        d = r.to_dict()
        r2 = CalibrationReport.from_dict(d)
        assert r2.module_stats == r.module_stats
        assert r2.confidence_bands == r.confidence_bands
        assert r2.overconfident_pairs == r.overconfident_pairs

    def test_from_dict_empty(self):
        from analysis.calibration import CalibrationReport
        r = CalibrationReport.from_dict({})
        assert r.module_stats == {}
        assert r.confidence_bands == {}
        assert r.overconfident_pairs == []


# ===========================================================================
# calibration_curve
# ===========================================================================

class TestCalibrationCurve:
    def test_returns_list(self, tmp_path):
        db = _make_db(tmp_path)
        sig_id = _insert_signal(db, confidence=0.72, outcome="TP_HIT")
        _insert_journal(db, sig_id)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.calibration_curve()
        assert isinstance(result, list)

    def test_no_db_returns_empty(self, tmp_path):
        missing = str(tmp_path / "missing.db")
        with patch("analysis.calibration.SQLITE_DB_FILE", missing):
            from analysis.calibration import ConfidenceCalibrator
            result = ConfidenceCalibrator().calibration_curve()
        assert result == []

    def test_bucket_count_matches_n_buckets(self, tmp_path):
        db = _make_db(tmp_path)
        sig_id = _insert_signal(db, confidence=0.72, outcome="TP_HIT")
        _insert_journal(db, sig_id)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.calibration_curve(n_buckets=5)
        assert len(result) == 5

    def test_bucket_has_required_keys(self, tmp_path):
        db = _make_db(tmp_path)
        sig_id = _insert_signal(db, confidence=0.75, outcome="TP_HIT")
        _insert_journal(db, sig_id)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.calibration_curve(n_buckets=10)
        for b in result:
            for key in ("bucket_low", "bucket_high", "stated_pct", "actual_win_rate", "n_trades"):
                assert key in b

    def test_tp_increases_win_rate(self, tmp_path):
        db = _make_db(tmp_path)
        for _ in range(4):
            sid = _insert_signal(db, confidence=0.75, outcome="TP_HIT")
            _insert_journal(db, sid)
        sid = _insert_signal(db, confidence=0.75, outcome="SL_HIT")
        _insert_journal(db, sid)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.calibration_curve(n_buckets=10)
        # bucket for 0.75 should have actual_win_rate = 4/5 = 0.80
        bucket = next((b for b in result if b["n_trades"] > 0), None)
        assert bucket is not None
        assert bucket["actual_win_rate"] == pytest.approx(0.8, abs=0.01)

    def test_unresolved_signals_ignored(self, tmp_path):
        """Signals with outcome=None should not count in n_trades."""
        db = _make_db(tmp_path)
        sid = _insert_signal(db, confidence=0.72, outcome=None)
        _insert_journal(db, sid)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.calibration_curve(n_buckets=10)
        total_trades = sum(b["n_trades"] for b in result)
        assert total_trades == 0


# ===========================================================================
# module_attribution
# ===========================================================================

class TestModuleAttribution:
    def test_no_db_returns_empty(self, tmp_path):
        missing = str(tmp_path / "nope.db")
        with patch("analysis.calibration.SQLITE_DB_FILE", missing):
            from analysis.calibration import ConfidenceCalibrator
            assert ConfidenceCalibrator().module_attribution() == {}

    def test_buy_vote_on_tp_counted(self, tmp_path):
        db = _make_db(tmp_path)
        sid = _insert_signal(db, outcome="TP_HIT")
        blob = {"layer1_votes": {"technical": "BUY"}}
        _insert_journal(db, sid, blob=blob, outcome_exit=1.0)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.module_attribution()
        assert "technical" in result
        assert result["technical"]["tp_rate"] == 1.0
        assert result["technical"]["n_votes"] == 1

    def test_non_buy_vote_excluded(self, tmp_path):
        db = _make_db(tmp_path)
        sid = _insert_signal(db, outcome="SL_HIT")
        blob = {"layer1_votes": {"technical": "SELL"}}
        _insert_journal(db, sid, blob=blob, outcome_exit=-1.0)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.module_attribution()
        assert result == {}

    def test_edge_positive_when_tp_dominates(self, tmp_path):
        db = _make_db(tmp_path)
        for _ in range(3):
            sid = _insert_signal(db, outcome="TP_HIT")
            _insert_journal(db, sid, blob={"layer1_votes": {"ma": "BUY"}}, outcome_exit=1.0)
        sid = _insert_signal(db, outcome="SL_HIT")
        _insert_journal(db, sid, blob={"layer1_votes": {"ma": "BUY"}}, outcome_exit=-1.0)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.module_attribution()
        assert result["ma"]["edge"] > 0.0

    def test_sorted_by_edge_descending(self, tmp_path):
        db = _make_db(tmp_path)
        # module_a: 2 TP, 0 SL → edge=1.0
        for _ in range(2):
            sid = _insert_signal(db, outcome="TP_HIT")
            _insert_journal(db, sid, blob={"layer1_votes": {"module_a": "BUY"}}, outcome_exit=1.0)
        # module_b: 0 TP, 2 SL → edge=-1.0
        for _ in range(2):
            sid = _insert_signal(db, outcome="SL_HIT")
            _insert_journal(db, sid, blob={"layer1_votes": {"module_b": "BUY"}}, outcome_exit=-1.0)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.module_attribution()
        keys = list(result.keys())
        assert keys[0] == "module_a"
        assert keys[-1] == "module_b"


# ===========================================================================
# compute_confidence_calibration
# ===========================================================================

class TestComputeConfidenceCalibration:
    def test_returns_four_bands(self, tmp_path):
        db = _make_db(tmp_path)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.compute_confidence_calibration()
        assert set(result.keys()) == {"0.50-0.59", "0.60-0.69", "0.70-0.79", "0.80+"}

    def test_win_rate_none_when_no_data(self, tmp_path):
        db = _make_db(tmp_path)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.compute_confidence_calibration()
        for band_data in result.values():
            assert band_data["actual_win_rate"] is None
            assert band_data["n_trades"] == 0

    def test_win_rate_computed_for_populated_band(self, tmp_path):
        db = _make_db(tmp_path)
        # 2 wins, 1 loss in 0.70-0.79 band
        for outcome_val in [1.5, 2.0]:
            sid = _insert_signal(db, confidence=0.75, outcome="TP_HIT")
            _insert_journal(db, sid, outcome_exit=outcome_val)
        sid = _insert_signal(db, confidence=0.75, outcome="SL_HIT")
        _insert_journal(db, sid, outcome_exit=-1.0)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.compute_confidence_calibration()
        band = result["0.70-0.79"]
        assert band["n_trades"] == 3
        assert band["actual_win_rate"] == pytest.approx(2 / 3, abs=0.001)

    def test_correction_factor_equals_wr_over_stated(self, tmp_path):
        db = _make_db(tmp_path)
        sid = _insert_signal(db, confidence=0.62, outcome="TP_HIT")
        _insert_journal(db, sid, outcome_exit=1.0)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.compute_confidence_calibration()
        band = result["0.60-0.69"]
        # stated_p for that band = (0.60 + 0.70) / 2 = 0.65
        # actual_win_rate = 1.0 (1 win out of 1)
        # correction_factor = 1.0 / 0.65 ≈ 1.538
        assert band["correction_factor"] is not None
        assert band["correction_factor"] == pytest.approx(1.0 / 0.65, abs=0.01)


# ===========================================================================
# detect_overconfidence
# ===========================================================================

class TestDetectOverconfidence:
    def test_no_data_returns_empty_list(self, tmp_path):
        db = _make_db(tmp_path)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            assert c.detect_overconfidence() == []

    def test_overconfident_pair_detected(self, tmp_path):
        db = _make_db(tmp_path)
        # 10 signals: avg confidence 0.80, actual win rate 0.5 (5 TP, 5 SL) → gap = 0.30
        for i in range(10):
            outcome = "TP_HIT" if i < 5 else "SL_HIT"
            realized = 1.0 if i < 5 else -1.0
            sid = _insert_signal(db, confidence=0.80, outcome=outcome,
                                 regime="bull", setup="momentum")
            _insert_journal(db, sid, outcome_exit=realized)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.detect_overconfidence(threshold=0.10)
        assert ("bull", "momentum") in result

    def test_below_threshold_not_flagged(self, tmp_path):
        db = _make_db(tmp_path)
        # avg confidence = 0.65, win rate = 0.60 → gap = 0.05 (below default 0.10)
        for i in range(10):
            outcome = "TP_HIT" if i < 6 else "SL_HIT"
            realized = 1.0 if i < 6 else -1.0
            sid = _insert_signal(db, confidence=0.65, outcome=outcome,
                                 regime="bull", setup="value")
            _insert_journal(db, sid, outcome_exit=realized)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.detect_overconfidence(threshold=0.10)
        assert ("bull", "value") not in result

    def test_fewer_than_10_samples_skipped(self, tmp_path):
        db = _make_db(tmp_path)
        for i in range(9):  # only 9 samples
            sid = _insert_signal(db, confidence=0.90, outcome="SL_HIT",
                                 regime="bear", setup="breakout")
            _insert_journal(db, sid, outcome_exit=-1.0)
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            result = c.detect_overconfidence()
        assert result == []


# ===========================================================================
# save / load calibration report
# ===========================================================================

class TestSaveLoadReport:
    def test_save_returns_positive_id(self, tmp_path):
        db = _make_db(tmp_path)
        from analysis.calibration import CalibrationReport
        report = CalibrationReport(module_stats={"bull": {}})
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            row_id = c.save_calibration_report(report)
        assert row_id > 0

    def test_load_latest_returns_none_when_empty(self, tmp_path):
        db = _make_db(tmp_path)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            from analysis.calibration import ConfidenceCalibrator
            result = ConfidenceCalibrator.load_latest_report()
        assert result is None

    def test_save_and_load_roundtrip(self, tmp_path):
        db = _make_db(tmp_path)
        from analysis.calibration import CalibrationReport
        report = CalibrationReport(
            module_stats={"bull": {"rsi": {"win_rate": 0.62, "n_trades": 45, "edge": 0.12}}},
            overconfident_pairs=[("bull", "breakout")],
        )
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            c.save_calibration_report(report)
            loaded = c.load_latest_report()
        assert loaded is not None
        assert loaded.module_stats == report.module_stats
        # JSON roundtrip converts tuples → lists; compare as lists
        assert [list(p) for p in loaded.overconfident_pairs] == [list(p) for p in report.overconfident_pairs]

    def test_load_returns_most_recent(self, tmp_path):
        db = _make_db(tmp_path)
        from analysis.calibration import CalibrationReport
        r1 = CalibrationReport(module_stats={"bull": {"a": {}}})
        r2 = CalibrationReport(module_stats={"bear": {"b": {}}})
        c = _calibrator(db)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            c.save_calibration_report(r1)
            c.save_calibration_report(r2)
            loaded = c.load_latest_report()
        assert "bear" in loaded.module_stats


# ===========================================================================
# get_correction_factor
# ===========================================================================

class TestGetCorrectionFactor:
    def test_returns_none_when_no_report(self, tmp_path):
        db = _make_db(tmp_path)
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            from analysis.calibration import ConfidenceCalibrator
            result = ConfidenceCalibrator.get_correction_factor(0.72)
        assert result is None

    def test_returns_factor_for_valid_band(self, tmp_path):
        db = _make_db(tmp_path)
        from analysis.calibration import CalibrationReport, ConfidenceCalibrator
        report = CalibrationReport(
            confidence_bands={
                "0.70-0.79": {
                    "stated_p": 0.745, "actual_win_rate": 0.60,
                    "correction_factor": 0.806, "n_trades": 15
                }
            }
        )
        c = ConfidenceCalibrator()
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            c.save_calibration_report(report)
            factor = ConfidenceCalibrator.get_correction_factor(0.72)
        assert factor == pytest.approx(0.806, abs=0.001)

    def test_returns_none_when_band_has_too_few_trades(self, tmp_path):
        db = _make_db(tmp_path)
        from analysis.calibration import CalibrationReport, ConfidenceCalibrator
        report = CalibrationReport(
            confidence_bands={
                "0.70-0.79": {
                    "stated_p": 0.745, "actual_win_rate": 0.60,
                    "correction_factor": 0.806, "n_trades": 5  # < 10
                }
            }
        )
        c = ConfidenceCalibrator()
        with patch("analysis.calibration.SQLITE_DB_FILE", db):
            c.save_calibration_report(report)
            factor = ConfidenceCalibrator.get_correction_factor(0.72)
        assert factor is None
