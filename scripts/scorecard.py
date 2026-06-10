#!/usr/bin/env python3
# =============================================================================
# scripts/scorecard.py — Weekly per-setup scorecard (Phase 2, TURNAROUND_PLAN.md)
#
# Prints a champion/challenger per-setup scorecard:
#   - Per-setup: N, WR, Wilson 95% LB, expectancy in R, verdict
#   - Any KILL/PROBATION/SCALE verdicts printed as action items
#
# Usage:
#   python scripts/scorecard.py
#   python scripts/scorecard.py --send       # also Telegram
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
from datetime import datetime

from analysis.setup_tracker import SetupTracker, KNOWN_SETUPS
from utils import get_logger

logger = get_logger("Scorecard")

_VERDICT_EMOJI = {
    "KILL":             "🛑",
    "PROBATION":        "⚠️ ",
    "SCALE":            "📈",
    "OK":               "✅",
    "INSUFFICIENT_DATA": "⏳",
}


def build_scorecard() -> str:
    tracker   = SetupTracker()
    all_stats = tracker.evaluate_all()
    now       = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"*Per-Setup Scorecard — {now}*",
        "",
        "```",
        f"{'Setup':<26} {'N':>4} {'WR':>6} {'LB95':>6} {'Exp(R)':>8}  Verdict",
        "-" * 64,
    ]

    for setup in KNOWN_SETUPS:
        st = all_stats.get(setup)
        if st is None or st.n_trades == 0:
            lines.append(f"{setup:<26} {'  0':>4} {'  —':>6} {'  —':>6} {'      —':>8}  ⏳ INSUFFICIENT_DATA")
            continue
        emoji = _VERDICT_EMOJI.get(st.verdict, "")
        lines.append(
            f"{setup:<26} {st.n_trades:>4} "
            f"{st.win_rate:>5.1%} "
            f"{st.wilson_lb_95:>5.1%} "
            f"{st.expectancy_r:>+7.3f}R  "
            f"{emoji} {st.verdict}"
        )

    # Extra setups not in KNOWN_SETUPS
    for setup, st in all_stats.items():
        if setup not in KNOWN_SETUPS:
            emoji = _VERDICT_EMOJI.get(st.verdict, "")
            lines.append(
                f"{setup:<26} {st.n_trades:>4} "
                f"{st.win_rate:>5.1%} "
                f"{st.wilson_lb_95:>5.1%} "
                f"{st.expectancy_r:>+7.3f}R  "
                f"{emoji} {st.verdict}"
            )

    lines += ["```", ""]

    actions = []
    for setup in list(KNOWN_SETUPS) + [s for s in all_stats if s not in KNOWN_SETUPS]:
        st = all_stats.get(setup)
        if st is None:
            continue
        if st.verdict == "KILL":
            actions.append(f"🛑 *{setup}* — {st.verdict_reason}")
        elif st.verdict == "PROBATION":
            actions.append(f"⚠️  *{setup}* — {st.verdict_reason}")
        elif st.verdict == "SCALE":
            actions.append(f"📈 *{setup}* — {st.verdict_reason}")

    if actions:
        lines.append("*Action required:*")
        lines.extend(actions)
        lines.append("")

    lines.append(
        "_LB95 = Wilson 95% lower confidence bound on win rate. "
        "KILL fires when LB95 < breakeven; SCALE fires when LB95 > breakeven+5pp._"
    )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Weekly per-setup scorecard")
    parser.add_argument("--send", action="store_true", help="Send report to Telegram")
    args = parser.parse_args()

    report = build_scorecard()
    print(report)

    if args.send:
        try:
            from utils.telegram import send
            send(report)
            print("\n[Telegram sent]")
        except Exception as exc:
            print(f"\n[Telegram FAILED: {exc}]")


if __name__ == "__main__":
    main()
