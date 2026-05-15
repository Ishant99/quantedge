# =============================================================================
# analysis/calibration.py — Confidence Calibration & Module Attribution
#
# Compares stated p_direction (confidence) buckets to actual win rates.
# Attributes TP/SL outcomes to the individual analysis modules that voted.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import json
from dataclasses import dataclass, field
from datetime import datetime
from utils import get_logger
from config import SQLITE_DB_FILE

logger = get_logger("Calibration")


@dataclass
class CalibrationReport:
    """Per-module, per-regime calibration snapshot stored in calibration_reports table."""
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    # {regime: {module: {"win_rate": float, "n_trades": int, "edge": float}}}
    module_stats: dict = field(default_factory=dict)
    # {band: {"stated_p": float, "actual_win_rate": float | None,
    #          "correction_factor": float | None, "n_trades": int}}
    confidence_bands: dict = field(default_factory=dict)
    # [(regime, setup_type), ...]
    overconfident_pairs: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "generated_at":       self.generated_at,
            "module_stats":       self.module_stats,
            "confidence_bands":   self.confidence_bands,
            "overconfident_pairs": self.overconfident_pairs,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CalibrationReport":
        return cls(
            generated_at        = d.get("generated_at", ""),
            module_stats        = d.get("module_stats", {}),
            confidence_bands    = d.get("confidence_bands", {}),
            overconfident_pairs = d.get("overconfident_pairs", []),
        )


class ConfidenceCalibrator:
    """
    Compares stated p_direction to actual win rate in buckets.
    Reads from decision_journals table in SQLite.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calibration_curve(self, n_buckets: int = 10) -> list[dict]:
        """
        Returns list of dicts:
            {bucket_low, bucket_high, stated_pct, actual_win_rate, n_trades}

        Buckets the signals by confidence (p_direction) in n_buckets
        equally-spaced intervals from 0.5 to 1.0.
        Actual win rate = fraction of resolved signals (outcome IS NOT NULL)
        in that bucket where outcome == 'TP_HIT'.
        """
        if not os.path.exists(SQLITE_DB_FILE):
            logger.warning("calibration_curve: DB not found, returning empty list")
            return []

        bucket_size = 0.5 / n_buckets  # range 0.5..1.0 split into n_buckets

        try:
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                rows = conn.execute("""
                    SELECT s.confidence, s.outcome
                    FROM signals s
                    JOIN decision_journals dj ON dj.signal_id = s.id
                    WHERE s.confidence IS NOT NULL
                      AND s.action = 'BUY'
                    ORDER BY s.confidence
                """).fetchall()
        except Exception as e:
            logger.warning(f"calibration_curve: query failed: {e}")
            return []

        if not rows:
            logger.info("calibration_curve: no data found")
            return []

        # Build bucket accumulators
        buckets: list[dict] = []
        for i in range(n_buckets):
            low  = round(0.5 + i * bucket_size, 6)
            high = round(low + bucket_size, 6)
            buckets.append({
                "bucket_low":      low,
                "bucket_high":     high,
                "stated_pct":      round((low + high) / 2, 4),
                "_tp":             0,
                "_resolved":       0,
            })

        for conf, outcome in rows:
            conf = conf or 0.0
            # Clamp to [0.5, 1.0)
            if conf < 0.5:
                conf = 0.5
            if conf >= 1.0:
                conf = 0.9999

            idx = int((conf - 0.5) / bucket_size)
            idx = max(0, min(n_buckets - 1, idx))

            if outcome is not None:  # resolved signal
                buckets[idx]["_resolved"] += 1
                if outcome == "TP_HIT":
                    buckets[idx]["_tp"] += 1

        result = []
        for b in buckets:
            n  = b["_resolved"]
            wr = round(b["_tp"] / n, 4) if n > 0 else None
            result.append({
                "bucket_low":     b["bucket_low"],
                "bucket_high":    b["bucket_high"],
                "stated_pct":     b["stated_pct"],
                "actual_win_rate": wr,
                "n_trades":       n,
            })

        return result

    def module_attribution(self) -> dict:
        """
        For each analysis module (technical, fii_dii, market_breadth, etc.),
        compute how often that module voted BUY on signals that hit TP vs SL.

        Returns:
            {
                "module_name": {
                    "tp_rate": float,   # fraction of BUY votes on TP_HIT signals
                    "sl_rate": float,   # fraction of BUY votes on SL_HIT signals
                    "n_votes": int,     # total BUY votes cast by this module
                    "edge":    float,   # tp_rate - sl_rate
                },
                ...
            }

        Reads decision_journals rows that have an outcome_exit or outcome_5d set,
        parses the json_blob to extract layer1_votes, layer2_votes, layer3_votes.
        """
        if not os.path.exists(SQLITE_DB_FILE):
            logger.warning("module_attribution: DB not found, returning empty dict")
            return {}

        try:
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                rows = conn.execute("""
                    SELECT dj.json_blob, s.outcome
                    FROM decision_journals dj
                    JOIN signals s ON s.id = dj.signal_id
                    WHERE s.outcome IS NOT NULL
                      AND s.action = 'BUY'
                      AND (dj.outcome_exit IS NOT NULL OR dj.outcome_5d IS NOT NULL)
                """).fetchall()
        except Exception as e:
            logger.warning(f"module_attribution: query failed: {e}")
            return {}

        if not rows:
            logger.info("module_attribution: no resolved journalled signals found")
            return {}

        # Accumulate per-module stats
        # Structure: {module: {"tp_votes": int, "sl_votes": int, "total_buy_votes": int}}
        stats: dict[str, dict] = {}

        def _record_votes(votes_dict: dict, outcome: str):
            """Walk a votes dict {module: vote_direction} and tally results."""
            if not isinstance(votes_dict, dict):
                return
            for module, vote in votes_dict.items():
                # Normalise vote value — accept BUY / 1 / "buy"
                is_buy = (
                    vote == "BUY"
                    or vote == 1
                    or (isinstance(vote, str) and vote.strip().upper() == "BUY")
                )
                if not is_buy:
                    continue
                if module not in stats:
                    stats[module] = {"tp_votes": 0, "sl_votes": 0, "total_buy_votes": 0}
                stats[module]["total_buy_votes"] += 1
                if outcome == "TP_HIT":
                    stats[module]["tp_votes"] += 1
                elif outcome == "SL_HIT":
                    stats[module]["sl_votes"] += 1

        for blob_text, outcome in rows:
            try:
                blob = json.loads(blob_text) if blob_text else {}
            except (json.JSONDecodeError, TypeError):
                continue

            # Support flat votes dict or nested layer1/layer2/layer3
            for key in ("layer1_votes", "layer2_votes", "layer3_votes", "votes"):
                _record_votes(blob.get(key, {}), outcome)

            # Also handle a top-level flat structure where keys are module names
            # (some journals store votes at the top level)
            if not any(k in blob for k in ("layer1_votes", "layer2_votes",
                                           "layer3_votes", "votes")):
                _record_votes(blob, outcome)

        result = {}
        for module, s in stats.items():
            n = s["total_buy_votes"]
            if n == 0:
                continue
            tp_r = round(s["tp_votes"] / n, 4)
            sl_r = round(s["sl_votes"] / n, 4)
            result[module] = {
                "tp_rate": tp_r,
                "sl_rate": sl_r,
                "n_votes": n,
                "edge":    round(tp_r - sl_r, 4),
            }

        # Sort by edge descending for easy reading
        result = dict(sorted(result.items(), key=lambda kv: kv[1]["edge"], reverse=True))
        return result

    # ------------------------------------------------------------------
    # Phase 4 — new calibration methods
    # ------------------------------------------------------------------

    def _ensure_reports_table(self):
        """Create calibration_reports table if it doesn't exist."""
        if not os.path.exists(SQLITE_DB_FILE):
            return
        try:
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS calibration_reports (
                        id           INTEGER PRIMARY KEY AUTOINCREMENT,
                        generated_at TEXT NOT NULL,
                        json_blob    TEXT NOT NULL
                    )
                """)
        except Exception as e:
            logger.warning(f"_ensure_reports_table: {e}")

    def compute_module_calibration(self, min_trades: int = 30) -> "CalibrationReport | None":
        """
        For each module in layer1/layer2 votes that voted BUY on a resolved signal:
        compute win rate split by regime.

        Only modules with >= min_trades resolved votes are included.
        Returns None if no data qualifies.
        """
        if not os.path.exists(SQLITE_DB_FILE):
            return None

        try:
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                rows = conn.execute("""
                    SELECT dj.json_blob, s.outcome, s.regime_tag
                    FROM decision_journals dj
                    JOIN signals s ON s.id = dj.signal_id
                    WHERE s.outcome IS NOT NULL
                      AND s.action = 'BUY'
                """).fetchall()
        except Exception as e:
            logger.warning(f"compute_module_calibration: query failed: {e}")
            return None

        if not rows:
            return None

        # acc[regime][module] = {"tp": int, "total": int}
        acc: dict[str, dict[str, dict]] = {}

        for blob_text, outcome, regime in rows:
            regime = (regime or "unknown").lower()
            try:
                blob = json.loads(blob_text) if blob_text else {}
            except (json.JSONDecodeError, TypeError):
                continue

            votes_combined: dict = {}
            for key in ("layer1_votes", "layer2_votes"):
                v = blob.get(key)
                if isinstance(v, dict):
                    votes_combined.update(v)
            if not votes_combined and not any(
                k in blob for k in ("layer1_votes", "layer2_votes")
            ):
                votes_combined = blob

            for module, vote in votes_combined.items():
                is_buy = (
                    vote == "BUY"
                    or vote == 1
                    or (isinstance(vote, str) and vote.strip().upper() == "BUY")
                )
                if not is_buy:
                    continue
                acc.setdefault(regime, {}).setdefault(
                    module, {"tp": 0, "total": 0}
                )
                acc[regime][module]["total"] += 1
                if outcome == "TP_HIT":
                    acc[regime][module]["tp"] += 1

        # Build module_stats, filtering by min_trades
        module_stats: dict[str, dict] = {}
        any_qualified = False
        for regime, modules in acc.items():
            for module, counts in modules.items():
                n = counts["total"]
                if n < min_trades:
                    continue
                any_qualified = True
                win_rate = round(counts["tp"] / n, 4)
                edge     = round(win_rate - 0.5, 4)
                module_stats.setdefault(regime, {})[module] = {
                    "win_rate": win_rate,
                    "n_trades": n,
                    "edge":     edge,
                }

        if not any_qualified:
            logger.info(
                f"compute_module_calibration: no module reached {min_trades} trades"
            )
            return None

        report = CalibrationReport(module_stats=module_stats)
        report.confidence_bands    = self.compute_confidence_calibration()
        report.overconfident_pairs = self.detect_overconfidence()
        return report

    def compute_confidence_calibration(self) -> dict:
        """
        Bucket signals by p_direction band: 0.50-0.59, 0.60-0.69, 0.70-0.79, 0.80+
        For each band: compute actual win rate from outcome_exit (or outcome_5d as fallback).
        Returns {band: {"stated_p", "actual_win_rate", "correction_factor", "n_trades"}}
        """
        if not os.path.exists(SQLITE_DB_FILE):
            return {}

        try:
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                rows = conn.execute("""
                    SELECT s.confidence,
                           COALESCE(dj.outcome_exit, dj.outcome_5d) AS realized_return
                    FROM signals s
                    JOIN decision_journals dj ON dj.signal_id = s.id
                    WHERE s.confidence IS NOT NULL
                      AND s.action = 'BUY'
                      AND (dj.outcome_exit IS NOT NULL OR dj.outcome_5d IS NOT NULL)
                """).fetchall()
        except Exception as e:
            logger.warning(f"compute_confidence_calibration: query failed: {e}")
            return {}

        BANDS = [
            ("0.50-0.59", 0.50, 0.60),
            ("0.60-0.69", 0.60, 0.70),
            ("0.70-0.79", 0.70, 0.80),
            ("0.80+",     0.80, 1.01),
        ]
        acc: dict[str, dict] = {
            b[0]: {"stated_p": (b[1] + min(b[2], 1.0)) / 2, "wins": 0, "total": 0}
            for b in BANDS
        }

        for conf, realized in rows:
            if conf is None or realized is None:
                continue
            c = float(conf)
            for label, lo, hi in BANDS:
                if lo <= c < hi:
                    acc[label]["total"] += 1
                    if float(realized) > 0:
                        acc[label]["wins"] += 1
                    break

        result = {}
        for label, data in acc.items():
            n    = data["total"]
            sp   = data["stated_p"]
            if n > 0:
                wr = round(data["wins"] / n, 4)
                cf = round(wr / sp, 4) if sp > 0 else None
            else:
                wr, cf = None, None
            result[label] = {
                "stated_p":          round(sp, 4),
                "actual_win_rate":   wr,
                "correction_factor": cf,
                "n_trades":          n,
            }

        return result

    def detect_overconfidence(self, threshold: float = 0.10) -> list:
        """
        Returns list of (regime, setup_type) pairs where the average stated
        confidence exceeds the actual win rate by more than threshold.
        Uses outcome_exit (or outcome_5d as fallback) as the realized result.
        """
        if not os.path.exists(SQLITE_DB_FILE):
            return []

        try:
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                rows = conn.execute("""
                    SELECT s.regime_tag, s.setup_type, s.confidence,
                           COALESCE(dj.outcome_exit, dj.outcome_5d) AS realized_return
                    FROM signals s
                    JOIN decision_journals dj ON dj.signal_id = s.id
                    WHERE s.action = 'BUY'
                      AND s.confidence IS NOT NULL
                      AND (dj.outcome_exit IS NOT NULL OR dj.outcome_5d IS NOT NULL)
                """).fetchall()
        except Exception as e:
            logger.warning(f"detect_overconfidence: query failed: {e}")
            return []

        # Group by (regime, setup_type)
        groups: dict[tuple, dict] = {}
        for regime, setup, conf, realized in rows:
            key = (regime or "unknown", setup or "unknown")
            if key not in groups:
                groups[key] = {"conf_sum": 0.0, "wins": 0, "n": 0}
            groups[key]["conf_sum"] += float(conf)
            groups[key]["n"]        += 1
            if realized is not None and float(realized) > 0:
                groups[key]["wins"] += 1

        overconfident = []
        for (regime, setup), data in groups.items():
            n = data["n"]
            if n < 10:  # need at least 10 samples
                continue
            avg_conf   = data["conf_sum"] / n
            actual_wr  = data["wins"] / n
            if avg_conf - actual_wr > threshold:
                overconfident.append((regime, setup))

        return overconfident

    def save_calibration_report(self, report: CalibrationReport) -> int:
        """Persist a CalibrationReport to the calibration_reports table. Returns row id."""
        self._ensure_reports_table()
        if not os.path.exists(SQLITE_DB_FILE):
            return -1
        try:
            blob = json.dumps(report.to_dict())
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                cur = conn.execute("""
                    INSERT INTO calibration_reports (generated_at, json_blob)
                    VALUES (?, ?)
                """, (report.generated_at, blob))
                return cur.lastrowid
        except Exception as e:
            logger.warning(f"save_calibration_report: {e}")
            return -1

    @staticmethod
    def get_correction_factor(p_direction: float) -> "float | None":
        """
        Return the correction factor for *p_direction* from the latest saved
        CalibrationReport.  Returns None when no report exists or the band
        has fewer than 10 trades (insufficient to trust).

        The corrected p_direction is: p_direction × correction_factor.
        A factor < 1.0 means the system is overconfident in that band.
        """
        report = ConfidenceCalibrator.load_latest_report()
        if not report or not report.confidence_bands:
            return None
        BANDS = [
            ("0.50-0.59", 0.50, 0.60),
            ("0.60-0.69", 0.60, 0.70),
            ("0.70-0.79", 0.70, 0.80),
            ("0.80+",     0.80, 1.01),
        ]
        for label, lo, hi in BANDS:
            if lo <= p_direction < hi:
                band = report.confidence_bands.get(label, {})
                if band.get("n_trades", 0) >= 10:
                    return band.get("correction_factor")
        return None

    @staticmethod
    def load_latest_report() -> "CalibrationReport | None":
        """Load the most recently saved CalibrationReport from DB."""
        if not os.path.exists(SQLITE_DB_FILE):
            return None
        try:
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                row = conn.execute("""
                    SELECT json_blob FROM calibration_reports
                    ORDER BY id DESC LIMIT 1
                """).fetchone()
            if row:
                return CalibrationReport.from_dict(json.loads(row[0]))
        except Exception as e:
            logger.warning(f"load_latest_report: {e}")
        return None

    def print_report(self):
        """Print calibration curve and module attribution to stdout."""
        print("\n" + "=" * 60)
        print("  CONFIDENCE CALIBRATION CURVE")
        print("=" * 60)
        curve = self.calibration_curve()
        if not curve:
            print("  No data available.")
        else:
            print(f"  {'Conf Range':<18} {'Stated':>7} {'Actual WR':>10} {'N Trades':>9}")
            print("  " + "-" * 46)
            for b in curve:
                low    = b["bucket_low"]
                high   = b["bucket_high"]
                stated = f"{b['stated_pct']:.0%}"
                actual = f"{b['actual_win_rate']:.1%}" if b["actual_win_rate"] is not None else "   N/A"
                n      = b["n_trades"]
                if n == 0:
                    continue
                print(f"  [{low:.2f} – {high:.2f}]    {stated:>7}   {actual:>9}   {n:>8}")

        print("\n" + "=" * 60)
        print("  MODULE ATTRIBUTION  (BUY votes on resolved signals)")
        print("=" * 60)
        attr = self.module_attribution()
        if not attr:
            print("  No data available.")
        else:
            print(f"  {'Module':<28} {'TP rate':>8} {'SL rate':>8} {'Edge':>8} {'N votes':>8}")
            print("  " + "-" * 62)
            for module, m in attr.items():
                print(
                    f"  {module:<28} {m['tp_rate']:>7.1%}  {m['sl_rate']:>7.1%}"
                    f"  {m['edge']:>+7.1%}  {m['n_votes']:>7}"
                )
        print("=" * 60 + "\n")


if __name__ == "__main__":
    cal = ConfidenceCalibrator()
    cal.print_report()
