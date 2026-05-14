# =============================================================================
# research/promotion_checklist.py — Gates for promoting research → production
#
# All gates must pass before a new module or parameter change is deployed.
#
# Usage:
#   from research.promotion_checklist import PromotionChecklist
#
#   checklist = PromotionChecklist()
#   result = checklist.check(
#       backtest_result=bt,
#       ablation_results=ablations,
#       paper_days=14,
#   )
#   checklist.print_report(result)
#   if result["passed"]:
#       # promote to production
# =============================================================================

from __future__ import annotations

import os
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class ChecklistItem:
    """Result for a single promotion gate."""
    name:      str   # gate identifier key
    passed:    bool  # True if gate is satisfied
    value:     Any   # measured value
    threshold: Any   # required threshold
    message:   str   # human-readable summary


# ---------------------------------------------------------------------------
# Task 7.2 — PromotionReport + PromotionChecker
# ---------------------------------------------------------------------------

PROMOTION_REQUIREMENTS = [
    "ablation_test_shows_positive_edge",   # Phase 5 ablation result > 0 Sharpe delta
    "paper_trades_count >= 30",            # run for ≥ 30 paper trades
    "paper_expectancy > 0",               # positive expected value
    "readiness_checker_passes",            # readiness/checker.py validation
    "manual_approval_logged",              # operator approval recorded with date
]


@dataclass
class PromotionReport:
    """Per-module promotion status report."""
    module_name:   str
    evaluated_at:  str = field(default_factory=lambda: datetime.now().isoformat())
    requirements:  dict = field(default_factory=dict)  # {req: bool}
    notes:         dict = field(default_factory=dict)  # {req: str note}
    overall_pass:  bool = False

    def summary(self) -> str:
        passed  = sum(1 for v in self.requirements.values() if v)
        total   = len(self.requirements)
        verdict = "APPROVED" if self.overall_pass else "BLOCKED"
        return f"{verdict} — {passed}/{total} requirements met for '{self.module_name}'"


class PromotionChecker:
    """
    Checks whether a research module satisfies all PROMOTION_REQUIREMENTS.

    Data sources (all optional — gate fails gracefully if absent):
      - research/research.db  : paper trade counts and expectancy per module
      - logs/trades.db        : readiness_checker output
      - logs/readiness_report.json : cached readiness result
      - research/approvals.json   : manually logged approvals

    Usage::

        report = PromotionChecker().evaluate("new_volume_analyzer")
        print(report.summary())
    """

    _APPROVALS_FILE = os.path.join(_BASE_DIR, "research", "approvals.json")
    _READINESS_FILE = os.path.join(_BASE_DIR, "logs", "readiness_report.json")
    _RESEARCH_DB    = os.path.join(_BASE_DIR, "research", "research.db")

    def evaluate(self, module_name: str) -> PromotionReport:
        """
        Evaluate all PROMOTION_REQUIREMENTS for *module_name*.

        Args:
            module_name: The name used when the module was registered in the
                         research pipeline (matches ablation config keys and
                         approval records).

        Returns:
            PromotionReport with per-requirement pass/fail and notes.
        """
        reqs:  dict[str, bool] = {}
        notes: dict[str, str]  = {}

        # --- 1. ablation_test_shows_positive_edge ---
        ablation_pass, ablation_note = self._check_ablation_edge(module_name)
        reqs["ablation_test_shows_positive_edge"] = ablation_pass
        notes["ablation_test_shows_positive_edge"] = ablation_note

        # --- 2. paper_trades_count >= 30 ---
        trade_count, trade_note = self._check_paper_trades(module_name)
        reqs["paper_trades_count >= 30"] = trade_count >= 30
        notes["paper_trades_count >= 30"] = trade_note

        # --- 3. paper_expectancy > 0 ---
        ev_pass, ev_note = self._check_paper_expectancy(module_name)
        reqs["paper_expectancy > 0"] = ev_pass
        notes["paper_expectancy > 0"] = ev_note

        # --- 4. readiness_checker_passes ---
        ready_pass, ready_note = self._check_readiness()
        reqs["readiness_checker_passes"] = ready_pass
        notes["readiness_checker_passes"] = ready_note

        # --- 5. manual_approval_logged ---
        approval_pass, approval_note = self._check_manual_approval(module_name)
        reqs["manual_approval_logged"] = approval_pass
        notes["manual_approval_logged"] = approval_note

        overall = all(reqs.values())
        return PromotionReport(
            module_name=module_name,
            requirements=reqs,
            notes=notes,
            overall_pass=overall,
        )

    def log_approval(self, module_name: str, operator: str, note: str = "") -> None:
        """Record a manual approval for *module_name*."""
        approvals: dict = {}
        if os.path.exists(self._APPROVALS_FILE):
            try:
                with open(self._APPROVALS_FILE) as f:
                    approvals = json.load(f)
            except Exception:
                pass
        approvals[module_name] = {
            "approved_at": datetime.now().isoformat(),
            "operator": operator,
            "note": note,
        }
        os.makedirs(os.path.dirname(self._APPROVALS_FILE), exist_ok=True)
        with open(self._APPROVALS_FILE, "w") as f:
            json.dump(approvals, f, indent=2)

    # ------------------------------------------------------------------
    # Individual requirement checks
    # ------------------------------------------------------------------

    def _check_ablation_edge(self, module_name: str) -> tuple[bool, str]:
        ablations_dir = os.path.join(_BASE_DIR, "research", "ablations")
        if not os.path.exists(ablations_dir):
            return False, "No ablation results found — run ablation first"
        for fname in os.listdir(ablations_dir):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(ablations_dir, fname)) as f:
                    data = json.load(f)
                if data.get("module") == module_name:
                    delta = float(data.get("sharpe_delta", -1))
                    if delta > 0:
                        return True, f"Ablation Sharpe delta = +{delta:.3f}"
                    return False, f"Ablation Sharpe delta = {delta:.3f} (need > 0)"
            except Exception:
                continue
        return False, f"No ablation result found for '{module_name}'"

    def _check_paper_trades(self, module_name: str) -> tuple[int, str]:
        if not os.path.exists(self._RESEARCH_DB):
            return 0, "research.db not found — run sandbox pipeline first"
        try:
            with sqlite3.connect(self._RESEARCH_DB) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM research_signals WHERE ablation_config LIKE ?",
                    (f"%{module_name}%",),
                ).fetchone()
                count = row[0] if row else 0
            return count, f"{count} paper trades logged for '{module_name}' (need >= 30)"
        except Exception as exc:
            return 0, f"DB query failed: {exc}"

    def _check_paper_expectancy(self, module_name: str) -> tuple[bool, str]:
        if not os.path.exists(self._RESEARCH_DB):
            return False, "research.db not found"
        try:
            with sqlite3.connect(self._RESEARCH_DB) as conn:
                rows = conn.execute(
                    "SELECT expected_value FROM research_signals "
                    "WHERE ablation_config LIKE ? AND expected_value IS NOT NULL",
                    (f"%{module_name}%",),
                ).fetchall()
            if not rows:
                return False, f"No EV data for '{module_name}'"
            avg_ev = sum(r[0] for r in rows) / len(rows)
            return avg_ev > 0, f"Avg paper EV = {avg_ev:.4f} ({'positive' if avg_ev > 0 else 'negative'})"
        except Exception as exc:
            return False, f"EV query failed: {exc}"

    def _check_readiness(self) -> tuple[bool, str]:
        if not os.path.exists(self._READINESS_FILE):
            return False, "readiness_report.json not found — run ReadinessChecker first"
        try:
            with open(self._READINESS_FILE) as f:
                data = json.load(f)
            is_ready = data.get("is_ready", False)
            passed   = data.get("passed", 0)
            total    = data.get("total", 0)
            ts       = data.get("timestamp", "unknown")
            return is_ready, f"ReadinessChecker {passed}/{total} gates passed (run at {ts})"
        except Exception as exc:
            return False, f"readiness_report.json parse failed: {exc}"

    def _check_manual_approval(self, module_name: str) -> tuple[bool, str]:
        if not os.path.exists(self._APPROVALS_FILE):
            return False, f"No approvals file — call PromotionChecker().log_approval('{module_name}', operator='you')"
        try:
            with open(self._APPROVALS_FILE) as f:
                approvals = json.load(f)
            rec = approvals.get(module_name)
            if rec:
                return True, f"Approved by {rec.get('operator','?')} at {rec.get('approved_at','?')}"
            return False, f"No manual approval for '{module_name}'"
        except Exception as exc:
            return False, f"approvals.json parse failed: {exc}"
    value:     Any   # measured value
    threshold: Any   # required threshold
    message:   str   # human-readable summary


class PromotionChecklist:
    """
    Gates for promoting a research config/module to production.
    All gates must pass before a new module or parameter change is deployed.

    Gate catalogue
    ──────────────
    Performance gates:
        min_win_rate     Win rate >= 55 %
        min_trades       At least 30 trades in backtest
        positive_ev      Expected value > 0
        max_drawdown     Max drawdown < 15 %

    Safety gates:
        no_look_ahead    No look-ahead bias detected
        out_of_sample    Tested on out-of-sample data
        paper_validated  At least 2 weeks of paper trading

    Stability gates:
        regime_coverage  Tested in at least 2 different regimes
        min_sharpe       Sharpe ratio > 0.5
    """

    GATES = [
        # (key,              description,                         threshold)
        ("min_win_rate",    "Win rate >= 55%",                   0.55),
        ("min_trades",      "At least 30 trades",                30),
        ("positive_ev",     "Expected value > 0",                0.0),
        ("max_drawdown",    "Max drawdown < 15%",                0.15),
        ("no_look_ahead",   "No look-ahead bias",                True),
        ("out_of_sample",   "Tested on OOS data",                True),
        ("paper_validated", "2 weeks paper trading",             True),
        ("regime_coverage", "Tested in 2+ regimes",              2),
        ("min_sharpe",      "Sharpe > 0.5",                      0.5),
    ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        backtest_result=None,
        ablation_results=None,
        paper_days: int = 0,
    ) -> dict:
        """
        Run all promotion gates.

        Args:
            backtest_result:  Object / dict produced by the backtest module.
                              Expected attributes / keys (all optional; gate
                              will FAIL if value is missing):

                              win_rate      float   0.0–1.0
                              total_trades  int
                              expected_value float
                              max_drawdown  float   0.0–1.0  (absolute fraction)
                              sharpe_ratio  float
                              no_look_ahead bool
                              out_of_sample bool
                              regimes_tested list[str] | int

            ablation_results: Optional list of per-ablation summary dicts
                              (currently reserved for future use).
            paper_days:       Number of days the config has been paper-traded.

        Returns:
            {
                "passed":       bool,            # True iff ALL gates pass
                "gates":        list[ChecklistItem],
                "gate_count":   int,
                "passed_count": int,
            }
        """
        br = _coerce(backtest_result)

        items: list[ChecklistItem] = [
            self._check_win_rate(br),
            self._check_min_trades(br),
            self._check_positive_ev(br),
            self._check_max_drawdown(br),
            self._check_no_look_ahead(br),
            self._check_out_of_sample(br),
            self._check_paper_validated(paper_days),
            self._check_regime_coverage(br),
            self._check_min_sharpe(br),
        ]

        passed_count = sum(1 for i in items if i.passed)
        all_passed   = passed_count == len(items)

        return {
            "passed":       all_passed,
            "gates":        items,
            "gate_count":   len(items),
            "passed_count": passed_count,
        }

    def print_report(self, result: dict) -> None:
        """Print a formatted checklist to stdout."""
        items:        list[ChecklistItem] = result.get("gates", [])
        passed_count: int                 = result.get("passed_count", 0)
        gate_count:   int                 = result.get("gate_count",   0)
        all_passed:   bool                = result.get("passed",       False)

        width = 60
        print("=" * width)
        print("  PROMOTION CHECKLIST")
        print("=" * width)

        for item in items:
            status = "PASS" if item.passed else "FAIL"
            mark   = "[+]" if item.passed else "[X]"
            print(f"  {mark} {status:<4}  {item.message}")

        print("-" * width)
        verdict = "APPROVED — ready for production" if all_passed else "BLOCKED — not ready"
        print(f"  {passed_count}/{gate_count} gates passed")
        print(f"  Verdict: {verdict}")
        print("=" * width)

    # ------------------------------------------------------------------
    # Individual gate implementations
    # ------------------------------------------------------------------

    def _check_win_rate(self, br: dict) -> ChecklistItem:
        key       = "min_win_rate"
        threshold = 0.55
        value     = br.get("win_rate")
        if value is None:
            return ChecklistItem(
                name=key, passed=False, value=None, threshold=threshold,
                message=f"Win rate: MISSING (need >= {threshold:.0%})",
            )
        passed  = float(value) >= threshold
        pct_val = f"{float(value):.1%}"
        return ChecklistItem(
            name=key, passed=passed, value=value, threshold=threshold,
            message=f"Win rate: {pct_val} (need >= {threshold:.0%})",
        )

    def _check_min_trades(self, br: dict) -> ChecklistItem:
        key       = "min_trades"
        threshold = 30
        value     = br.get("total_trades")
        if value is None:
            return ChecklistItem(
                name=key, passed=False, value=None, threshold=threshold,
                message=f"Trades: MISSING (need >= {threshold})",
            )
        passed = int(value) >= threshold
        return ChecklistItem(
            name=key, passed=passed, value=value, threshold=threshold,
            message=f"Trades: {int(value)} (need >= {threshold})",
        )

    def _check_positive_ev(self, br: dict) -> ChecklistItem:
        key       = "positive_ev"
        threshold = 0.0
        value     = br.get("expected_value")
        if value is None:
            return ChecklistItem(
                name=key, passed=False, value=None, threshold=threshold,
                message="Expected value: MISSING (need > 0)",
            )
        passed = float(value) > threshold
        return ChecklistItem(
            name=key, passed=passed, value=value, threshold=threshold,
            message=f"Expected value: {float(value):.4f} (need > {threshold})",
        )

    def _check_max_drawdown(self, br: dict) -> ChecklistItem:
        key       = "max_drawdown"
        threshold = 0.15
        value     = br.get("max_drawdown")
        if value is None:
            return ChecklistItem(
                name=key, passed=False, value=None, threshold=threshold,
                message=f"Max drawdown: MISSING (need < {threshold:.0%})",
            )
        passed  = float(value) < threshold
        pct_val = f"{float(value):.1%}"
        return ChecklistItem(
            name=key, passed=passed, value=value, threshold=threshold,
            message=f"Max drawdown: {pct_val} (need < {threshold:.0%})",
        )

    def _check_no_look_ahead(self, br: dict) -> ChecklistItem:
        key       = "no_look_ahead"
        threshold = True
        value     = br.get("no_look_ahead")
        if value is None:
            # Default to FAIL — must be explicitly confirmed
            return ChecklistItem(
                name=key, passed=False, value=None, threshold=threshold,
                message="No look-ahead bias: NOT CONFIRMED",
            )
        passed = bool(value) is True
        status = "confirmed" if passed else "DETECTED — fix before promoting"
        return ChecklistItem(
            name=key, passed=passed, value=value, threshold=threshold,
            message=f"No look-ahead bias: {status}",
        )

    def _check_out_of_sample(self, br: dict) -> ChecklistItem:
        key       = "out_of_sample"
        threshold = True
        value     = br.get("out_of_sample")
        if value is None:
            return ChecklistItem(
                name=key, passed=False, value=None, threshold=threshold,
                message="Out-of-sample test: NOT CONFIRMED",
            )
        passed = bool(value) is True
        status = "done" if passed else "missing — run OOS backtest"
        return ChecklistItem(
            name=key, passed=passed, value=value, threshold=threshold,
            message=f"Out-of-sample test: {status}",
        )

    def _check_paper_validated(self, paper_days: int) -> ChecklistItem:
        key       = "paper_validated"
        threshold = True   # 14 days minimum
        min_days  = 14
        passed    = int(paper_days) >= min_days
        status    = (
            f"{paper_days} days (>= {min_days} required)"
            if passed
            else f"{paper_days} days — need {min_days - paper_days} more"
        )
        return ChecklistItem(
            name=key, passed=passed, value=paper_days, threshold=threshold,
            message=f"Paper trading: {status}",
        )

    def _check_regime_coverage(self, br: dict) -> ChecklistItem:
        key       = "regime_coverage"
        threshold = 2
        value     = br.get("regimes_tested")
        if value is None:
            return ChecklistItem(
                name=key, passed=False, value=None, threshold=threshold,
                message=f"Regime coverage: MISSING (need >= {threshold} regimes)",
            )
        # Accept a list of regime names or a plain integer count
        if isinstance(value, (list, tuple, set)):
            count = len(set(value))
            display = f"{count} regimes: {sorted(set(value))}"
        else:
            count   = int(value)
            display = f"{count} regimes"
        passed = count >= threshold
        return ChecklistItem(
            name=key, passed=passed, value=value, threshold=threshold,
            message=f"Regime coverage: {display} (need >= {threshold})",
        )

    def _check_min_sharpe(self, br: dict) -> ChecklistItem:
        key       = "min_sharpe"
        threshold = 0.5
        value     = br.get("sharpe_ratio")
        if value is None:
            return ChecklistItem(
                name=key, passed=False, value=None, threshold=threshold,
                message=f"Sharpe ratio: MISSING (need > {threshold})",
            )
        passed = float(value) > threshold
        return ChecklistItem(
            name=key, passed=passed, value=value, threshold=threshold,
            message=f"Sharpe ratio: {float(value):.3f} (need > {threshold})",
        )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _coerce(backtest_result) -> dict:
    """
    Accept a dict, an object with attributes, or None.
    Returns a plain dict for uniform key access in gate methods.
    """
    if backtest_result is None:
        return {}
    if isinstance(backtest_result, dict):
        return backtest_result
    # Object with attributes — build a dict from the gate field names
    fields = [
        "win_rate", "total_trades", "expected_value", "max_drawdown",
        "no_look_ahead", "out_of_sample", "regimes_tested", "sharpe_ratio",
    ]
    return {f: getattr(backtest_result, f, None) for f in fields}
