# =============================================================================
# backtest/drift_analysis.py — Signal Drift Analyser
#
# Compares live paper-trading performance to what the backtest model
# predicted. Measures drift = divergence between backtested expected
# outcomes and actual paper-trading outcomes.
#
# Does NOT re-run the backtest engine (too slow). Instead it uses:
#   - The signals table in SQLite (has confidence, TP/SL, and outcome columns)
#   - Historical win-rate benchmarks from backtest JSON result files
#
# Drift Score: 0 = no drift (live matches backtest), 1 = total drift
#
# Recommendation thresholds:
#   drift_score < 0.20 → "OK"
#   drift_score < 0.40 → "RECALIBRATE"
#   drift_score >= 0.40 → "HALT"
#
# Usage:
#   python -m backtest.drift_analysis
#   python -m backtest.drift_analysis --days 60
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import json
import math
from datetime import datetime, timedelta
from typing import Optional

from config import SQLITE_DB_FILE, BACKTEST_RESULTS_DIR
from utils import get_logger

logger = get_logger("DriftAnalyser")

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
DRIFT_OK          = 0.20
DRIFT_RECALIBRATE = 0.40   # >= this → RECALIBRATE
DRIFT_HALT        = 0.40   # same boundary — only HALT when confirmed above 0.40


class DriftAnalyser:
    """
    Compares paper trading performance to what backtest predicted.

    Measures drift = divergence between backtested signals and actual
    paper outcomes.

    All analysis is done from the signals table in SQLite — no external
    data fetching, no re-running the backtest engine.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyse(self, lookback_days: int = 90) -> dict:
        """
        Compare signals in DB (with outcomes) to what backtest engine
        would have predicted.

        Parameters
        ----------
        lookback_days : how many days back to look in the signals table.
                        Signals older than this are excluded.

        Returns
        -------
        dict with keys:
          total_signals        int   — total signals with resolved outcomes
          direction_match_rate float — fraction where signal direction matched outcome
          outcome_match_rate   float — fraction where both backtest and live agreed (TP)
          avg_confidence_gap   float — live stated confidence - actual win rate
          drift_score          float — 0 = no drift, 1 = total drift
          recommendation       str   — "OK" | "RECALIBRATE" | "HALT"
          details              dict  — extra breakdown for debugging
        """
        if not os.path.exists(SQLITE_DB_FILE):
            logger.warning(f"DriftAnalyser: DB not found at {SQLITE_DB_FILE}")
            return self._empty_result("DB_NOT_FOUND")

        cutoff = (datetime.now() - timedelta(days=lookback_days)).isoformat()
        signals = self._load_resolved_signals(cutoff)

        if not signals:
            logger.warning(
                f"DriftAnalyser: no resolved signals in last {lookback_days} days"
            )
            return self._empty_result("NO_DATA")

        logger.info(
            f"DriftAnalyser: analysing {len(signals)} resolved signals "
            f"(last {lookback_days} days)"
        )

        # ------------------------------------------------------------------
        # 1. Direction match rate
        #    We treat BUY signals as "bullish" predictions.
        #    A correct direction = outcome is TP_HIT (price went up to target).
        #    SL_HIT or EXPIRED at a loss = direction was wrong.
        # ------------------------------------------------------------------
        tp_hits     = [s for s in signals if s["outcome"] == "TP_HIT"]
        sl_hits     = [s for s in signals if s["outcome"] == "SL_HIT"]
        expired     = [s for s in signals if s["outcome"] == "EXPIRED"]

        n_total         = len(signals)
        n_tp            = len(tp_hits)
        n_sl            = len(sl_hits)
        n_exp           = len(expired)
        direction_match = n_tp / n_total if n_total > 0 else 0.0

        # ------------------------------------------------------------------
        # 2. Outcome match rate
        #    Compare per-symbol live TP rate to backtest predicted win rate.
        #    For each symbol we have a backtest JSON with win_rate_pct.
        #    We check if live TP rate ≥ backtest win rate × 0.75
        #    (allowing 25% degradation before flagging as a mismatch).
        # ------------------------------------------------------------------
        symbol_match_results = self._compute_symbol_outcome_matches(signals)
        outcome_match_rate   = (
            symbol_match_results["matched_symbols"] /
            symbol_match_results["compared_symbols"]
            if symbol_match_results["compared_symbols"] > 0 else direction_match
        )

        # ------------------------------------------------------------------
        # 3. Confidence gap
        #    avg_confidence_gap = mean(stated_confidence) - actual_win_rate
        #    Positive gap → model is overconfident (drift upward)
        #    Negative gap → model is underconfident (rarer, less concerning)
        # ------------------------------------------------------------------
        stated_confidences = [
            s["confidence"] for s in signals
            if s.get("confidence") is not None
        ]
        avg_stated_conf  = (
            sum(stated_confidences) / len(stated_confidences)
            if stated_confidences else 0.5
        )
        avg_confidence_gap = round(avg_stated_conf - direction_match, 4)

        # ------------------------------------------------------------------
        # 4. Drift score (composite, 0–1)
        #
        # Drift is a weighted combination of three components:
        #   a) win_rate_gap      = how far the live TP rate deviates from
        #                          the expected (stated confidence)
        #   b) outcome_deviation = 1 - outcome_match_rate (symbol-level)
        #   c) confidence_bias   = magnitude of confidence gap, capped at 1
        #
        # Weights: win_rate_gap 0.50, outcome_deviation 0.35, conf_bias 0.15
        # ------------------------------------------------------------------
        win_rate_gap      = abs(direction_match - avg_stated_conf)
        outcome_deviation = 1.0 - outcome_match_rate
        confidence_bias   = min(abs(avg_confidence_gap), 1.0)

        drift_score = round(
            0.50 * win_rate_gap +
            0.35 * outcome_deviation +
            0.15 * confidence_bias,
            4,
        )
        drift_score = min(drift_score, 1.0)

        # ------------------------------------------------------------------
        # 5. Recommendation
        # ------------------------------------------------------------------
        if drift_score < DRIFT_OK:
            recommendation = "OK"
        elif drift_score < DRIFT_HALT:
            recommendation = "RECALIBRATE"
        else:
            recommendation = "HALT"

        # ------------------------------------------------------------------
        # 6. Compile result
        # ------------------------------------------------------------------
        result = {
            "total_signals":       n_total,
            "direction_match_rate": round(direction_match, 4),
            "outcome_match_rate":   round(outcome_match_rate, 4),
            "avg_confidence_gap":   avg_confidence_gap,
            "drift_score":          drift_score,
            "recommendation":       recommendation,
            "details": {
                "tp_count":            n_tp,
                "sl_count":            n_sl,
                "expired_count":       n_exp,
                "avg_stated_conf":     round(avg_stated_conf, 4),
                "live_tp_rate":        round(direction_match, 4),
                "win_rate_gap":        round(win_rate_gap, 4),
                "outcome_deviation":   round(outcome_deviation, 4),
                "confidence_bias":     round(confidence_bias, 4),
                "compared_symbols":    symbol_match_results["compared_symbols"],
                "matched_symbols":     symbol_match_results["matched_symbols"],
                "symbol_breakdown":    symbol_match_results["symbol_breakdown"],
                "lookback_days":       lookback_days,
                "analysed_at":         datetime.now().isoformat(),
            }
        }

        self._log_summary(result)
        return result

    # ------------------------------------------------------------------
    # Symbol-level outcome comparison
    # ------------------------------------------------------------------

    def _compute_symbol_outcome_matches(self, signals: list) -> dict:
        """
        For each symbol that has resolved signals, compare its live TP rate
        to the backtest-predicted win rate (loaded from JSON result files).

        A symbol "matches" if its live TP rate >= backtest_win_rate * 0.75.

        Returns:
          {
            "compared_symbols": int,
            "matched_symbols":  int,
            "symbol_breakdown": list[dict]
          }
        """
        # Group signals by symbol
        by_symbol: dict = {}
        for s in signals:
            sym = s.get("symbol", "UNKNOWN")
            by_symbol.setdefault(sym, []).append(s)

        compared  = 0
        matched   = 0
        breakdown = []

        for sym, sym_sigs in by_symbol.items():
            n_sym   = len(sym_sigs)
            tp_sym  = sum(1 for s in sym_sigs if s["outcome"] == "TP_HIT")
            live_tp = tp_sym / n_sym if n_sym > 0 else 0.0

            # Load backtest win rate for this symbol
            bt_win_rate = self._load_backtest_win_rate(sym)

            if bt_win_rate is None:
                # No backtest result for this symbol — skip comparison
                breakdown.append({
                    "symbol":     sym,
                    "n_signals":  n_sym,
                    "live_tp":    round(live_tp, 4),
                    "bt_win_rate": None,
                    "match":      None,
                    "note":       "no backtest result on disk",
                })
                continue

            compared += 1
            threshold = bt_win_rate * 0.75  # allow 25% degradation
            is_match  = live_tp >= (threshold / 100)   # bt_win_rate is in %, live_tp is 0-1

            if is_match:
                matched += 1

            breakdown.append({
                "symbol":      sym,
                "n_signals":   n_sym,
                "live_tp_pct": round(live_tp * 100, 2),
                "bt_win_pct":  round(bt_win_rate, 2),
                "threshold":   round(threshold, 2),
                "match":       is_match,
            })

        return {
            "compared_symbols": compared,
            "matched_symbols":  matched,
            "symbol_breakdown": breakdown,
        }

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _load_resolved_signals(self, cutoff_iso: str) -> list:
        """
        Load all BUY signals with resolved outcomes (TP_HIT, SL_HIT, EXPIRED)
        from the signals table, newer than cutoff_iso.
        """
        try:
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT symbol, action, confidence, ta_score,
                           entry_price, stop_loss, take_profit,
                           outcome, outcome_price, outcome_date, days_to_outcome,
                           timestamp
                    FROM signals
                    WHERE outcome    IS NOT NULL
                      AND outcome    IN ('TP_HIT', 'SL_HIT', 'EXPIRED')
                      AND action     = 'BUY'
                      AND timestamp  >= ?
                    ORDER BY timestamp DESC
                """, (cutoff_iso,)).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"DriftAnalyser: failed to load signals: {e}")
            return []

    def _load_backtest_win_rate(self, symbol: str) -> Optional[float]:
        """
        Load the win_rate_pct from the most recent saved backtest JSON for
        this symbol. Returns None if the file doesn't exist or can't be read.
        """
        path = os.path.join(BACKTEST_RESULTS_DIR, f"{symbol}_backtest.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            wr = data.get("result", {}).get("win_rate_pct")
            return float(wr) if wr is not None else None
        except Exception as e:
            logger.debug(f"DriftAnalyser: could not read backtest for {symbol}: {e}")
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _empty_result(self, reason: str) -> dict:
        return {
            "total_signals":        0,
            "direction_match_rate": 0.0,
            "outcome_match_rate":   0.0,
            "avg_confidence_gap":   0.0,
            "drift_score":          0.0,
            "recommendation":       "OK",
            "details": {
                "note":   reason,
                "analysed_at": datetime.now().isoformat(),
            },
        }

    def _log_summary(self, result: dict) -> None:
        rec = result["recommendation"]
        score = result["drift_score"]
        n = result["total_signals"]
        logger.info(
            f"DriftAnalyser: {n} signals | drift_score={score:.4f} | "
            f"direction_match={result['direction_match_rate']:.2%} | "
            f"conf_gap={result['avg_confidence_gap']:+.4f} | "
            f"recommendation={rec}"
        )
        if rec == "HALT":
            logger.error(
                f"DriftAnalyser HALT: drift={score:.4f} exceeds threshold "
                f"{DRIFT_HALT}. Live strategy has diverged from backtest. "
                "Manual review required."
            )
        elif rec == "RECALIBRATE":
            logger.warning(
                f"DriftAnalyser RECALIBRATE: drift={score:.4f}. "
                "Consider rerunning backtest and updating confidence calibration."
            )

    def print_report(self, result: dict) -> None:
        """Print a human-readable drift report to stdout."""
        sep = "=" * 65
        rec = result["recommendation"]
        rec_flag = " ***" if rec == "HALT" else (" *" if rec == "RECALIBRATE" else "")

        print(f"\n{sep}")
        print(f"  DRIFT ANALYSIS REPORT")
        print(sep)
        print(f"  Total resolved signals : {result['total_signals']}")
        print(f"  Direction match rate   : {result['direction_match_rate']:.2%}")
        print(f"  Outcome match rate     : {result['outcome_match_rate']:.2%}")
        print(f"  Avg confidence gap     : {result['avg_confidence_gap']:+.4f}")
        print(f"  Drift score            : {result['drift_score']:.4f}  (0=none, 1=total)")
        print(f"  Recommendation         : {rec}{rec_flag}")
        print(sep)

        d = result.get("details", {})
        if d:
            print(f"\n  Breakdown:")
            print(f"    TP hits     : {d.get('tp_count', 0)}")
            print(f"    SL hits     : {d.get('sl_count', 0)}")
            print(f"    Expired     : {d.get('expired_count', 0)}")
            print(f"    Stated conf : {d.get('avg_stated_conf', 0):.2%}")
            print(f"    Live TP rate: {d.get('live_tp_rate', 0):.2%}")
            print(f"    Compared syms: {d.get('compared_symbols', 0)}")
            print(f"    Matched syms : {d.get('matched_symbols', 0)}")

            breakdown = d.get("symbol_breakdown", [])
            if breakdown:
                print(f"\n  Per-symbol comparison:")
                hdr = (
                    f"    {'Symbol':<16} {'Live TP%':>9} {'BT Win%':>9} "
                    f"{'Threshold':>10} {'Match':>7}"
                )
                print(hdr)
                print("    " + "-" * 55)
                for row in sorted(breakdown, key=lambda x: x.get("live_tp_pct", 0)):
                    if row.get("bt_win_rate") is None:
                        continue
                    match_str = "YES" if row.get("match") else "NO"
                    print(
                        f"    {row['symbol']:<16} "
                        f"{row.get('live_tp_pct', 0):>9.1f} "
                        f"{row.get('bt_win_pct', 0):>9.1f} "
                        f"{row.get('threshold', 0):>10.1f} "
                        f"{match_str:>7}"
                    )
        print(f"\n{sep}\n")


# =============================================================================
# CLI — python -m backtest.drift_analysis
# =============================================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Signal drift analyser")
    parser.add_argument(
        "--days", type=int, default=90,
        help="Lookback window in days (default 90)"
    )
    args = parser.parse_args()

    analyser = DriftAnalyser()
    result   = analyser.analyse(lookback_days=args.days)
    analyser.print_report(result)
