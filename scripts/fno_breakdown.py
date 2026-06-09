#!/usr/bin/env python3
# =============================================================================
# scripts/fno_breakdown.py — F&O Trade Breakdown Analyser
#
# Usage:
#   python scripts/fno_breakdown.py              # last 90 days
#   python scripts/fno_breakdown.py --days 30
#   python scripts/fno_breakdown.py --month 2026-05
#   python scripts/fno_breakdown.py --days 90 --send
# =============================================================================
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import argparse
from datetime import datetime, timedelta
from collections import defaultdict
from config import SQLITE_DB_FILE

try:
    from telegram.bot import send_telegram_message
    _TELEGRAM_OK = True
except Exception:
    _TELEGRAM_OK = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _conn():
    return sqlite3.connect(SQLITE_DB_FILE)


def _date_range(args) -> tuple[str, str]:
    if args.month:
        try:
            y, m = map(int, args.month.split("-"))
            start = datetime(y, m, 1)
            if m == 12:
                end = datetime(y + 1, 1, 1)
            else:
                end = datetime(y, m + 1, 1)
            return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        except ValueError:
            print(f"Invalid --month format '{args.month}'. Use YYYY-MM.")
            sys.exit(1)
    days = args.days
    end  = datetime.now()
    start = end - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _fmt_inr(v) -> str:
    try:
        v = float(v)
        sign = "-" if v < 0 else "+"
        return f"{sign}₹{abs(v):,.0f}"
    except Exception:
        return "—"


def _direction_label(option_type: str) -> str:
    ot = str(option_type).upper()
    if ot in ("CE",):
        return "BUY CE"
    if ot in ("PE",):
        return "BUY PE"
    if ot.startswith("SELL-CE"):
        return "SELL CE"
    if ot.startswith("SELL-PE"):
        return "SELL PE"
    if ot == "FUT-LONG":
        return "FUT LONG"
    if ot == "FUT-SHORT":
        return "FUT SHORT"
    return ot


def _is_win(row: dict) -> bool | None:
    pnl = row.get("pnl")
    if pnl is None:
        return None
    return float(pnl) > 0


def _days_held(row: dict) -> str:
    try:
        et = row.get("entry_time") or ""
        xt = row.get("exit_time")  or ""
        if not et or not xt:
            return "open"
        e = datetime.fromisoformat(et)
        x = datetime.fromisoformat(xt)
        d = (x - e).days
        return f"{d}d" if d > 0 else "<1d"
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

def fetch_trades(start: str, end: str) -> list[dict]:
    if not os.path.exists(SQLITE_DB_FILE):
        return []
    try:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT id, instrument, option_type, strike, expiry,
                       lots, qty, entry_premium, exit_premium,
                       entry_time, exit_time,
                       pnl, pnl_pct, status, exit_reason, reasoning
                FROM fno_trades
                WHERE entry_time >= ? AND entry_time < ?
                ORDER BY entry_time
            """, (start, end)).fetchall()
        cols = ["id", "instrument", "option_type", "strike", "expiry",
                "lots", "qty", "entry_premium", "exit_premium",
                "entry_time", "exit_time",
                "pnl", "pnl_pct", "status", "exit_reason", "reasoning"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        print(f"DB error: {e}")
        return []


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------

def _trade_table(trades: list[dict]) -> list[str]:
    lines = ["*Per-Trade Breakdown*", "```"]
    header = f"{'#':>3} {'Instrument':<11} {'Type':<10} {'Strike':>6} {'Expiry':<12} {'Entry':>7} {'Exit':>7} {'Lots':>4} {'P&L':>9} {'Result':<8} {'Held':<6} {'Exit Reason'}"
    lines.append(header)
    lines.append("-" * len(header))

    for i, t in enumerate(trades, 1):
        result = "WIN" if _is_win(t) else ("LOSS" if t.get("status") == "closed" else "OPEN")
        entry_p = f"₹{float(t['entry_premium'] or 0):,.0f}" if t.get("entry_premium") is not None else "—"
        exit_p  = f"₹{float(t['exit_premium']  or 0):,.0f}" if t.get("exit_premium")  is not None else "—"
        pnl_str = _fmt_inr(t.get("pnl") or 0)
        exit_r  = str(t.get("exit_reason") or "—")[:10]
        row = (
            f"{i:>3} {str(t['instrument'] or ''):<11} "
            f"{_direction_label(t['option_type']):<10} "
            f"{int(t['strike'] or 0):>6} "
            f"{str(t['expiry'] or ''):<12} "
            f"{entry_p:>7} {exit_p:>7} "
            f"{int(t['lots'] or 0):>4} "
            f"{pnl_str:>9} "
            f"{result:<8} {_days_held(t):<6} {exit_r}"
        )
        lines.append(row)

    lines.append("```")
    return lines


def _summary_by_category(trades: list[dict]) -> list[str]:
    lines = ["*Summary by Category*", "```"]

    def _tally(group_key):
        acc = defaultdict(lambda: {"wins": 0, "losses": 0, "open": 0, "total_pnl": 0.0, "trades": 0})
        for t in trades:
            k = group_key(t)
            status = t.get("status")
            pnl    = float(t.get("pnl") or 0)
            acc[k]["trades"] += 1
            acc[k]["total_pnl"] += pnl
            if status == "closed":
                if pnl > 0:
                    acc[k]["wins"] += 1
                else:
                    acc[k]["losses"] += 1
            else:
                acc[k]["open"] += 1
        return acc

    # By instrument
    lines.append("By Instrument:")
    inst_acc = _tally(lambda t: str(t.get("instrument") or "UNKNOWN").upper())
    lines.append(f"  {'Instrument':<12} {'Trades':>6} {'Wins':>5} {'Losses':>7} {'WR%':>6} {'Total P&L':>12}")
    for k, v in sorted(inst_acc.items()):
        closed = v["wins"] + v["losses"]
        wr = f"{v['wins']/closed*100:.0f}%" if closed else "—"
        lines.append(f"  {k:<12} {v['trades']:>6} {v['wins']:>5} {v['losses']:>7} {wr:>6} {_fmt_inr(v['total_pnl']):>12}")

    lines.append("")

    # By option type / direction
    lines.append("By Direction:")
    dir_acc = _tally(lambda t: _direction_label(t.get("option_type") or ""))
    lines.append(f"  {'Direction':<12} {'Trades':>6} {'Wins':>5} {'Losses':>7} {'WR%':>6} {'Total P&L':>12}")
    for k, v in sorted(dir_acc.items()):
        closed = v["wins"] + v["losses"]
        wr = f"{v['wins']/closed*100:.0f}%" if closed else "—"
        lines.append(f"  {k:<12} {v['trades']:>6} {v['wins']:>5} {v['losses']:>7} {wr:>6} {_fmt_inr(v['total_pnl']):>12}")

    lines.append("")

    # By exit reason
    lines.append("By Exit Reason:")
    exit_acc = _tally(lambda t: str(t.get("exit_reason") or "OPEN"))
    lines.append(f"  {'Exit Reason':<12} {'Trades':>6} {'Avg P&L':>10}")
    for k, v in sorted(exit_acc.items()):
        avg = v["total_pnl"] / v["trades"] if v["trades"] else 0
        lines.append(f"  {k:<12} {v['trades']:>6} {_fmt_inr(avg):>10}")

    lines.append("```")
    return lines


def _metrics_section(trades: list[dict]) -> list[str]:
    closed  = [t for t in trades if t.get("status") == "closed"]
    winners = [t for t in closed if float(t.get("pnl") or 0) > 0]
    losers  = [t for t in closed if float(t.get("pnl") or 0) <= 0]
    open_t  = [t for t in trades if t.get("status") != "closed"]

    total_pnl = sum(float(t.get("pnl") or 0) for t in trades)
    avg_win   = sum(float(t.get("pnl") or 0) for t in winners) / len(winners) if winners else 0
    avg_loss  = sum(float(t.get("pnl") or 0) for t in losers)  / len(losers)  if losers  else 0
    win_rate  = len(winners) / len(closed) * 100 if closed else 0
    rr        = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    breakeven_wr = 1 / (1 + rr) * 100 if rr > 0 else 33.3

    lines = ["*Key Metrics*", "```"]
    lines.append(f"  Total trades       : {len(trades)}  (closed: {len(closed)}, open: {len(open_t)})")
    lines.append(f"  Win / Loss         : {len(winners)} / {len(losers)}")
    lines.append(f"  Win rate           : {win_rate:.1f}%  (breakeven @ {breakeven_wr:.1f}% for {rr:.1f}:1 R:R)")
    lines.append(f"  Avg winning trade  : {_fmt_inr(avg_win)}")
    lines.append(f"  Avg losing trade   : {_fmt_inr(avg_loss)}")
    lines.append(f"  Net P&L            : {_fmt_inr(total_pnl)}")
    if closed:
        best = max(closed, key=lambda t: float(t.get("pnl") or 0))
        worst = min(closed, key=lambda t: float(t.get("pnl") or 0))
        lines.append(f"  Best trade         : {best['instrument']} {_direction_label(best['option_type'])} {_fmt_inr(best.get('pnl'))}")
        lines.append(f"  Worst trade        : {worst['instrument']} {_direction_label(worst['option_type'])} {_fmt_inr(worst.get('pnl'))}")
    if win_rate < breakeven_wr and len(closed) >= 3:
        gap = breakeven_wr - win_rate
        lines.append(f"")
        lines.append(f"  ⚠  Win rate is {gap:.1f}pp BELOW breakeven — strategy is losing EV")
    lines.append("```")
    return lines


def _diagnosis(trades: list[dict]) -> list[str]:
    """Flag losing setups based on category P&L."""
    lines = ["*Diagnosis*"]

    closed = [t for t in trades if t.get("status") == "closed"]
    if not closed:
        lines.append("_No closed trades to analyse._")
        return lines

    # Build per-category P&L
    dir_pnl: dict[str, float] = defaultdict(float)
    dir_trades: dict[str, int] = defaultdict(int)
    for t in closed:
        k = _direction_label(t.get("option_type") or "")
        dir_pnl[k]    += float(t.get("pnl") or 0)
        dir_trades[k] += 1

    losing_setups = [(k, v) for k, v in dir_pnl.items() if v < 0]
    losing_setups.sort(key=lambda x: x[1])

    if not losing_setups:
        lines.append("✅ No systematically losing setups found.")
        return lines

    lines.append("Losing setups (by direction):")
    for k, pnl in losing_setups:
        n = dir_trades[k]
        lines.append(f"  • {k:<12}: {_fmt_inr(pnl)} over {n} trade(s)")

    # Check if SELL strategies are fine vs BUY
    sell_pnl = sum(v for k, v in dir_pnl.items() if k.startswith("SELL"))
    buy_pnl  = sum(v for k, v in dir_pnl.items() if not k.startswith("SELL") and not k.startswith("FUT"))
    fut_pnl  = sum(v for k, v in dir_pnl.items() if k.startswith("FUT"))

    if buy_pnl < 0:
        lines.append(f"  → Directional BUY options are losing: {_fmt_inr(buy_pnl)}")
        lines.append(f"    Consider: reduce lot size, tighten entry filter (higher MIN_TA_SCORE)")
    if sell_pnl < 0:
        lines.append(f"  → Premium-SELL strategies are losing: {_fmt_inr(sell_pnl)}")
        lines.append(f"    Consider: only sell in HV environment, widen strikes")
    if fut_pnl < 0:
        lines.append(f"  → Futures trades are losing: {_fmt_inr(fut_pnl)}")
        lines.append(f"    Consider: check FNO_BLOCK_SAME_DAY_INDEX gate is active")

    return lines


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_report(start: str, end: str) -> tuple[list[str], list[dict]]:
    trades = fetch_trades(start, end)
    period_label = f"{start} → {end}"

    lines = [
        f"📊 *F&O Trade Breakdown — {period_label}*",
        f"_{len(trades)} total trade(s)_",
        "",
    ]

    if not trades:
        lines.append("_No F&O trades found for this period._")
        return lines, []

    lines += _metrics_section(trades)
    lines.append("")
    lines += _summary_by_category(trades)
    lines.append("")
    lines += _diagnosis(trades)
    lines.append("")
    lines += _trade_table(trades)

    return lines, trades


def main():
    parser = argparse.ArgumentParser(description="F&O Trade Breakdown")
    parser.add_argument("--days",  type=int, default=90, help="lookback days (default 90)")
    parser.add_argument("--month", type=str, default=None, help="calendar month YYYY-MM")
    parser.add_argument("--send",  action="store_true", help="send report to Telegram")
    args = parser.parse_args()

    start, end = _date_range(args)
    lines, trades = build_report(start, end)

    text = "\n".join(lines)
    print(text)

    if args.send:
        if not _TELEGRAM_OK:
            print("\n[WARN] Telegram not available — skipping send.")
            return
        # Send in chunks of 4000 chars (Telegram limit ~4096)
        chunk_size = 4000
        for i in range(0, len(text), chunk_size):
            send_telegram_message(text[i:i + chunk_size])
        print("\n[OK] Report sent to Telegram.")


if __name__ == "__main__":
    main()
