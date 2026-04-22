# =============================================================================
# backtest/run_backtest.py — Backtest Runner
#
# Runs walk-forward validation on historical NSE data.
# Three modes:
#   python -m backtest.run_backtest --quick       # 5 liquid stocks, 2 years
#   python -m backtest.run_backtest --full        # NSE top 50, 2 years
#   python -m backtest.run_backtest --walkforward # train 2022-23, test 2024
#
# Outputs: CSV summary + per-setup breakdown printed to console
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
import pandas as pd
from datetime import datetime
from backtest.engine import BacktestEngine
from utils import get_logger

logger = get_logger("BacktestRunner")

# Tier 1: most liquid NSE stocks — best for walk-forward validation
TIER1_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY",
    "BHARTIARTL", "SBIN", "LICI", "ITC", "HINDUNILVR",
    "LT", "BAJFINANCE", "HCLTECH", "MARUTI", "SUNPHARMA",
    "AXISBANK", "KOTAKBANK", "TITAN", "WIPRO", "ONGC",
]

# Top 50 for the full run
TOP50_SYMBOLS = TIER1_SYMBOLS + [
    "NTPC", "ASIANPAINT", "POWERGRID", "ULTRACEMCO", "NESTLEIND",
    "JSWSTEEL", "M&M", "TATAMOTORS", "HDFCLIFE", "COALINDIA",
    "BAJAJ-AUTO", "SBILIFE", "TATACONSUM", "DRREDDY", "GRASIM",
    "ADANIPORTS", "DIVISLAB", "TECHM", "CIPLA", "BPCL",
    "EICHERMOT", "HEROMOTOCO", "BRITANNIA", "INDUSINDBK", "APOLLOHOSP",
    "TRENT", "BAJAJFINSV", "SHRIRAMFIN", "BEL", "ADANIENT",
]


def run_quick():
    """Quick 5-stock validation — run in < 3 minutes."""
    print("\n" + "=" * 60)
    print("QUICK BACKTEST — 5 stocks, 2 years")
    print("=" * 60)
    engine  = BacktestEngine()
    symbols = TIER1_SYMBOLS[:5]
    df      = engine.run_all(symbols, start_date="2023-01-01", end_date="2024-12-31")
    _print_summary(df, "Quick")
    return df


def run_full():
    """Full backtest — NSE top 50, 2 years."""
    print("\n" + "=" * 60)
    print("FULL BACKTEST — NSE Top 50, 2 years")
    print("=" * 60)
    engine = BacktestEngine()
    df     = engine.run_all(TOP50_SYMBOLS, start_date="2023-01-01", end_date="2024-12-31")
    _print_summary(df, "Full")
    return df


def run_walkforward():
    """
    Walk-forward validation:
      Train window: 2022-01-01 to 2023-12-31
      Test window:  2024-01-01 to 2024-12-31
    Compare results — if test metrics are close to train, strategy is robust.
    """
    print("\n" + "=" * 60)
    print("WALK-FORWARD VALIDATION")
    print("  Train: 2022-2023  |  Test: 2024")
    print("=" * 60)
    engine  = BacktestEngine()
    symbols = TIER1_SYMBOLS

    print("\n--- TRAINING WINDOW (2022–2023) ---")
    train_df = engine.run_all(symbols, start_date="2022-01-01", end_date="2023-12-31")

    print("\n--- TEST WINDOW (2024) ---")
    test_df  = engine.run_all(symbols, start_date="2024-01-01", end_date="2024-12-31")

    _compare_walkforward(train_df, test_df)
    return train_df, test_df


def _print_summary(df: pd.DataFrame, label: str):
    if df is None or df.empty:
        print("No results.")
        return

    print(f"\n{'─'*60}")
    print(f"{label} Backtest Summary")
    print(f"{'─'*60}")
    print(f"Stocks tested:   {len(df)}")
    print(f"Avg return:      {df['return_%'].mean():.1f}%")
    print(f"Avg win rate:    {df['win_rate_%'].mean():.1f}%")
    print(f"Avg Sharpe:      {df['sharpe'].mean():.2f}")
    print(f"Avg max DD:      {df['max_drawdown_%'].mean():.1f}%")
    print(f"Avg pf:          {df['profit_factor'].mean():.2f}")
    print()

    # Top performers
    top = df.nlargest(5, "return_%")[["symbol", "return_%", "win_rate_%", "sharpe"]]
    print("Top 5 by return:")
    print(top.to_string(index=False))
    print()

    # Per-setup breakdown (if setup_type column exists in results)
    _setup_breakdown(df)


def _setup_breakdown(df: pd.DataFrame):
    """Load trade-level data from backtest results and group by setup type."""
    from config import BACKTEST_RESULTS_DIR
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", BACKTEST_RESULTS_DIR)
    if not os.path.isdir(results_dir):
        return

    all_trades = []
    for fname in os.listdir(results_dir):
        if fname.endswith("_trades.json"):
            try:
                with open(os.path.join(results_dir, fname)) as f:
                    trades = json.load(f)
                    all_trades.extend(trades)
            except Exception:
                pass

    if not all_trades:
        return

    trades_df = pd.DataFrame(all_trades)
    if "setup_type" not in trades_df.columns or trades_df.empty:
        return

    print("P&L by setup type:")
    breakdown = trades_df.groupby("setup_type").agg(
        trades=("pnl", "count"),
        total_pnl=("pnl", "sum"),
        win_rate=("pnl", lambda x: (x > 0).mean() * 100),
    ).sort_values("total_pnl", ascending=False)
    print(breakdown.to_string())
    print()


def _compare_walkforward(train: pd.DataFrame, test: pd.DataFrame):
    if train is None or train.empty or test is None or test.empty:
        return

    print(f"\n{'─'*60}")
    print("WALK-FORWARD COMPARISON")
    print(f"{'─'*60}")

    metrics = ["return_%", "win_rate_%", "sharpe", "max_drawdown_%"]
    for m in metrics:
        if m in train.columns and m in test.columns:
            t_val = train[m].mean()
            v_val = test[m].mean()
            delta = v_val - t_val
            flag  = "✓" if abs(delta) / max(abs(t_val), 0.01) < 0.3 else "⚠"
            print(f"  {m:<20} Train: {t_val:+.2f}  Test: {v_val:+.2f}  Δ{delta:+.2f} {flag}")

    print()
    print("✓ = test within 30% of train (strategy robust)")
    print("⚠ = significant degradation — possible overfitting")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NSE Strategy Backtester")
    parser.add_argument("--quick",       action="store_true", help="5 stocks, 2 years")
    parser.add_argument("--full",        action="store_true", help="Top 50, 2 years")
    parser.add_argument("--walkforward", action="store_true", help="Train 2022-23, test 2024")
    args = parser.parse_args()

    if args.full:
        run_full()
    elif args.walkforward:
        run_walkforward()
    else:
        run_quick()   # default
