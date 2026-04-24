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

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ChecklistItem:
    """Result for a single promotion gate."""
    name:      str   # gate identifier key
    passed:    bool  # True if gate is satisfied
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
