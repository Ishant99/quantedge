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
from config import SQLITE_DB_FILE, OUTCOME_MAX_HOLD_DAYS

logger = get_logger("OutcomeTracker")

MAX_HOLD_DAYS = OUTCOME_MAX_HOLD_DAYS


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

        # Also update F&O, Crypto, US paper positions that have hit TP/SL
        self._check_fno_positions()
        self._check_crypto_positions()
        self._check_us_positions()

        # Update multi-horizon returns for all journalled signals
        try:
            from memory.portfolio_memory import PortfolioMemory
            mem = PortfolioMemory()
            self.track_journal_outcomes(mem)
        except Exception as e:
            logger.warning(f"track_journal_outcomes skipped: {e}")

        return counts

    def _check_fno_positions(self):
        """Trigger F&O monitor to close any expired/TP/SL positions."""
        try:
            from execution.brokers.fno_paper_broker import FNOPaperBroker
            broker = FNOPaperBroker()
            closed = broker.monitor_and_exit() + broker.monitor_futures() + broker.monitor_selling()
            if closed:
                logger.info(f"OutcomeTracker: closed {len(closed)} F&O positions via expiry/TP/SL")
        except Exception as e:
            logger.debug(f"F&O check skipped: {e}")

    def _check_crypto_positions(self):
        """Trigger crypto monitor."""
        try:
            from execution.brokers.crypto_paper_broker import CryptoPaperBroker
            closed = CryptoPaperBroker().monitor_and_exit()
            if closed:
                logger.info(f"OutcomeTracker: closed {len(closed)} crypto positions")
        except Exception as e:
            logger.debug(f"Crypto check skipped: {e}")

    def _check_us_positions(self):
        """Trigger US stocks monitor."""
        try:
            from execution.brokers.us_paper_broker import USPaperBroker
            closed = USPaperBroker().monitor_and_exit()
            if closed:
                logger.info(f"OutcomeTracker: closed {len(closed)} US positions")
        except Exception as e:
            logger.debug(f"US check skipped: {e}")

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
                sl_hit = bool(sl and lo <= sl)
                tp_hit = bool(tp and hi >= tp)

                if sl_hit and tp_hit:
                    intraday = self._resolve_intraday_sequence(symbol, date_str, sl, tp)
                    if intraday:
                        outcome, price = intraday
                        days = (datetime.fromisoformat(date_str) -
                                datetime.fromisoformat(sig_date)).days
                        return (outcome, round(price, 2), date_str, days)

                # Stop-loss hit (low touches SL)
                if sl_hit:
                    days = (datetime.fromisoformat(date_str) -
                            datetime.fromisoformat(sig_date)).days
                    return ("SL_HIT", round(sl, 2), date_str, days)

                # Take-profit hit (high touches TP)
                if tp_hit:
                    days = (datetime.fromisoformat(date_str) -
                            datetime.fromisoformat(sig_date)).days
                    return ("TP_HIT", round(tp, 2), date_str, days)

            # Still running — no trigger yet
            return ("OPEN", None, None, days_since)

        except Exception as e:
            logger.debug(f"{symbol} outcome check failed: {e}")
            return ("OPEN", None, None, days_since)

    def _resolve_intraday_sequence(self, symbol: str, date_str: str, sl: float, tp: float):
        """Use 15m bars on ambiguous days to see which level was hit first."""
        try:
            start = datetime.fromisoformat(date_str)
            end = start + timedelta(days=1)
            intraday = yf.Ticker(f"{symbol}.NS").history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval="15m",
                auto_adjust=True,
            )
            if intraday.empty:
                return None
            intraday.columns = [c.lower() for c in intraday.columns]
            for _, row in intraday.iterrows():
                lo = row["low"]
                hi = row["high"]
                if lo <= sl:
                    return ("SL_HIT", sl)
                if hi >= tp:
                    return ("TP_HIT", tp)
        except Exception as e:
            logger.debug(f"{symbol} intraday sequence check failed: {e}")
        return None

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
            if "outcome_1d" not in cols:
                conn.execute("ALTER TABLE signals ADD COLUMN outcome_1d REAL DEFAULT NULL")
                logger.info("OutcomeTracker: added 'outcome_1d' column to signals table")
            if "outcome_3d" not in cols:
                conn.execute("ALTER TABLE signals ADD COLUMN outcome_3d REAL DEFAULT NULL")
                logger.info("OutcomeTracker: added 'outcome_3d' column to signals table")
            if "outcome_5d" not in cols:
                conn.execute("ALTER TABLE signals ADD COLUMN outcome_5d REAL DEFAULT NULL")
                logger.info("OutcomeTracker: added 'outcome_5d' column to signals table")

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

    def _price_at_date(self, symbol: str, date_str: str) -> float:
        """
        Fetch the closing price for a given symbol on or after date_str (up to +4 calendar days
        to account for weekends/holidays).  Returns 0.0 if no data is available.
        """
        try:
            start = datetime.fromisoformat(date_str)
            end   = start + timedelta(days=6)
            hist  = yf.Ticker(f"{symbol}.NS").history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval="1d",
                auto_adjust=True,
            )
            if hist.empty:
                return 0.0
            # Return the first close on or after date_str
            return float(hist["Close"].iloc[0])
        except Exception as e:
            logger.debug(f"_price_at_date({symbol}, {date_str}) failed: {e}")
            return 0.0

    # ------------------------------------------------------------------
    # Multi-horizon journal outcome tracking
    # ------------------------------------------------------------------

    def track_journal_outcomes(self, memory) -> int:
        """
        For every signal from the last 30 days that has a decision_journals row,
        compute the return at 1d, 3d, and 5d from signal date and write the
        values back via memory.update_journal_outcome().

        Returns the number of signals processed.
        """
        if not os.path.exists(SQLITE_DB_FILE):
            logger.debug("track_journal_outcomes: DB not found, skipping")
            return 0

        cutoff = (datetime.now() - timedelta(days=30)).isoformat()

        try:
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT s.id        AS signal_id,
                           s.symbol    AS symbol,
                           s.entry_price AS entry_price,
                           s.timestamp AS sig_ts,
                           dj.outcome_1d,
                           dj.outcome_3d,
                           dj.outcome_5d
                    FROM signals s
                    JOIN decision_journals dj ON dj.signal_id = s.id
                    WHERE s.timestamp >= ?
                      AND s.action = 'BUY'
                      AND s.entry_price IS NOT NULL
                    ORDER BY s.timestamp DESC
                """, (cutoff,)).fetchall()
        except Exception as e:
            logger.warning(f"track_journal_outcomes: query failed: {e}")
            return 0

        if not rows:
            logger.info("track_journal_outcomes: no journalled signals in last 30 days")
            return 0

        processed = 0
        for row in rows:
            signal_id  = row["signal_id"]
            symbol     = row["symbol"]
            entry_px   = row["entry_price"]
            sig_date   = row["sig_ts"][:10]   # "YYYY-MM-DD"

            if entry_px is None or entry_px == 0:
                continue

            for horizon, days_offset in (("1d", 1), ("3d", 3), ("5d", 5)):
                # Skip horizons already recorded
                existing_key = f"outcome_{horizon.replace('d', 'd')}"
                if row[existing_key] is not None:
                    continue

                # Compute the target calendar date
                target_date = (
                    datetime.fromisoformat(sig_date) + timedelta(days=days_offset)
                ).strftime("%Y-%m-%d")

                # Don't fill future horizons yet
                if datetime.fromisoformat(target_date).date() > datetime.now().date():
                    continue

                price = self._price_at_date(symbol, target_date)
                if price and price > 0:
                    return_pct = round((price - entry_px) / entry_px * 100, 4)
                    try:
                        memory.update_journal_outcome(signal_id, horizon, return_pct)
                        logger.debug(
                            f"track_journal_outcomes: {symbol} signal {signal_id} "
                            f"{horizon} return={return_pct:+.2f}%"
                        )
                    except Exception as e:
                        logger.warning(
                            f"track_journal_outcomes: update failed for signal {signal_id}: {e}"
                        )

            processed += 1

        logger.info(f"track_journal_outcomes: processed {processed} signals")
        return processed

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _send_telegram(self, resolved: list):
        """Send a Telegram message summarising today's resolved signals."""
        tp_list  = [r for r in resolved if r["outcome"] == "TP_HIT"]
        sl_list  = [r for r in resolved if r["outcome"] == "SL_HIT"]
        exp_list = [r for r in resolved if r["outcome"] == "EXPIRED"]

        lines = ["*📋 Trade Outcomes — Today*", ""]

        if tp_list:
            lines.append(f"🎯 *Target reached ({len(tp_list)})*")
            for r in tp_list:
                lines.append(
                    f"  ✅ {r['symbol']}  `{r['pnl_pct']:+.1f}%`  "
                    f"held {r['days']}d  entry ₹{r['entry']:,.0f}"
                )

        if sl_list:
            lines.append(f"\n🛑 *Stop loss hit ({len(sl_list)})*")
            for r in sl_list:
                lines.append(
                    f"  🔴 {r['symbol']}  `{r['pnl_pct']:+.1f}%`  "
                    f"held {r['days']}d  entry ₹{r['entry']:,.0f}"
                )

        if exp_list:
            lines.append(f"\n⏱ *Timed out — held too long ({len(exp_list)})*")
            for r in exp_list:
                lines.append(f"  ➡️ {r['symbol']}  `{r['pnl_pct']:+.1f}%`  closed at time limit")

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
