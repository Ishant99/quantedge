#!/usr/bin/env python3
# =============================================================================
# scripts/review.py — QuantEdge Performance Review
#
# Pulls everything from trades.db + logs and prints a complete report.
# Run on the VM:  python scripts/review.py [--days 21] [--send]
#
# --days N   lookback window (default 21)
# --send     also send the report to Telegram + Discord
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
import sqlite3
from datetime import datetime, timedelta

# ── Setup ─────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="QuantEdge performance review")
parser.add_argument("--days",  type=int, default=30, help="Lookback window in days (default 30)")
parser.add_argument("--month", type=str, default="",
                    help="Calendar month, e.g. 2026-05 — overrides --days")
parser.add_argument("--send",  action="store_true",  help="Send report to Telegram/Discord")
args = parser.parse_args()

if args.month:
    try:
        from calendar import monthrange
        y, m   = int(args.month.split("-")[0]), int(args.month.split("-")[1])
        CUTOFF = f"{y:04d}-{m:02d}-01"
        last_d = monthrange(y, m)[1]
        TODAY  = f"{y:04d}-{m:02d}-{last_d:02d}"
        DAYS   = last_d
        _MONTH_LABEL = args.month
    except Exception:
        print(f"[ERROR] --month must be YYYY-MM, got: {args.month}")
        sys.exit(1)
else:
    DAYS  = args.days
    TODAY = datetime.now().strftime("%Y-%m-%d")
    _MONTH_LABEL = ""
if not args.month:
    CUTOFF = (datetime.now() - timedelta(days=DAYS)).strftime("%Y-%m-%d")
_ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGS   = os.path.join(_ROOT, "logs")

try:
    from config import SQLITE_DB_FILE, VIRTUAL_CAPITAL, INR_PER_USD, INR_PER_USDT
except Exception:
    SQLITE_DB_FILE  = os.path.join(_LOGS, "trades.db")
    VIRTUAL_CAPITAL = 1_000_000
    INR_PER_USD     = 83.0
    INR_PER_USDT    = 83.0

SEP  = "=" * 62
SEP2 = "-" * 62

lines = []   # collected report lines for Telegram send


def _p(*args_):
    """Print and collect."""
    text = " ".join(str(a) for a in args_)
    print(text)
    lines.append(text)


def _j(path, default=None):
    """Load JSON file safely."""
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return default or {}


def _q(conn, sql, params=()):
    """Run a query safely, return rows or []."""
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return []


def _q1(conn, sql, params=()):
    """Run a query safely, return first row or None."""
    try:
        return conn.execute(sql, params).fetchone()
    except Exception:
        return None


# ── Connect ───────────────────────────────────────────────────────────────────

if not os.path.exists(SQLITE_DB_FILE):
    print(f"[ERROR] Database not found: {SQLITE_DB_FILE}")
    sys.exit(1)

conn = sqlite3.connect(SQLITE_DB_FILE)

# ─────────────────────────────────────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────────────────────────────────────

_period_label = _MONTH_LABEL if _MONTH_LABEL else f"{DAYS} days"
_p(SEP)
_p(f"  QUANTEDGE — PERFORMANCE REVIEW  ({_period_label.upper()})")
_p(f"  Period : {CUTOFF}  →  {TODAY}")
_p(f"  Report : {datetime.now().strftime('%d %b %Y  %H:%M IST')}")
_p(SEP)

# ─────────────────────────────────────────────────────────────────────────────
# 1. NSE EQUITY
# ─────────────────────────────────────────────────────────────────────────────

_p("\n📈  NSE EQUITY")
_p(SEP2)

sig_total = _q1(conn,
    "SELECT COUNT(*) FROM signals WHERE timestamp >= ? AND action='BUY'",
    (CUTOFF,))[0]
sig_exec = _q1(conn,
    "SELECT COUNT(*) FROM signals WHERE timestamp >= ? AND action='BUY' AND executed=1",
    (CUTOFF,))[0]

_p(f"  Signals generated : {sig_total}")
_p(f"  Signals executed  : {sig_exec}")
if sig_total:
    _p(f"  Execution rate    : {sig_exec/sig_total*100:.0f}%")

# Closed trades
row = _q1(conn,
    "SELECT COUNT(*), COALESCE(SUM(pnl),0), COALESCE(AVG(pnl),0), "
    "       COALESCE(AVG(pnl_pct),0) "
    "FROM trades WHERE status='closed' AND exit_time >= ?",
    (CUTOFF,))
nse_count = row[0] if row else 0
nse_pnl   = row[1] if row else 0.0
nse_avg   = row[2] if row else 0.0
nse_pct   = row[3] if row else 0.0

wins = (_q1(conn,
    "SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl>0 AND exit_time >= ?",
    (CUTOFF,)) or [0])[0]
losses = (_q1(conn,
    "SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl<=0 AND exit_time >= ?",
    (CUTOFF,)) or [0])[0]

_p(f"\n  Closed trades : {nse_count}  (W:{wins}  L:{losses}  "
   f"WR:{wins/nse_count*100:.0f}%)" if nse_count else f"\n  Closed trades : 0")
_p(f"  Total P&L     : Rs.{nse_pnl:+,.0f}")
_p(f"  Avg per trade : Rs.{nse_avg:+,.0f}  ({nse_pct:+.2f}%)")

# Best / worst
best = _q1(conn,
    "SELECT symbol, pnl, pnl_pct FROM trades WHERE status='closed' AND exit_time >= ? "
    "ORDER BY pnl DESC LIMIT 1", (CUTOFF,))
worst = _q1(conn,
    "SELECT symbol, pnl, pnl_pct FROM trades WHERE status='closed' AND exit_time >= ? "
    "ORDER BY pnl ASC LIMIT 1", (CUTOFF,))
if best:
    _p(f"  Best trade    : {best[0]}  Rs.{best[1]:+,.0f}  ({best[2]:+.1f}%)")
if worst:
    _p(f"  Worst trade   : {worst[0]}  Rs.{worst[1]:+,.0f}  ({worst[2]:+.1f}%)")

# Open positions
open_pos = _q1(conn,
    "SELECT COUNT(*) FROM trades WHERE status='open'")[0]
_p(f"  Open positions: {open_pos}")

# ─────────────────────────────────────────────────────────────────────────────
# 2. F&O PAPER
# ─────────────────────────────────────────────────────────────────────────────

_p("\n📊  F&O PAPER")
_p(SEP2)

fno = _q1(conn,
    "SELECT COUNT(*), COALESCE(SUM(pnl),0), COALESCE(AVG(pnl_pct),0) "
    "FROM fno_trades WHERE status='closed' AND exit_time >= ?",
    (CUTOFF,))
fno_count = fno[0] if fno else 0
fno_pnl   = fno[1] if fno else 0.0
fno_pct   = fno[2] if fno else 0.0

fno_wins = (_q1(conn,
    "SELECT COUNT(*) FROM fno_trades WHERE status='closed' AND pnl>0 AND exit_time >= ?",
    (CUTOFF,)) or [0])[0]

fno_open = (_q1(conn, "SELECT COUNT(*) FROM fno_trades WHERE status='open'") or [0])[0]

_p(f"  Closed trades : {fno_count}  " +
   (f"(W:{fno_wins}  L:{fno_count-fno_wins}  WR:{fno_wins/fno_count*100:.0f}%)"
    if fno_count else ""))
_p(f"  Total P&L     : Rs.{fno_pnl:+,.0f}  (avg {fno_pct:+.2f}%/trade)")
_p(f"  Open positions: {fno_open}")

# By option type breakdown
ot_rows = _q(conn,
    "SELECT option_type, COUNT(*), COALESCE(SUM(pnl),0) "
    "FROM fno_trades WHERE status='closed' AND exit_time >= ? "
    "GROUP BY option_type", (CUTOFF,))
if ot_rows:
    _p("  Breakdown:")
    for ot, cnt, pnl in ot_rows:
        _p(f"    {(ot or 'FUT'):6s} : {cnt:3d} trades  Rs.{pnl:+,.0f}")

# ─────────────────────────────────────────────────────────────────────────────
# 3. CRYPTO
# ─────────────────────────────────────────────────────────────────────────────

_p("\n₿   CRYPTO")
_p(SEP2)

cry = _q1(conn,
    "SELECT COUNT(*), COALESCE(SUM(pnl),0), COALESCE(AVG(pnl_pct),0) "
    "FROM crypto_trades WHERE status='closed' AND exit_time >= ?",
    (CUTOFF,))
cry_count = cry[0] if cry else 0
cry_pnl   = cry[1] if cry else 0.0
cry_pct   = cry[2] if cry else 0.0

cry_wins = (_q1(conn,
    "SELECT COUNT(*) FROM crypto_trades WHERE status='closed' AND pnl>0 AND exit_time >= ?",
    (CUTOFF,)) or [0])[0]
cry_open = (_q1(conn, "SELECT COUNT(*) FROM crypto_trades WHERE status='open'") or [0])[0]

_p(f"  Closed trades : {cry_count}  " +
   (f"(W:{cry_wins}  L:{cry_count-cry_wins}  WR:{cry_wins/cry_count*100:.0f}%)"
    if cry_count else ""))
_p(f"  Total P&L     : {cry_pnl:+.2f} USDT  ≈  Rs.{cry_pnl*INR_PER_USDT:+,.0f}")
_p(f"  Avg per trade : {cry_pct:+.2f}%")
_p(f"  Open positions: {cry_open}")

# ─────────────────────────────────────────────────────────────────────────────
# 4. US STOCKS
# ─────────────────────────────────────────────────────────────────────────────

_p("\n🇺🇸  US STOCKS")
_p(SEP2)

us = _q1(conn,
    "SELECT COUNT(*), COALESCE(SUM(pnl),0), COALESCE(AVG(pnl_pct),0) "
    "FROM us_trades WHERE status='closed' AND exit_time >= ?",
    (CUTOFF,))
us_count = us[0] if us else 0
us_pnl   = us[1] if us else 0.0
us_pct   = us[2] if us else 0.0

us_wins = (_q1(conn,
    "SELECT COUNT(*) FROM us_trades WHERE status='closed' AND pnl>0 AND exit_time >= ?",
    (CUTOFF,)) or [0])[0]
us_open = (_q1(conn, "SELECT COUNT(*) FROM us_trades WHERE status='open'") or [0])[0]

_p(f"  Closed trades : {us_count}  " +
   (f"(W:{us_wins}  L:{us_count-us_wins}  WR:{us_wins/us_count*100:.0f}%)"
    if us_count else ""))
_p(f"  Total P&L     : ${us_pnl:+.2f}  ≈  Rs.{us_pnl*INR_PER_USD:+,.0f}")
_p(f"  Avg per trade : {us_pct:+.2f}%")
_p(f"  Open positions: {us_open}")

# ─────────────────────────────────────────────────────────────────────────────
# 5. COMBINED P&L
# ─────────────────────────────────────────────────────────────────────────────

combined = nse_pnl + fno_pnl + (cry_pnl * INR_PER_USDT) + (us_pnl * INR_PER_USD)
pf = _j(os.path.join(_LOGS, "virtual_portfolio.json"))
portfolio_value = pf.get("cash", VIRTUAL_CAPITAL)

# Add mark-to-market of open NSE positions
for sym, pos in pf.get("positions", {}).items():
    portfolio_value += pos.get("entry", 0) * pos.get("qty", 0)

nse_total_pnl = portfolio_value - VIRTUAL_CAPITAL

_p("\n" + SEP)
_p("  COMBINED SUMMARY")
_p(SEP)
_p(f"  Starting capital  : Rs.{VIRTUAL_CAPITAL:,.0f}")
_p(f"  Portfolio value   : Rs.{portfolio_value:,.0f}")
_p(f"  NSE equity P&L    : Rs.{nse_total_pnl:+,.0f}  "
   f"({nse_total_pnl/VIRTUAL_CAPITAL*100:+.2f}%)")
_p(f"  F&O paper P&L     : Rs.{fno_pnl:+,.0f}")
_p(f"  Crypto P&L        : {cry_pnl:+.2f} USDT  (Rs.{cry_pnl*INR_PER_USDT:+,.0f})")
_p(f"  US stocks P&L     : ${us_pnl:+.2f}  (Rs.{us_pnl*INR_PER_USD:+,.0f})")
_p(SEP2)
total_trades = nse_count + fno_count + cry_count + us_count
_p(f"  Total closed trades : {total_trades}")
_p(f"  Combined P&L (INR)  : Rs.{combined:+,.0f}")

# ─────────────────────────────────────────────────────────────────────────────
# 6. SIGNAL OUTCOMES
# ─────────────────────────────────────────────────────────────────────────────

_p("\n🎯  SIGNAL OUTCOMES  (BUY signals, all-time resolved)")
_p(SEP2)

outcome_rows = _q(conn,
    "SELECT outcome, COUNT(*), AVG(days_to_outcome) "
    "FROM signals WHERE outcome IS NOT NULL GROUP BY outcome")
total_resolved = sum(r[1] for r in outcome_rows)
for outcome, cnt, avg_days in sorted(outcome_rows, key=lambda x: -x[1]):
    pct = cnt / total_resolved * 100 if total_resolved else 0
    d   = f"  avg {avg_days:.0f}d" if avg_days else ""
    _p(f"  {outcome:10s} : {cnt:4d}  ({pct:.0f}%){d}")

if total_resolved:
    tp_count = next((r[1] for r in outcome_rows if r[0] == "TP_HIT"), 0)
    _p(f"\n  All-time win rate : {tp_count/total_resolved*100:.1f}%  "
       f"({tp_count}/{total_resolved} resolved)")

# Last 21d outcomes
recent_outcomes = _q(conn,
    "SELECT outcome, COUNT(*) FROM signals "
    "WHERE outcome IS NOT NULL AND outcome_date >= ? GROUP BY outcome",
    (CUTOFF,))
if recent_outcomes:
    _p(f"\n  Last {DAYS} days resolved:")
    rr_total = sum(r[1] for r in recent_outcomes)
    for outcome, cnt in sorted(recent_outcomes, key=lambda x: -x[1]):
        _p(f"    {outcome:10s} : {cnt}  ({cnt/rr_total*100:.0f}%)")

# ─────────────────────────────────────────────────────────────────────────────
# 7. REGIME ACTIVITY
# ─────────────────────────────────────────────────────────────────────────────

_p(f"\n🌦   REGIME ACTIVITY  (last {DAYS} days)")
_p(SEP2)

regime_rows = _q(conn,
    "SELECT regime_tag, COUNT(*) FROM signals "
    "WHERE timestamp >= ? AND action='BUY' AND regime_tag != '' "
    "GROUP BY regime_tag ORDER BY COUNT(*) DESC",
    (CUTOFF,))
if regime_rows:
    total_tagged = sum(r[1] for r in regime_rows)
    for regime, cnt in regime_rows:
        bar = "█" * int(cnt / max(r[1] for r in regime_rows) * 20)
        _p(f"  {regime:10s} : {cnt:4d} signals  {cnt/total_tagged*100:.0f}%  {bar}")
else:
    _p("  (no regime_tag data — will populate on next scan)")

# Sideways blocks (signals generated but not executed in sideways)
sideways_blocked = _q1(conn,
    "SELECT COUNT(*) FROM signals "
    "WHERE timestamp >= ? AND action='BUY' AND regime_tag='sideways' AND executed=0",
    (CUTOFF,))
if sideways_blocked and sideways_blocked[0]:
    _p(f"\n  ↔ SIDEWAYS blocks : {sideways_blocked[0]} BUY signals suppressed")

# ─────────────────────────────────────────────────────────────────────────────
# 8. TOP PERFORMERS
# ─────────────────────────────────────────────────────────────────────────────

_p("\n🏆  TOP & BOTTOM PERFORMERS  (closed NSE trades)")
_p(SEP2)

sym_rows = _q(conn,
    "SELECT symbol, COUNT(*), SUM(pnl), AVG(pnl_pct), "
    "       SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) "
    "FROM trades WHERE status='closed' AND exit_time >= ? "
    "GROUP BY symbol ORDER BY SUM(pnl) DESC",
    (CUTOFF,))

if sym_rows:
    _p("  Top 5:")
    for sym, cnt, pnl, pct, w in sym_rows[:5]:
        _p(f"    {sym:15s} {cnt:2d}t  Rs.{pnl:+8,.0f}  {pct:+.1f}%  WR:{w/cnt*100:.0f}%")
    if len(sym_rows) > 5:
        _p("  Bottom 5:")
        for sym, cnt, pnl, pct, w in sym_rows[-5:]:
            _p(f"    {sym:15s} {cnt:2d}t  Rs.{pnl:+8,.0f}  {pct:+.1f}%  WR:{w/cnt*100:.0f}%")
else:
    _p("  No closed NSE trades in this window")

# ─────────────────────────────────────────────────────────────────────────────
# 9. CONFIDENCE CALIBRATION STATUS
# ─────────────────────────────────────────────────────────────────────────────

_p("\n📐  CALIBRATION STATUS")
_p(SEP2)

bands = [
    ("0.50–0.59", 0.50, 0.60),
    ("0.60–0.69", 0.60, 0.70),
    ("0.70–0.79", 0.70, 0.80),
    ("0.80+",     0.80, 1.01),
]
calib_ready = 0
for label, lo, hi in bands:
    row = _q1(conn,
        "SELECT COUNT(*), "
        "SUM(CASE WHEN outcome='TP_HIT' THEN 1 ELSE 0 END) "
        "FROM signals WHERE outcome IS NOT NULL "
        "AND confidence >= ? AND confidence < ?",
        (lo, hi))
    n   = row[0] if row else 0
    tp  = row[1] if row else 0
    wr  = f"{tp/n*100:.0f}%" if n else "—"
    ready = "✅ active" if n >= 10 else f"⏳ need {10-n} more"
    if n >= 10:
        calib_ready += 1
    _p(f"  conf {label} : {n:4d} resolved  WR={wr:>5s}  {ready}")

_p(f"\n  Calibration active on {calib_ready}/4 confidence bands")

# Latest calibration report
cal_row = _q1(conn,
    "SELECT generated_at, json_blob FROM calibration_reports ORDER BY id DESC LIMIT 1")
if cal_row:
    try:
        cal = json.loads(cal_row[1])
        _p(f"  Last report : {cal_row[0][:16]}")
        _p(f"  Overconfident pairs : {len(cal.get('overconfident_pairs', []))}")
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# 10. DRIFT ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

_p("\n🔍  DRIFT ANALYSIS")
_p(SEP2)

try:
    from backtest.drift_analysis import DriftAnalyser
    drift = DriftAnalyser().analyse(lookback_days=DAYS)
    score = drift.get("drift_score", 0.0)
    rec   = drift.get("recommendation", "OK")
    dmr   = drift.get("direction_match_rate", 0.0)
    omr   = drift.get("outcome_match_rate", 0.0)
    gap   = drift.get("avg_confidence_gap", 0.0)
    nsig  = drift.get("total_signals", 0)
    icon  = "🔴" if rec == "HALT" else "🟡" if rec == "RECALIBRATE" else "🟢"
    _p(f"  {icon} Recommendation : {rec}")
    _p(f"  Drift score          : {score:.3f}  (HALT ≥ 0.40 | RECALIBRATE ≥ 0.20)")
    _p(f"  Direction match rate : {dmr:.1%}")
    _p(f"  Outcome match rate   : {omr:.1%}")
    _p(f"  Avg confidence gap   : {gap:+.3f}  (+ = overconfident)")
    _p(f"  Signals analysed     : {nsig}")
except Exception as e:
    _p(f"  Drift analysis skipped: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# 11. SCHEDULER HEALTH
# ─────────────────────────────────────────────────────────────────────────────

_p("\n⚙️   SCHEDULER HEALTH  (last known run)")
_p(SEP2)

status = _j(os.path.join(_LOGS, "scheduler_status.json"))
KEY_JOBS = [
    "daily_scan", "morning_digest", "outcome_tracker",
    "price_monitor", "fno_monitor", "eod_digest",
    "crypto_scan", "us_scan", "weekly_optimizer",
    "earnings_refresh", "ohlcv_update",
]
any_error = False
for job in KEY_JOBS:
    info = status.get(job, {})
    st   = info.get("state", "never")
    ts   = info.get("updated_at", "")[:16] if info.get("updated_at") else "—"
    det  = info.get("detail", "")
    icon = "✅" if st == "ok" else "⚠️" if st in ("error", "never") else "🔵"
    if st == "error":
        any_error = True
    suffix = f"  [{det[:50]}]" if det and st != "ok" else ""
    _p(f"  {icon} {job:22s} {st:10s} {ts}{suffix}")

if any_error:
    _p("\n  ⚠ Some jobs had errors — check logs/scheduler.log for details")

# ─────────────────────────────────────────────────────────────────────────────
# 12. RECENT SIGNALS (last 5)
# ─────────────────────────────────────────────────────────────────────────────

_p(f"\n📋  RECENT SIGNALS  (last 5 BUY)")
_p(SEP2)

recent_sigs = _q(conn,
    "SELECT timestamp, symbol, confidence, ta_score, entry_price, "
    "       outcome, regime_tag "
    "FROM signals WHERE action='BUY' ORDER BY timestamp DESC LIMIT 5")
for ts, sym, conf, ta, ep, outcome, regime in recent_sigs:
    out_str = outcome or "OPEN"
    _p(f"  {ts[:16]}  {sym:15s}  conf={conf:.0%}  TA={ta:.1f}"
       f"  ep=Rs.{ep:,.0f}  {out_str}  [{regime or '?'}]")

# ─────────────────────────────────────────────────────────────────────────────
# FOOTER
# ─────────────────────────────────────────────────────────────────────────────

conn.close()

_p("\n" + SEP)
_p(f"  Generated : {datetime.now().strftime('%d %b %Y  %H:%M:%S IST')}")
_p(f"  DB        : {SQLITE_DB_FILE}")
_p(SEP)

# ─────────────────────────────────────────────────────────────────────────────
# OPTIONAL TELEGRAM SEND
# ─────────────────────────────────────────────────────────────────────────────

if args.send:
    try:
        from utils.telegram import send
        # Condense to a Telegram-friendly version (key metrics only)
        tg_lines = [
            f"*📊 QuantEdge — {DAYS}-Day Review*",
            f"_{CUTOFF} → {TODAY}_",
            "",
            f"*NSE Equity*",
            f"Signals: {sig_total} gen | {sig_exec} executed",
            f"Trades: {nse_count} closed | W:{wins} L:{losses}" +
            (f" | WR:{wins/nse_count*100:.0f}%" if nse_count else ""),
            f"P&L: `Rs.{nse_pnl:+,.0f}`",
            "",
            f"*F&O Paper*: {fno_count} trades | `Rs.{fno_pnl:+,.0f}`",
            f"*Crypto*: {cry_count} trades | `{cry_pnl:+.2f} USDT`",
            f"*US Stocks*: {us_count} trades | `${us_pnl:+.2f}`",
            "",
            f"*Combined P&L: `Rs.{combined:+,.0f}`*",
            f"Portfolio: `Rs.{portfolio_value:,.0f}`",
            "",
        ]

        if total_resolved:
            tp_c = next((r[1] for r in outcome_rows if r[0] == "TP_HIT"), 0)
            tg_lines.append(f"*Signal accuracy*: {tp_c/total_resolved*100:.1f}% TP rate ({total_resolved} resolved)")

        tg_lines += [
            "",
            f"*Drift*: score={score:.3f} → {rec}",
            f"*Calib active*: {calib_ready}/4 bands",
            "",
            f"_Run `python scripts/review.py --days {DAYS}` for full report_",
        ]
        send("\n".join(tg_lines))
        print("\n✅ Summary sent to Telegram")
    except Exception as e:
        print(f"\n⚠ Telegram send failed: {e}")
