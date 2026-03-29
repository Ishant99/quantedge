# =============================================================================
# backtest/engine.py — M8: Backtesting Engine
#
# Runs the full strategy on historical data and reports:
#   total return, win rate, max drawdown, Sharpe ratio, profit factor
#
# Usage:
#   python -m backtest.engine --symbol RELIANCE --years 3
#   python -m backtest.engine --all --years 2
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import json
from datetime import datetime, timedelta
from dataclasses import dataclass
import yfinance as yf

from config import (
    BACKTEST_START_DATE, BACKTEST_END_DATE, BACKTEST_CAPITAL,
    BACKTEST_RESULTS_DIR, RISK_PER_TRADE_PCT, REWARD_RISK_RATIO,
    RSI_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    SMA_SHORT, SMA_MID, SMA_LONG, MIN_TA_SCORE
)
from analysis.technical_agent import TechnicalAgent
from utils import get_logger

logger = get_logger("Backtest")


@dataclass
class BacktestResult:
    symbol:           str
    start_date:       str
    end_date:         str
    initial_capital:  float
    final_capital:    float
    total_return_pct: float
    total_trades:     int
    wins:             int
    losses:           int
    win_rate_pct:     float
    max_drawdown_pct: float
    sharpe_ratio:     float
    profit_factor:    float
    avg_trade_pnl:    float
    best_trade_pnl:   float
    worst_trade_pnl:  float


class BacktestEngine:
    """
    M8 — Walk-forward backtester using daily OHLCV data.

    Strategy tested:
      - Entry:  TA score >= MIN_TA_SCORE and signal == bullish
      - Exit:   Stop-loss OR take-profit hit (checked daily on high/low)
      - Sizing: 2% risk per trade rule (same as live agent)
    """

    def __init__(self):
        self.ta = TechnicalAgent()
        os.makedirs(BACKTEST_RESULTS_DIR, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        symbol:     str,
        start_date: str = BACKTEST_START_DATE,
        end_date:   str = BACKTEST_END_DATE,
        capital:    float = BACKTEST_CAPITAL,
    ) -> BacktestResult:
        """Run backtest for a single symbol."""
        logger.info(f"Backtesting {symbol} | {start_date} to {end_date} | capital: {capital:,.0f}")

        df = self._fetch(symbol, start_date, end_date)
        if df is None or len(df) < SMA_LONG + 50:
            logger.warning(f"{symbol}: insufficient history for backtest")
            return None

        trades, equity_curve = self._simulate(df, capital)

        if not trades:
            logger.warning(f"{symbol}: no trades generated in backtest period")

        result = self._metrics(symbol, start_date, end_date, capital, trades, equity_curve)
        self._save(result, trades)
        self._print(result)
        return result

    def run_all(
        self,
        symbols:    list[str],
        start_date: str = BACKTEST_START_DATE,
        end_date:   str = BACKTEST_END_DATE,
    ) -> pd.DataFrame:
        """Run backtest for multiple symbols and return summary DataFrame."""
        results = []
        for i, sym in enumerate(symbols, 1):
            logger.info(f"[{i}/{len(symbols)}] {sym}")
            r = self.run(sym, start_date, end_date)
            if r:
                results.append(r)

        if not results:
            return pd.DataFrame()

        df = pd.DataFrame([
            {
                "symbol":           r.symbol,
                "return_%":         r.total_return_pct,
                "trades":           r.total_trades,
                "win_rate_%":       r.win_rate_pct,
                "max_drawdown_%":   r.max_drawdown_pct,
                "sharpe":           r.sharpe_ratio,
                "profit_factor":    r.profit_factor,
            }
            for r in results
        ]).sort_values("return_%", ascending=False)

        # Save summary
        path = os.path.join(BACKTEST_RESULTS_DIR, "summary.csv")
        df.to_csv(path, index=False)
        logger.info(f"Summary saved to {path}")
        self._print_summary(df)
        return df

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    def _simulate(self, df: pd.DataFrame, capital: float):
        """
        Walk-forward simulation. For each day:
          1. Calculate TA on data up to that day
          2. If signal is bullish and no open position — enter
          3. Each subsequent day check if SL or TP hit
        """
        trades     = []
        equity     = [capital]
        cash       = capital
        position   = None   # {entry, sl, tp, qty, entry_date}

        for i in range(SMA_LONG, len(df)):
            window = df.iloc[:i]
            today  = df.iloc[i]
            date   = df.index[i]

            # Check exit conditions for open position
            if position:
                hi = today["high"]
                lo = today["low"]

                # Stop-loss hit
                if lo <= position["sl"]:
                    pnl   = (position["sl"] - position["entry"]) * position["qty"]
                    cash += position["sl"] * position["qty"]
                    trades.append({
                        "entry_date": position["entry_date"],
                        "exit_date":  str(date.date()),
                        "symbol":     df["symbol"].iloc[0] if "symbol" in df.columns else "",
                        "entry":      position["entry"],
                        "exit":       position["sl"],
                        "qty":        position["qty"],
                        "pnl":        round(pnl, 2),
                        "exit_type":  "stop_loss",
                    })
                    position = None

                # Take-profit hit
                elif hi >= position["tp"]:
                    pnl   = (position["tp"] - position["entry"]) * position["qty"]
                    cash += position["tp"] * position["qty"]
                    trades.append({
                        "entry_date": position["entry_date"],
                        "exit_date":  str(date.date()),
                        "symbol":     df["symbol"].iloc[0] if "symbol" in df.columns else "",
                        "entry":      position["entry"],
                        "exit":       position["tp"],
                        "qty":        position["qty"],
                        "pnl":        round(pnl, 2),
                        "exit_type":  "take_profit",
                    })
                    position = None

            # Look for entry (only if no open position)
            if position is None:
                ta = self.ta.analyse("BT", window)
                if ta and ta.tradeable and ta.signal == "bullish":
                    entry  = today["close"]
                    atr    = self._atr(window)
                    sl     = entry - (1.5 * atr)
                    tp     = entry + (REWARD_RISK_RATIO * 1.5 * atr)
                    sl_dist= entry - sl

                    if sl_dist > 0:
                        risk_amt = cash * RISK_PER_TRADE_PCT
                        qty      = int(risk_amt / sl_dist)
                        cost     = qty * entry

                        if qty > 0 and cost <= cash:
                            cash    -= cost
                            position = {
                                "entry":      entry,
                                "sl":         sl,
                                "tp":         tp,
                                "qty":        qty,
                                "entry_date": str(date.date()),
                            }

            # Mark-to-market equity
            mtm = cash
            if position:
                mtm += today["close"] * position["qty"]
            equity.append(mtm)

        # Close any open position at end
        if position:
            last = df.iloc[-1]["close"]
            pnl  = (last - position["entry"]) * position["qty"]
            cash += last * position["qty"]
            trades.append({
                "entry_date": position["entry_date"],
                "exit_date":  str(df.index[-1].date()),
                "entry":      position["entry"],
                "exit":       last,
                "qty":        position["qty"],
                "pnl":        round(pnl, 2),
                "exit_type":  "end_of_period",
            })

        return trades, equity

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _metrics(
        self, symbol, start, end, initial, trades, equity
    ) -> BacktestResult:
        final     = equity[-1] if equity else initial
        ret_pct   = ((final - initial) / initial) * 100

        wins      = [t for t in trades if t["pnl"] > 0]
        losses    = [t for t in trades if t["pnl"] <= 0]
        win_rate  = (len(wins) / len(trades) * 100) if trades else 0

        avg_win   = np.mean([t["pnl"] for t in wins])  if wins   else 0
        avg_loss  = np.mean([t["pnl"] for t in losses]) if losses else 0
        pf        = abs(avg_win / avg_loss) if avg_loss != 0 else 0

        all_pnl   = [t["pnl"] for t in trades]
        avg_pnl   = np.mean(all_pnl) if all_pnl else 0
        best      = max(all_pnl)  if all_pnl else 0
        worst     = min(all_pnl)  if all_pnl else 0

        # Sharpe ratio (annualised, daily returns)
        eq        = pd.Series(equity)
        daily_ret = eq.pct_change().dropna()
        sharpe    = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)
                     if daily_ret.std() > 0 else 0)

        # Max drawdown
        peak  = eq.cummax()
        dd    = ((peak - eq) / peak * 100).max()

        return BacktestResult(
            symbol           = symbol,
            start_date       = start,
            end_date         = end,
            initial_capital  = initial,
            final_capital    = round(final, 2),
            total_return_pct = round(ret_pct, 2),
            total_trades     = len(trades),
            wins             = len(wins),
            losses           = len(losses),
            win_rate_pct     = round(win_rate, 1),
            max_drawdown_pct = round(float(dd), 2),
            sharpe_ratio     = round(float(sharpe), 2),
            profit_factor    = round(pf, 2),
            avg_trade_pnl    = round(avg_pnl, 2),
            best_trade_pnl   = round(best, 2),
            worst_trade_pnl  = round(worst, 2),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fetch(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        try:
            df = yf.Ticker(f"{symbol}.NS").history(
                start=start, end=end, interval="1d", auto_adjust=True
            )
            if df.empty:
                return None
            df.columns = [c.lower() for c in df.columns]
            df = df[["open","high","low","close","volume"]]
            df = df[df["close"] > 0].dropna()
            return df.sort_index()
        except Exception as e:
            logger.error(f"Fetch error {symbol}: {e}")
            return None

    def _atr(self, df: pd.DataFrame, period: int = 14) -> float:
        hi, lo, cl = df["high"], df["low"], df["close"]
        tr = pd.concat([hi-lo, (hi-cl.shift()).abs(), (lo-cl.shift()).abs()], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    def _save(self, result: BacktestResult, trades: list):
        os.makedirs(BACKTEST_RESULTS_DIR, exist_ok=True)
        path = os.path.join(BACKTEST_RESULTS_DIR, f"{result.symbol}_backtest.json")
        with open(path, "w") as f:
            json.dump({"result": result.__dict__, "trades": trades}, f, indent=2)

    def _print(self, r: BacktestResult):
        print(f"\n{'='*55}")
        print(f"  BACKTEST — {r.symbol}  ({r.start_date} to {r.end_date})")
        print(f"{'='*55}")
        print(f"  Initial capital  : Rs.{r.initial_capital:>12,.0f}")
        print(f"  Final capital    : Rs.{r.final_capital:>12,.0f}")
        print(f"  Total return     : {r.total_return_pct:>+10.2f}%")
        print(f"  Total trades     : {r.total_trades:>12}")
        print(f"  Win rate         : {r.win_rate_pct:>11.1f}%")
        print(f"  Profit factor    : {r.profit_factor:>12.2f}")
        print(f"  Max drawdown     : {r.max_drawdown_pct:>11.2f}%")
        print(f"  Sharpe ratio     : {r.sharpe_ratio:>12.2f}")
        print(f"  Avg trade P&L    : Rs.{r.avg_trade_pnl:>10,.0f}")
        print(f"  Best trade       : Rs.{r.best_trade_pnl:>10,.0f}")
        print(f"  Worst trade      : Rs.{r.worst_trade_pnl:>10,.0f}")
        print(f"{'='*55}\n")

    def _print_summary(self, df: pd.DataFrame):
        print(f"\n{'='*70}")
        print(f"  BACKTEST SUMMARY — {len(df)} STOCKS")
        print(f"{'='*70}")
        print(df.to_string(index=False))
        print(f"{'='*70}\n")


# =============================================================================
# CLI — python -m backtest.engine
# =============================================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="RELIANCE", help="NSE symbol")
    parser.add_argument("--years",  type=int, default=3, help="Years of history")
    parser.add_argument("--all",    action="store_true", help="Run all top stocks")
    args = parser.parse_args()

    engine = BacktestEngine()
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=365 * args.years)).strftime("%Y-%m-%d")

    if args.all:
        symbols = ["RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK",
                   "HINDUNILVR","SBIN","ITC","KOTAKBANK","AXISBANK"]
        engine.run_all(symbols, start, end)
    else:
        engine.run(args.symbol, start, end)
