# =============================================================================
# analysis/setup_tracker.py — Per-setup win-rate tracker with Wilson 95% LB
#
# Reads resolved paper trades from the signals table, groups by setup_type,
# and evaluates Phase-3 statistical gates from docs/TURNAROUND_PLAN.md:
#
#   KILL       ≥30 trades AND Wilson 95% LB < breakeven WR for R:R
#   PROBATION  ≥20 trades AND expectancy < -0.1R
#   SCALE      ≥50 trades AND Wilson 95% LB > breakeven WR + 5pp
#   OK         enough data, no concerning signal
#   INSUFFICIENT_DATA  < 20 resolved trades
# =============================================================================
import math
import sqlite3
from dataclasses import dataclass
from typing import Optional

from config import SQLITE_DB_FILE
from utils import get_logger

logger = get_logger("SetupTracker")

KNOWN_SETUPS = ("technical_base", "breakout_52w", "rsi2_mean_reversion")

_MIN_TRADES_PROBATION = 20
_MIN_TRADES_KILL      = 30
_MIN_TRADES_SCALE     = 50
_DEFAULT_RR           = 2.0
_SCALE_MARGIN         = 0.05   # Wilson LB must beat breakeven by this to SCALE
_PROBATION_THRESHOLD  = -0.10  # expectancy in R below which PROBATION fires


def wilson_lower_bound(wins: int, n: int, z: float = 1.96) -> float:
    """95% Wilson score lower confidence bound for a binomial proportion."""
    if n == 0:
        return 0.0
    p_hat = wins / n
    denom  = 1 + z * z / n
    centre = p_hat + z * z / (2 * n)
    margin = z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def breakeven_win_rate(rr: float) -> float:
    """Minimum win rate to break even: 1 / (1 + rr)."""
    return 1.0 / (1.0 + max(rr, 0.01))


@dataclass
class SetupStats:
    setup: str
    n_trades: int
    n_wins: int
    win_rate: float
    wilson_lb_95: float
    breakeven_wr: float
    expectancy_r: float
    avg_rr: float
    verdict: str
    verdict_reason: str


class SetupTracker:
    """
    Loads resolved signals (outcome IN ('TP_HIT','SL_HIT')) from the DB,
    groups by setup_type, and runs Phase-3 statistical gates.
    """

    def __init__(self, db_path: str = SQLITE_DB_FILE):
        self.db_path = db_path

    def evaluate_all(self) -> dict:
        """Return {setup_name: SetupStats} for all setups found in the DB,
        plus any KNOWN_SETUPS that have zero trades."""
        rows   = self._load_rows()
        bucket: dict = {}
        for row in rows:
            s = (row.get("setup_type") or "technical_base").strip() or "technical_base"
            bucket.setdefault(s, []).append(row)

        result = {}
        seen = set()
        for setup in KNOWN_SETUPS:
            result[setup] = self._evaluate(setup, bucket.get(setup, []))
            seen.add(setup)
        for setup, trades in bucket.items():
            if setup not in seen:
                result[setup] = self._evaluate(setup, trades)
        return result

    def evaluate(self, setup: str) -> SetupStats:
        """Evaluate a single named setup."""
        rows = self._load_rows(setup_filter=setup)
        return self._evaluate(setup, rows)

    # ------------------------------------------------------------------

    def _load_rows(self, setup_filter: Optional[str] = None) -> list:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cols = {r[1] for r in conn.execute("PRAGMA table_info(signals)").fetchall()}
                if "outcome" not in cols:
                    return []

                extra  = ""
                if "setup_type"   in cols: extra += ", setup_type"
                if "pnl"          in cols: extra += ", pnl"
                if "risk_reward"  in cols: extra += ", risk_reward"

                where = "WHERE outcome IN ('TP_HIT','SL_HIT')"
                params: tuple = ()
                if setup_filter and "setup_type" in cols:
                    where  += " AND setup_type = ?"
                    params  = (setup_filter,)

                rows = conn.execute(
                    f"SELECT outcome {extra} FROM signals {where}", params
                ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.Error as exc:
            logger.warning(f"SetupTracker DB read failed: {exc}")
            return []

    def _evaluate(self, setup: str, trades: list) -> SetupStats:
        n    = len(trades)
        wins = sum(1 for t in trades if t.get("outcome") == "TP_HIT")

        win_pnls  = [abs(float(t["pnl"])) for t in trades
                     if t.get("outcome") == "TP_HIT" and t.get("pnl")]
        loss_pnls = [abs(float(t["pnl"])) for t in trades
                     if t.get("outcome") == "SL_HIT" and t.get("pnl")]

        avg_win  = sum(win_pnls)  / len(win_pnls)  if win_pnls  else 0.0
        avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0.0
        rr       = avg_win / avg_loss if avg_loss > 0 else _DEFAULT_RR

        win_rate   = wins / n if n > 0 else 0.0
        wilson_lb  = wilson_lower_bound(wins, n)
        bk_wr      = breakeven_win_rate(rr)
        expectancy = win_rate * rr - (1 - win_rate) * 1.0

        verdict, reason = self._gate(n, win_rate, wilson_lb, bk_wr, expectancy)
        return SetupStats(
            setup=setup,
            n_trades=n,
            n_wins=wins,
            win_rate=round(win_rate, 4),
            wilson_lb_95=round(wilson_lb, 4),
            breakeven_wr=round(bk_wr, 4),
            expectancy_r=round(expectancy, 4),
            avg_rr=round(rr, 2),
            verdict=verdict,
            verdict_reason=reason,
        )

    @staticmethod
    def _gate(n: int, win_rate: float, wilson_lb: float,
              bk_wr: float, expectancy: float) -> tuple:
        if n < _MIN_TRADES_PROBATION:
            return (
                "INSUFFICIENT_DATA",
                f"{n} trades (need ≥{_MIN_TRADES_PROBATION} for PROBATION gate)",
            )
        if n >= _MIN_TRADES_KILL and wilson_lb < bk_wr:
            return (
                "KILL",
                f"Wilson 95% LB {wilson_lb:.1%} < breakeven {bk_wr:.1%} at {n} trades",
            )
        if n >= _MIN_TRADES_SCALE and wilson_lb > (bk_wr + _SCALE_MARGIN):
            return (
                "SCALE",
                f"Wilson 95% LB {wilson_lb:.1%} > breakeven+5pp {bk_wr+_SCALE_MARGIN:.1%} at {n} trades",
            )
        if expectancy < _PROBATION_THRESHOLD:
            return (
                "PROBATION",
                f"Expectancy {expectancy:+.3f}R < {_PROBATION_THRESHOLD:+.1f}R threshold at {n} trades",
            )
        return (
            "OK",
            f"n={n}, WR={win_rate:.1%}, Wilson LB={wilson_lb:.1%}, "
            f"expectancy={expectancy:+.3f}R, breakeven={bk_wr:.1%}",
        )
