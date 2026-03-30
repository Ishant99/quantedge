# =============================================================================
# analysis/performance_attribution.py — Indicator Performance Attribution
#
# Analyses the signal history in SQLite to answer:
#   "Which of the 8 TA indicators actually predict winning trades?"
#
# For each resolved signal (TP_HIT / SL_HIT), reads the indicators stored
# in the reasoning text and correlates them with the outcome.
#
# Output: ranked table of indicators by win-rate contribution.
# Called from: dashboard RESEARCH tab + weekly summary.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import re
from collections import defaultdict
from config import SQLITE_DB_FILE
from utils import get_logger

logger = get_logger("PerfAttrib")

# Indicator keywords to scan for in reasoning text
INDICATOR_PATTERNS = {
    "RSI":        [r"rsi", r"rsi\s+\d", r"oversold", r"overbought"],
    "MACD":       [r"macd", r"macd crossover", r"macd above signal"],
    "EMA":        [r"ema", r"above ema", r"ema uptrend", r"above all ema"],
    "Bollinger":  [r"bollinger", r"bb", r"lower band", r"squeeze"],
    "Volume":     [r"volume breakout", r"volume surge", r"high volume"],
    "ADX":        [r"adx", r"strong trend", r"trend strength"],
    "Stochastic": [r"stoch", r"stochastic", r"k\s*>\s*d"],
    "OBV":        [r"obv", r"accumulation", r"obv bullish"],
    "Pattern":    [r"pattern", r"breakout", r"support", r"resistance"],
    "Sentiment":  [r"sentiment", r"positive news", r"negative news"],
}


class PerformanceAttributor:
    """
    Reads resolved signals from DB and builds per-indicator win rates.
    """

    def run(self) -> dict:
        """
        Returns dict:
          {
            "indicator_stats": {
              "RSI":   {"signals": 12, "wins": 8, "losses": 4, "win_rate": 66.7},
              ...
            },
            "ranked": [("RSI", 66.7), ("MACD", 60.0), ...],
            "total_resolved": 20,
            "overall_win_rate": 55.0,
          }
        """
        signals = self._load_resolved_signals()
        if not signals:
            logger.info("PerfAttrib: no resolved signals yet")
            return {}

        counts = defaultdict(lambda: {"signals": 0, "wins": 0, "losses": 0})

        for sig in signals:
            reasoning = (sig.get("reasoning") or "").lower()
            outcome   = sig.get("outcome", "")
            is_win    = outcome == "TP_HIT"

            for indicator, patterns in INDICATOR_PATTERNS.items():
                mentioned = any(re.search(p, reasoning) for p in patterns)
                if mentioned:
                    counts[indicator]["signals"] += 1
                    if is_win:
                        counts[indicator]["wins"] += 1
                    else:
                        counts[indicator]["losses"] += 1

        # Build win rates
        stats = {}
        for ind, c in counts.items():
            n = c["signals"]
            if n >= 2:   # need at least 2 data points
                stats[ind] = {
                    "signals":  n,
                    "wins":     c["wins"],
                    "losses":   c["losses"],
                    "win_rate": round(c["wins"] / n * 100, 1),
                }

        ranked = sorted(stats.items(), key=lambda x: x[1]["win_rate"], reverse=True)

        total    = len(signals)
        wins     = sum(1 for s in signals if s.get("outcome") == "TP_HIT")
        wr_overall = round(wins / total * 100, 1) if total else 0

        logger.info(f"PerfAttrib: {total} signals, {len(stats)} indicators analysed")
        return {
            "indicator_stats":  stats,
            "ranked":           ranked,
            "total_resolved":   total,
            "overall_win_rate": wr_overall,
        }

    def _load_resolved_signals(self) -> list:
        if not os.path.exists(SQLITE_DB_FILE):
            return []
        try:
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT symbol, action, confidence, ta_score,
                           sentiment, reasoning, outcome, outcome_date
                    FROM signals
                    WHERE outcome IN ('TP_HIT','SL_HIT')
                    ORDER BY outcome_date DESC
                """).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"PerfAttrib DB read failed: {e}")
            return []

    @staticmethod
    def get_cached() -> dict:
        """
        Lightweight version for dashboard — runs attribution and returns result.
        In production this would be cached; here we compute on demand.
        """
        return PerformanceAttributor().run()
