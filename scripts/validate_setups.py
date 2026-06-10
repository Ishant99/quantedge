#!/usr/bin/env python3
# =============================================================================
# scripts/validate_setups.py — Turnaround Plan Phase 1
#
# Validates each production setup SEPARATELY on the NSE500 universe with
# realistic costs, then reports per-setup and per-regime expectancy with
# Wilson 95% lower-bound win rates. The Wilson lower bound — not the raw
# win rate — is the decision number.
#
# Setups (mirroring production rules in pipeline/runner.py):
#   technical_base       — TA composite entry (BacktestEngine default)
#   breakout_52w         — TA entry AND within 2% of 252d high, vol>=1.5x20d, close>EMA50
#   rsi2_mean_reversion  — TA entry AND RSI(2)<=10, close>EMA200
#
# Verdicts:
#   PASS               n>=30, Wilson LB > breakeven WR, expectancy > 0
#   FAIL               n>=30 and either condition missed
#   INSUFFICIENT_DATA  n<30
#
# Usage (run overnight on the VM — yfinance fetch for ~150 symbols is slow):
#   python scripts/validate_setups.py                       # 3y, 150 symbols, all setups
#   python scripts/validate_setups.py --years 3 --limit 250
#   python scripts/validate_setups.py --setups rsi2_mean_reversion,breakout_52w
# =============================================================================
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import csv
import json
import math
from datetime import datetime, timedelta

import pandas as pd

from backtest.engine import BacktestEngine
from config import REWARD_RISK_RATIO
from utils import get_logger

logger = get_logger("SetupValidator")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIVERSE_CSV = os.path.join(_ROOT, "data", "nse500_symbols.csv")
RESULTS_FILE = os.path.join(_ROOT, "logs", "setup_validation.json")

SETUPS = ("technical_base", "breakout_52w", "rsi2_mean_reversion")

# Realistic round-trip costs: ~0.05% commission/charges + 0.10% slippage per side.
# Deliberately harsher than the engine defaults (0.03%/0.05%) which the live
# drift score proved optimistic.
COMMISSION_PCT = 0.0005
SLIPPAGE_PCT   = 0.0010

MIN_TRADES_VERDICT = 30   # below this: INSUFFICIENT_DATA
MIN_TRADES_REGIME  = 15   # per-regime verdict threshold


def wilson_lower_bound(wins: int, n: int, z: float = 1.96) -> float:
    """Wilson score interval lower bound for a binomial proportion."""
    if n == 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


class SetupValidator(BacktestEngine):
    """BacktestEngine with a per-setup entry overlay ANDed into the TA signal."""

    def __init__(self, setup: str):
        super().__init__()
        self.setup = setup

    def _generate_signal(self, window: pd.DataFrame, disabled: set) -> tuple:
        tradeable, bullish, ta_score = super()._generate_signal(window, disabled)
        if not (tradeable and bullish):
            return tradeable, bullish, ta_score
        if self.setup == "technical_base":
            return tradeable, bullish, ta_score
        if self.setup == "breakout_52w":
            return (self._breakout_overlay(window), bullish, ta_score)
        if self.setup == "rsi2_mean_reversion":
            return (self._rsi2_overlay(window), bullish, ta_score)
        return tradeable, bullish, ta_score

    @staticmethod
    def _breakout_overlay(window: pd.DataFrame) -> bool:
        """Mirror analysis/breakout_52w.py: near 52w high + volume + EMA50 trend."""
        try:
            close, high, volume = window["close"], window["high"], window["volume"]
            last_close = float(close.iloc[-1])
            lookback = min(252, len(window))
            high_52w = float(high.iloc[-lookback:].max())
            if high_52w <= 0:
                return False
            near_high = (high_52w - last_close) / high_52w * 100 <= 2.0
            vol_avg20 = float(volume.rolling(20).mean().iloc[-1])
            vol_confirmed = vol_avg20 > 0 and float(volume.iloc[-1]) / vol_avg20 >= 1.5
            ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
            return near_high and vol_confirmed and last_close > ema50
        except Exception:
            return False

    @staticmethod
    def _rsi2_overlay(window: pd.DataFrame) -> bool:
        """Mirror analysis/rsi2_strategy.py BUY rule: RSI(2)<=10 in an uptrend."""
        try:
            close = window["close"]
            delta = close.diff()
            gain = delta.clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
            loss = (-delta.clip(upper=0)).ewm(alpha=1 / 2, adjust=False).mean()
            rs = gain / loss.replace(0, 1e-10)
            rsi2 = float((100 - 100 / (1 + rs)).iloc[-1])
            ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
            return rsi2 <= 10.0 and float(close.iloc[-1]) > ema200
        except Exception:
            return False


def load_universe(limit: int) -> list[str]:
    symbols = []
    with open(UNIVERSE_CSV) as f:
        for row in csv.DictReader(f):
            sym = (row.get("symbol") or "").strip()
            if sym:
                symbols.append(sym)
    # Keep market-cap order from the CSV (rank column is pre-sorted) so a
    # --limit run still covers the most liquid names first.
    return symbols[:limit] if limit else symbols


def summarise(trades: list[dict]) -> dict:
    closed = [t for t in trades if t.get("exit_type") != "end_of_period"]
    wins   = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    n = len(closed)
    wr = len(wins) / n if n else 0.0
    avg_win  = sum(t["pnl"] for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0.0
    realized_rr = abs(avg_win / avg_loss) if avg_loss else 0.0
    breakeven_wr = 1 / (1 + realized_rr) if realized_rr > 0 else 1 / (1 + REWARD_RISK_RATIO)
    expectancy_r = (wr * avg_win + (1 - wr) * avg_loss) / abs(avg_loss) if avg_loss else 0.0
    lb = wilson_lower_bound(len(wins), n)
    return {
        "trades":          n,
        "wins":            len(wins),
        "win_rate":        round(wr, 4),
        "wilson_lb_95":    round(lb, 4),
        "avg_win":         round(avg_win, 2),
        "avg_loss":        round(avg_loss, 2),
        "realized_rr":     round(realized_rr, 2),
        "breakeven_wr":    round(breakeven_wr, 4),
        "expectancy_r":    round(expectancy_r, 4),
        "total_pnl":       round(sum(t["pnl"] for t in closed), 2),
    }


def verdict(stats: dict, min_trades: int = MIN_TRADES_VERDICT) -> str:
    if stats["trades"] < min_trades:
        return "INSUFFICIENT_DATA"
    if stats["wilson_lb_95"] > stats["breakeven_wr"] and stats["expectancy_r"] > 0:
        return "PASS"
    return "FAIL"


def validate_setup(setup: str, symbols: list[str], start: str, end: str) -> dict:
    engine = SetupValidator(setup)
    all_trades: list[dict] = []
    fetched = 0

    for i, sym in enumerate(symbols, 1):
        df = engine._fetch(sym, start, end)
        if df is None or len(df) < 250:
            continue
        fetched += 1
        try:
            trades, _, _ = engine._simulate(df, 1_000_000, COMMISSION_PCT, SLIPPAGE_PCT)
            trades = engine._tag_trades_with_regime(trades, df)
            for t in trades:
                t["symbol"] = sym
            all_trades.extend(trades)
        except Exception as e:
            logger.warning(f"{setup}/{sym}: simulation failed: {e}")
        if i % 25 == 0:
            print(f"  [{setup}] {i}/{len(symbols)} symbols, {len(all_trades)} trades so far...")

    overall = summarise(all_trades)
    overall["verdict"] = verdict(overall)
    overall["symbols_tested"] = fetched

    regimes: dict[str, dict] = {}
    by_regime: dict[str, list] = {}
    for t in all_trades:
        by_regime.setdefault(t.get("regime", "unknown"), []).append(t)
    for regime, ts in sorted(by_regime.items()):
        rstats = summarise(ts)
        rstats["verdict"] = verdict(rstats, min_trades=MIN_TRADES_REGIME)
        regimes[regime] = rstats

    return {"overall": overall, "by_regime": regimes}


def print_report(results: dict, start: str, end: str):
    print("\n" + "=" * 78)
    print(f"  SETUP VALIDATION — {start} → {end} "
          f"(costs: {COMMISSION_PCT*100:.2f}% comm + {SLIPPAGE_PCT*100:.2f}% slip per side)")
    print("=" * 78)
    header = (f"  {'Setup':<22} {'Trades':>6} {'WR':>6} {'WilsonLB':>8} "
              f"{'BreakevenWR':>11} {'Exp(R)':>7} {'Verdict':<18}")
    print(header)
    print("  " + "-" * 76)
    for setup, res in results.items():
        o = res["overall"]
        print(f"  {setup:<22} {o['trades']:>6} {o['win_rate']*100:>5.1f}% "
              f"{o['wilson_lb_95']*100:>7.1f}% {o['breakeven_wr']*100:>10.1f}% "
              f"{o['expectancy_r']:>7.3f} {o['verdict']:<18}")
        for regime, r in res["by_regime"].items():
            print(f"    └ {regime:<18} {r['trades']:>6} {r['win_rate']*100:>5.1f}% "
                  f"{r['wilson_lb_95']*100:>7.1f}% {r['breakeven_wr']*100:>10.1f}% "
                  f"{r['expectancy_r']:>7.3f} {r['verdict']:<18}")
    print("\n  Decision rule: trade a setup ONLY in regimes where verdict is PASS.")
    print(f"  Full results saved to {RESULTS_FILE}")


def main():
    parser = argparse.ArgumentParser(description="Per-setup walk-forward validation")
    parser.add_argument("--years",  type=int, default=3, help="lookback years (default 3)")
    parser.add_argument("--limit",  type=int, default=150,
                        help="max universe symbols, market-cap order (default 150, 0=all)")
    parser.add_argument("--setups", type=str, default=",".join(SETUPS),
                        help=f"comma-separated subset of: {','.join(SETUPS)}")
    args = parser.parse_args()

    setups = [s.strip() for s in args.setups.split(",") if s.strip() in SETUPS]
    if not setups:
        print(f"No valid setups in '{args.setups}'. Valid: {', '.join(SETUPS)}")
        sys.exit(1)

    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=args.years * 365)).strftime("%Y-%m-%d")
    symbols = load_universe(args.limit)
    print(f"Validating {len(setups)} setup(s) on {len(symbols)} symbols, {start} → {end}")
    print("This fetches data per symbol via yfinance — expect 1-3h for 150 symbols.\n")

    results = {}
    for setup in setups:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Validating {setup}...")
        results[setup] = validate_setup(setup, symbols, start, end)

    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "period": {"start": start, "end": end},
            "costs": {"commission_pct": COMMISSION_PCT, "slippage_pct": SLIPPAGE_PCT},
            "universe_size": len(symbols),
            "results": results,
        }, f, indent=2)

    print_report(results, start, end)

    # Best-effort Telegram summary
    try:
        from utils.telegram import send
        lines = []
        for setup, res in results.items():
            o = res["overall"]
            icon = {"PASS": "✅", "FAIL": "❌"}.get(o["verdict"], "⚠️")
            lines.append(
                f"{icon} `{setup}`: {o['trades']} trades, WR {o['win_rate']*100:.1f}% "
                f"(LB {o['wilson_lb_95']*100:.1f}%), Exp {o['expectancy_r']:+.2f}R → *{o['verdict']}*"
            )
        send("*Setup Validation Complete*\n" + "\n".join(lines))
    except Exception:
        pass


if __name__ == "__main__":
    main()
