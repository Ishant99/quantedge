#!/usr/bin/env python3
# =============================================================================
# scripts/phase0_reset.py — Turnaround Plan Phase 0
#
# Applies the kill switches and focused-mode settings from
# docs/TURNAROUND_PLAN.md:
#   1. Disable F&O, crypto, and US new entries (asset class gates)
#   2. Keep NSE spot enabled (the focused learning market)
#   3. Print what changed and what still needs a restart
#
# Open positions are NOT touched — monitors keep managing them to exit.
#
# Usage (on the VM):
#   python scripts/phase0_reset.py            # show current state, ask nothing, apply
#   python scripts/phase0_reset.py --dry-run  # show what would change only
# =============================================================================
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import settings.manager as S


GATES = {
    # setting key            -> (target value, label)
    "ASSET_NSE_SPOT_ENABLED": ("true",  "NSE spot (focused learning market)"),
    "ASSET_FNO_ENABLED":      ("false", "F&O — 99% of losses, disabled until equity edge proven"),
    "ASSET_CRYPTO_ENABLED":   ("false", "Crypto — disabled to focus sample collection"),
    "ASSET_US_ENABLED":       ("false", "US equities — disabled to focus sample collection"),
}


def main():
    parser = argparse.ArgumentParser(description="Apply Turnaround Plan Phase 0 settings")
    parser.add_argument("--dry-run", action="store_true", help="show changes without applying")
    args = parser.parse_args()

    print("=" * 70)
    print("  TURNAROUND PLAN — PHASE 0 RESET")
    print("=" * 70)

    changes = []
    for key, (target, label) in GATES.items():
        current = S.get(key)
        current_str = str(current).strip().lower() if current is not None else "(unset)"
        if current_str != target:
            changes.append((key, current_str, target, label))
            print(f"  {key:<26} {current_str:>8} → {target:<6} | {label}")
        else:
            print(f"  {key:<26} {current_str:>8} (no change) | {label}")

    if not changes:
        print("\nAll gates already in Phase 0 state. Nothing to do.")
        return

    if args.dry_run:
        print(f"\n[DRY RUN] {len(changes)} setting(s) would change. Re-run without --dry-run to apply.")
        return

    for key, _, target, _ in changes:
        S.set_value(key, target)

    print(f"\n[OK] {len(changes)} setting(s) written to logs/user_settings.json")
    print("\nIMPORTANT — next steps:")
    print("  1. RESTART the scheduler (config gates are read at import time):")
    print("       sudo systemctl restart trading-agent")
    print("  2. Open F&O/crypto/US positions are still monitored and will exit")
    print("     normally; only NEW entries are blocked.")
    print("  3. Run Phase 1 validation overnight:")
    print("       python scripts/validate_setups.py --years 3")

    # Best-effort Telegram note so the change is on the record
    try:
        from utils.telegram import send
        lines = "\n".join(f"  • {k}: {c} → {t}" for k, c, t, _ in changes)
        send(
            "🔒 *Phase 0 Reset Applied*\n"
            "Focused mode: NSE spot only. F&O/crypto/US entries disabled.\n"
            f"{lines}\n"
            "_Restart the scheduler for gates to take effect._"
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
