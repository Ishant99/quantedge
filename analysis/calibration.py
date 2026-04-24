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
from utils import get_logger
from config import SQLITE_DB_FILE

logger = get_logger("Calibration")


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
            n   = s["total_buy_votes"]
            tp_r = round(s["tp_votes"] / n, 4) if n > 0 else 0.0
            sl_r = round(s["sl_votes"] / n, 4) if n > 0 else 0.0
            result[module] = {
                "tp_rate": tp_r,
                "sl_rate": sl_r,
                "n_votes": n,
                "edge":    round(tp_r - sl_r, 4),
            }

        # Sort by edge descending for easy reading
        result = dict(sorted(result.items(), key=lambda kv: kv[1]["edge"], reverse=True))
        return result

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
