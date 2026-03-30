# =============================================================================
# analysis/outcome_tracker.py — Signal Outcome Tracker
#
# Runs daily after market close (3:30 PM IST).
# For every signal in the DB that has no outcome yet:
#   - Fetches price history from signal date to today
#   - Checks if TP or SL was hit first, or if the signal expired (>30 days)
#   - Writes outcome back to signals table
#   - Sends Telegram summary of today's outcomes
#
# Outcome values: TP_HIT | SL_HIT | EXPIRED | OPEN
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from utils import get_logger
from utils.telegram import send
from config import SQLITE_DB_FILE

logger = get_logger("OutcomeTracker")

MAX_HOLD_DAYS = 30   # signals older than this with no trigger → EXPIRED


class OutcomeTracker:
    """
    Checks every unresolved signal and marks whether it hit TP, SL, or expired.
    Builds a real-time accuracy score for the strategy.
    """

    def run(self) -> dict:
        """
        Main entry point. Returns summary dict with counts.
        """
        self._migrate()
        pending = self._get_pending_signals()

        if not pending:
            logger.info("OutcomeTracker: no pending signals to check")
            return {"checked": 0, "tp_hit": 0, "sl_hit": 0, "expired": 0, "still_open": 0}

        logger.info(f"OutcomeTracker: checking {len(pending)} pending signals")
        counts = {"tp_hit": 0, "sl_hit": 0, "expired": 0, "still_open": 0}
        resolved_today = []

        for sig in pending:
            outcome, outcome_price, outcome_date, days = self._evaluate(sig)
            if outcome != "OPEN":
                self._write_outcome(sig["id"], outcome, outcome_price, outcome_date, days)
                counts[outcome.lower()] += 1
                resolved_today.append({
                    "symbol":        sig["symbol"],
                    "outcome":       outcome,
                    "entry":         sig["entry_price"],
                    "outcome_price": outcome_price,
                    "pnl_pct":       round((outcome_price - sig["entry_price"])
                                          / sig["entry_price"] * 100, 2)
                                     if outcome_price else 0,
                    "days":          days,
                })
            else:
                counts["still_open"] += 1

        counts["checked"] = len(pending)
        logger.info(f"OutcomeTracker: TP={counts['tp_hit']} SL={counts['sl_hit']} "
                    f"EXPIRED={counts['expired']} OPEN={counts['still_open']}")

        if resolved_today:
            self._send_telegram(resolved_today)

        return counts

    # ------------------------------------------------------------------
    # Core evaluation
    # ------------------------------------------------------------------

    def _evaluate(self, sig: dict) -> tuple:
        """
        Returns (outcome, outcome_price, outcome_date, days_held).
        outcome ∈ {TP_HIT, SL_HIT, EXPIRED, OPEN}
        """
        symbol     = sig["symbol"]
        entry_px   = sig["entry_price"]
        sl         = sig["stop_loss"]
        tp         = sig["take_profit"]
        sig_date   = sig["timestamp"][:10]   # "YYYY-MM-DD"
        today      = datetime.now().strftime("%Y-%m-%d")
        days_since = (datetime.now() - datetime.fromisoformat(sig["timestamp"])).days

        # Expired — too old with no trigger
        if days_since > MAX_HOLD_DAYS:
            try:
                curr = self._latest_price(symbol)
                return ("EXPIRED", curr, today, days_since)
            except Exception:
                return ("EXPIRED", entry_px, today, days_since)

        # Fetch daily OHLC from signal date onward
        try:
            df = yf.Ticker(f"{symbol}.NS").history(
                start=sig_date, end=today, interval="1d", auto_adjust=True
            )
            if df.empty or len(df) < 1:
                return ("OPEN", None, None, days_since)

            df.columns = [c.lower() for c in df.columns]

            # Walk through each day since signal — check high/low for TP/SL
            for idx, row in df.iterrows():
                date_str = str(idx.date()) if hasattr(idx, "date") else str(idx)[:10]
                lo = row["low"]
                hi = row["high"]

                # Stop-loss hit (low touches SL)
                if sl and lo <= sl:
                    days = (datetime.fromisoformat(date_str) -
                            datetime.fromisoformat(sig_date)).days
                    return ("SL_HIT", round(sl, 2), date_str, days)

                # Take-profit hit (high touches TP)
                if tp and hi >= tp:
                    days = (datetime.fromisoformat(date_str) -
                            datetime.fromisoformat(sig_date)).days
                    return ("TP_HIT", round(tp, 2), date_str, days)

            # Still running — no trigger yet
            return ("OPEN", None, None, days_since)

        except Exception as e:
            logger.debug(f"{symbol} outcome check failed: {e}")
            return ("OPEN", None, None, days_since)

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _migrate(self):
        """Add outcome columns to signals table if they don't exist yet."""
        if not os.path.exists(SQLITE_DB_FILE):
            return
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(signals)").fetchall()]
            if "outcome" not in cols:
                conn.execute("ALTER TABLE signals ADD COLUMN outcome TEXT DEFAULT NULL")
                logger.info("OutcomeTracker: added 'outcome' column to signals table")
            if "outcome_price" not in cols:
                conn.execute("ALTER TABLE signals ADD COLUMN outcome_price REAL DEFAULT NULL")
            if "outcome_date" not in cols:
                conn.execute("ALTER TABLE signals ADD COLUMN outcome_date TEXT DEFAULT NULL")
            if "days_to_outcome" not in cols:
                conn.execute("ALTER TABLE signals ADD COLUMN days_to_outcome INTEGER DEFAULT NULL")

    def _get_pending_signals(self) -> list[dict]:
        """Return all signals without an outcome yet (BUY signals only, not too old)."""
        if not os.path.exists(SQLITE_DB_FILE):
            return []
        cutoff = (datetime.now() - timedelta(days=MAX_HOLD_DAYS + 5)).isoformat()
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT id, symbol, action, entry_price, stop_loss, take_profit,
                       confidence, ta_score, timestamp
                FROM signals
                WHERE outcome IS NULL
                  AND action = 'BUY'
                  AND entry_price IS NOT NULL
                  AND stop_loss   IS NOT NULL
                  AND take_profit IS NOT NULL
                  AND timestamp   >= ?
                ORDER BY timestamp DESC
            """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]

    def _write_outcome(self, signal_id: int, outcome: str, price, date, days):
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            conn.execute("""
                UPDATE signals
                SET outcome=?, outcome_price=?, outcome_date=?, days_to_outcome=?
                WHERE id=?
            """, (outcome, price, date, days, signal_id))

    def _latest_price(self, symbol: str) -> float:
        hist = yf.Ticker(f"{symbol}.NS").history(period="2d", interval="1d", auto_adjust=True)
        return float(hist["Close"].iloc[-1]) if not hist.empty else 0.0

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _send_telegram(self, resolved: list):
        """Send a Telegram message summarising today's resolved signals."""
        tp_list  = [r for r in resolved if r["outcome"] == "TP_HIT"]
        sl_list  = [r for r in resolved if r["outcome"] == "SL_HIT"]
        exp_list = [r for r in resolved if r["outcome"] == "EXPIRED"]

        lines = ["*Signal Outcomes — Today*", ""]

        if tp_list:
            lines.append(f"TARGET HIT ({len(tp_list)})")
            for r in tp_list:
                lines.append(f"  {r['symbol']}  {r['pnl_pct']:+.1f}%  "
                             f"({r['days']}d)  entry Rs.{r['entry']:,.0f}")

        if sl_list:
            lines.append(f"STOP HIT ({len(sl_list)})")
            for r in sl_list:
                lines.append(f"  {r['symbol']}  {r['pnl_pct']:+.1f}%  "
                             f"({r['days']}d)  entry Rs.{r['entry']:,.0f}")

        if exp_list:
            lines.append(f"EXPIRED ({len(exp_list)})")
            for r in exp_list:
                lines.append(f"  {r['symbol']}  {r['pnl_pct']:+.1f}%  expired")

        send("\n".join(lines))

    # ------------------------------------------------------------------
    # Analytics — called by dashboard
    # ------------------------------------------------------------------

    @staticmethod
    def get_stats() -> dict:
        """Return overall signal accuracy stats for the dashboard."""
        if not os.path.exists(SQLITE_DB_FILE):
            return {}
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            rows = conn.execute("""
                SELECT outcome, COUNT(*) as cnt,
                       AVG(days_to_outcome) as avg_days
                FROM signals
                WHERE outcome IS NOT NULL
                GROUP BY outcome
            """).fetchall()

        totals = {r[0]: {"count": r[1], "avg_days": round(r[2] or 0, 1)}
                  for r in rows}
        total  = sum(v["count"] for v in totals.values())
        tp     = totals.get("TP_HIT", {}).get("count", 0)
        sl     = totals.get("SL_HIT", {}).get("count", 0)
        exp    = totals.get("EXPIRED", {}).get("count", 0)

        return {
            "total_resolved": total,
            "tp_count":       tp,
            "sl_count":       sl,
            "expired_count":  exp,
            "tp_rate_pct":    round(tp / total * 100, 1) if total else 0,
            "sl_rate_pct":    round(sl / total * 100, 1) if total else 0,
            "avg_days_tp":    totals.get("TP_HIT",  {}).get("avg_days", 0),
            "avg_days_sl":    totals.get("SL_HIT",  {}).get("avg_days", 0),
        }

    @staticmethod
    def get_recent_outcomes(limit: int = 50) -> list[dict]:
        """Return most recent resolved signals with outcome, for dashboard table."""
        if not os.path.exists(SQLITE_DB_FILE):
            return []
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT symbol, action, confidence, entry_price,
                       stop_loss, take_profit, ta_score, sentiment,
                       outcome, outcome_price, outcome_date, days_to_outcome,
                       timestamp
                FROM signals
                WHERE outcome IS NOT NULL
                ORDER BY outcome_date DESC, timestamp DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    tracker = OutcomeTracker()
    result  = tracker.run()
    print("Outcome check complete:", result)
    stats = OutcomeTracker.get_stats()
    print("Overall accuracy:", stats)
