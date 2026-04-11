# =============================================================================
# automation/weekly_summary.py
# Sends a rich weekly performance summary to Telegram every Sunday 8 PM IST.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import sqlite3
from datetime import datetime, timedelta
from config import VIRTUAL_CAPITAL, SQLITE_DB_FILE, VIRTUAL_PORTFOLIO_FILE
from utils import get_logger
from utils.telegram import send

logger = get_logger("WeeklySummary")


def _fetch_live_prices(symbols):
    """Best-effort batch fetch of current NSE prices via yfinance.
    Returns {symbol: last_price}. Silently skips failures."""
    out = {}
    if not symbols:
        return out
    try:
        import yfinance as yf
    except Exception:
        return out
    for sym in symbols:
        try:
            h = yf.Ticker(f"{sym}.NS").history(period="1d", interval="15m")
            if not h.empty:
                out[sym] = float(h["Close"].iloc[-1])
        except Exception:
            continue
    return out


def send_weekly_summary():
    try:
        _send()
    except Exception as e:
        logger.error(f"Weekly summary failed: {e}")
        send(f"Weekly summary error: {e}")


def build_period_report(start_date: str, end_date: str = None) -> str:
    """
    Build a downloadable trading report for an arbitrary date range.

    Args:
        start_date: ISO date 'YYYY-MM-DD' (inclusive)
        end_date:   ISO date 'YYYY-MM-DD' (inclusive). Defaults to today.

    Returns:
        Markdown-formatted report as a string.
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    return _build_report(start_date, end_date)


def _send():
    week_label = datetime.now().strftime("%d %b %Y")
    week_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # NSE equity portfolio
    # ------------------------------------------------------------------
    initial_capital = VIRTUAL_CAPITAL
    portfolio_value = initial_capital
    open_positions  = {}

    if os.path.exists(VIRTUAL_PORTFOLIO_FILE):
        with open(VIRTUAL_PORTFOLIO_FILE) as f:
            pf = json.load(f)
        cash           = pf.get("cash", initial_capital)
        open_positions = pf.get("positions", {})
        mtm            = sum(p.get("entry", 0) * p.get("qty", 0)
                             for p in open_positions.values())
        portfolio_value = cash + mtm
    else:
        cash = initial_capital

    pnl     = portfolio_value - initial_capital
    pnl_pct = pnl / initial_capital * 100

    # ------------------------------------------------------------------
    # NSE equity trades this week
    # ------------------------------------------------------------------
    week_trades   = []
    total_signals = 0

    if os.path.exists(SQLITE_DB_FILE):
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            conn.row_factory = sqlite3.Row

            # NSE equity trades
            try:
                rows = conn.execute("""
                    SELECT symbol, action, entry_price, exit_price,
                           pnl, pnl_pct, exit_time
                    FROM trades
                    WHERE status='closed' AND exit_time >= ?
                    ORDER BY exit_time DESC
                """, (week_start,)).fetchall()
                week_trades = [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass

            try:
                total_signals = conn.execute(
                    "SELECT COUNT(*) FROM signals WHERE timestamp >= ?",
                    (week_start,)
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass

    wins   = [t for t in week_trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in week_trades if (t.get("pnl") or 0) <= 0]
    total_pnl_week = sum(t.get("pnl") or 0 for t in week_trades)
    win_rate = (len(wins) / len(week_trades) * 100) if week_trades else 0

    best  = max(week_trades, key=lambda t: t.get("pnl") or 0, default=None)
    worst = min(week_trades, key=lambda t: t.get("pnl") or 0, default=None)

    # ------------------------------------------------------------------
    # F&O trades this week
    # ------------------------------------------------------------------
    fno_week_pnl = 0
    fno_week_count = 0
    fno_open_count = 0

    if os.path.exists(SQLITE_DB_FILE):
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            try:
                row = conn.execute("""
                    SELECT COUNT(*), COALESCE(SUM(pnl),0)
                    FROM fno_trades WHERE status='closed' AND exit_time >= ?
                """, (week_start,)).fetchone()
                fno_week_count, fno_week_pnl = row[0], row[1]
                fno_open_count = conn.execute(
                    "SELECT COUNT(*) FROM fno_trades WHERE status='open'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass

    # ------------------------------------------------------------------
    # Crypto trades this week
    # ------------------------------------------------------------------
    crypto_week_pnl   = 0
    crypto_week_count = 0
    crypto_open_count = 0

    if os.path.exists(SQLITE_DB_FILE):
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            try:
                row = conn.execute("""
                    SELECT COUNT(*), COALESCE(SUM(pnl_usdt),0)
                    FROM crypto_trades WHERE status='closed' AND exit_time >= ?
                """, (week_start,)).fetchone()
                crypto_week_count, crypto_week_pnl = row[0], row[1]
                crypto_open_count = conn.execute(
                    "SELECT COUNT(*) FROM crypto_trades WHERE status='open'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass

    # ------------------------------------------------------------------
    # US trades this week
    # ------------------------------------------------------------------
    us_week_pnl   = 0
    us_week_count = 0
    us_open_count = 0

    if os.path.exists(SQLITE_DB_FILE):
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            try:
                row = conn.execute("""
                    SELECT COUNT(*), COALESCE(SUM(pnl_usd),0)
                    FROM us_trades WHERE status='closed' AND exit_time >= ?
                """, (week_start,)).fetchone()
                us_week_count, us_week_pnl = row[0], row[1]
                us_open_count = conn.execute(
                    "SELECT COUNT(*) FROM us_trades WHERE status='open'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass

    INR_RATE = 83.0
    combined_pnl = (total_pnl_week + fno_week_pnl +
                    crypto_week_pnl * INR_RATE + us_week_pnl * INR_RATE)

    # ------------------------------------------------------------------
    # Readiness gates
    # ------------------------------------------------------------------
    gates_passed = gates_total = 0
    readiness_file = "logs/readiness_report.json"
    if os.path.exists(readiness_file):
        with open(readiness_file) as f:
            r = json.load(f)
        gates_passed = r.get("passed", 0)
        gates_total  = r.get("total", 8)

    # ------------------------------------------------------------------
    # Build message
    # ------------------------------------------------------------------
    lines = [
        "*Weekly Trading Report*",
        f"_Week ending {week_label}_",
        "",
        "*NSE Equity Portfolio*",
        f"Value:  `Rs.{portfolio_value:>12,.0f}`",
        f"P&L:    `Rs.{pnl:>+12,.0f}` ({pnl_pct:+.2f}%)",
        f"Open positions: `{len(open_positions)}`",
        "",
        "*This Week — NSE Equity*",
        f"Signals generated: `{total_signals}`",
        f"Trades executed:   `{len(week_trades)}`",
        f"Wins / Losses:     `{len(wins)} / {len(losses)}`",
        f"Win rate:          `{win_rate:.0f}%`",
        f"Week P&L:          `Rs.{total_pnl_week:+,.0f}`",
    ]

    if best and best.get("pnl"):
        lines.append(f"Best:  `{best['symbol']}` `Rs.{best['pnl']:+,.0f}` ({best.get('pnl_pct', 0):+.1f}%)")
    if worst and worst.get("pnl"):
        lines.append(f"Worst: `{worst['symbol']}` `Rs.{worst['pnl']:+,.0f}` ({worst.get('pnl_pct', 0):+.1f}%)")

    lines += [
        "",
        "*This Week — F&O*",
        f"Trades: `{fno_week_count}` | Open: `{fno_open_count}`",
        f"P&L:    `Rs.{fno_week_pnl:+,.0f}`",
        "",
        "*This Week — Crypto*",
        f"Trades: `{crypto_week_count}` | Open: `{crypto_open_count}`",
        f"P&L:    `{crypto_week_pnl:+.2f} USDT` (Rs.{crypto_week_pnl * INR_RATE:+,.0f})",
        "",
        "*This Week — US Stocks*",
        f"Trades: `{us_week_count}` | Open: `{us_open_count}`",
        f"P&L:    `${us_week_pnl:+.2f}` (Rs.{us_week_pnl * INR_RATE:+,.0f})",
        "",
        f"*Combined Week P&L: `Rs.{combined_pnl:+,.0f}`*",
    ]

    if open_positions:
        lines += ["", "*Top NSE Open Positions*"]
        for sym, pos in list(open_positions.items())[:5]:
            lines.append(f"  {sym} | entry Rs.{pos.get('entry', 0):,.0f}")
        if len(open_positions) > 5:
            lines.append(f"  ...and {len(open_positions) - 5} more")

    if gates_total > 0:
        lines += [
            "",
            f"*Live Readiness: {gates_passed}/{gates_total} gates*",
            "Ready to go live!" if gates_passed == gates_total
            else f"~{max(0,(gates_total-gates_passed)*5)} more trading days needed",
        ]

    lines += ["", "_Keep the agent running daily!_"]

    send("\n".join(lines))
    logger.info(f"Weekly summary sent — {len(week_trades)} NSE + {fno_week_count} F&O + "
                f"{crypto_week_count} crypto + {us_week_count} US trades, "
                f"combined P&L Rs.{combined_pnl:+,.0f}")


def _build_report(start_date: str, end_date: str) -> str:
    """Build a trading report markdown string for an arbitrary date range."""
    # End-date is inclusive — query needs exit_time < end_date+1
    end_inclusive = (datetime.strptime(end_date, "%Y-%m-%d") +
                     timedelta(days=1)).strftime("%Y-%m-%d")

    # ----- NSE portfolio snapshot (live MTM, matches dashboard) -----
    initial_capital = VIRTUAL_CAPITAL
    open_positions  = {}
    cash            = initial_capital
    if os.path.exists(VIRTUAL_PORTFOLIO_FILE):
        with open(VIRTUAL_PORTFOLIO_FILE) as f:
            pf = json.load(f)
        cash           = pf.get("cash", initial_capital)
        open_positions = pf.get("positions", {})

    # Fetch live prices for all open NSE positions (best effort)
    live_prices = _fetch_live_prices(list(open_positions.keys()))

    invested_cost = 0.0   # entry * qty   (original cost basis)
    live_mtm      = 0.0   # current price * qty
    unrealized    = 0.0   # (current - entry) * qty
    position_rows = []    # for "Top Open Positions" section
    for sym, p in open_positions.items():
        entry = p.get("entry", 0) or 0
        qty   = p.get("qty", 0) or 0
        curr  = live_prices.get(sym, entry)
        cost  = entry * qty
        mkt   = curr * qty
        unr   = (curr - entry) * qty
        invested_cost += cost
        live_mtm      += mkt
        unrealized    += unr
        position_rows.append({
            "symbol": sym, "qty": qty, "entry": entry,
            "current": curr, "cost": cost, "mtm": mkt,
            "unrealized": unr,
            "pct": ((curr - entry) / entry * 100) if entry else 0,
            "live": sym in live_prices,
        })
    # Sort biggest losers first so they surface at the top of the report
    position_rows.sort(key=lambda r: r["unrealized"])

    portfolio_value = cash + live_mtm
    pnl_total       = portfolio_value - initial_capital
    pnl_total_pct   = (pnl_total / initial_capital * 100) if initial_capital else 0
    prices_ok       = bool(live_prices) or not open_positions

    # ----- Trades in period (all markets, full rows) -----
    nse_trades, nse_signals = [], 0
    fno_trades, crypto_trades, us_trades = [], [], []

    if os.path.exists(SQLITE_DB_FILE):
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("""
                    SELECT symbol, action, qty, entry_price, exit_price,
                           pnl, pnl_pct, entry_time, exit_time
                    FROM trades
                    WHERE status='closed' AND exit_time >= ? AND exit_time < ?
                    ORDER BY exit_time DESC
                """, (start_date, end_inclusive)).fetchall()
                nse_trades = [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass
            try:
                nse_signals = conn.execute(
                    "SELECT COUNT(*) FROM signals WHERE timestamp >= ? AND timestamp < ?",
                    (start_date, end_inclusive)
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass
            try:
                rows = conn.execute("""
                    SELECT instrument, option_type, strike, expiry, lots, lot_size,
                           entry_premium, exit_premium, pnl, pnl_pct,
                           entry_time, exit_time, exit_reason
                    FROM fno_trades
                    WHERE status='closed' AND exit_time >= ? AND exit_time < ?
                    ORDER BY exit_time DESC
                """, (start_date, end_inclusive)).fetchall()
                fno_trades = [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass
            try:
                rows = conn.execute("""
                    SELECT symbol, direction, qty, entry_price, exit_price,
                           pnl_usdt, pnl_pct, entry_time, exit_time, exit_reason
                    FROM crypto_trades
                    WHERE status='closed' AND exit_time >= ? AND exit_time < ?
                    ORDER BY exit_time DESC
                """, (start_date, end_inclusive)).fetchall()
                crypto_trades = [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass
            try:
                rows = conn.execute("""
                    SELECT symbol, direction, qty, entry_price, exit_price,
                           pnl_usd, pnl_pct, entry_time, exit_time, exit_reason
                    FROM us_trades
                    WHERE status='closed' AND exit_time >= ? AND exit_time < ?
                    ORDER BY exit_time DESC
                """, (start_date, end_inclusive)).fetchall()
                us_trades = [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass

    wins   = [t for t in nse_trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in nse_trades if (t.get("pnl") or 0) <= 0]
    nse_pnl = sum(t.get("pnl") or 0 for t in nse_trades)
    win_rate = (len(wins) / len(nse_trades) * 100) if nse_trades else 0
    best  = max(nse_trades, key=lambda t: t.get("pnl") or 0, default=None)
    worst = min(nse_trades, key=lambda t: t.get("pnl") or 0, default=None)

    fno_count    = len(fno_trades)
    fno_pnl      = sum(t.get("pnl") or 0 for t in fno_trades)
    crypto_count = len(crypto_trades)
    crypto_pnl   = sum(t.get("pnl_usdt") or 0 for t in crypto_trades)
    us_count     = len(us_trades)
    us_pnl       = sum(t.get("pnl_usd") or 0 for t in us_trades)

    INR_RATE = 83.0
    realized_combined = (nse_pnl + fno_pnl +
                         crypto_pnl * INR_RATE + us_pnl * INR_RATE)
    # True P&L includes live unrealized MTM on open NSE equity positions
    true_combined = realized_combined + unrealized

    # ----- Build markdown -----
    price_note = "" if prices_ok else " _(live price fetch failed — using entry as fallback)_"
    lines = [
        f"# Trading Report",
        f"_{start_date} → {end_date}_",
        "",
        "## NSE Equity Portfolio (live snapshot)" + price_note,
        f"- Cash: Rs.{cash:,.0f}",
        f"- Open MTM (live): Rs.{live_mtm:,.0f}",
        f"- Portfolio value: Rs.{portfolio_value:,.0f}",
        f"- All-time P&L: Rs.{pnl_total:+,.0f} ({pnl_total_pct:+.2f}%)",
        f"- Open positions: {len(open_positions)}",
        f"- Unrealized P&L on open positions: Rs.{unrealized:+,.0f}",
        "",
        "## Period — NSE Equity (realized)",
        f"- Signals generated: {nse_signals}",
        f"- Trades closed: {len(nse_trades)}",
        f"- Wins / Losses: {len(wins)} / {len(losses)}",
        f"- Win rate: {win_rate:.0f}%",
        f"- Realized P&L: Rs.{nse_pnl:+,.0f}",
    ]
    if best and best.get("pnl"):
        lines.append(f"- Best:  {best['symbol']} Rs.{best['pnl']:+,.0f} ({best.get('pnl_pct', 0):+.1f}%)")
    if worst and worst.get("pnl"):
        lines.append(f"- Worst: {worst['symbol']} Rs.{worst['pnl']:+,.0f} ({worst.get('pnl_pct', 0):+.1f}%)")

    lines += [
        "",
        "## Period — F&O",
        f"- Trades: {fno_count}",
        f"- P&L: Rs.{fno_pnl:+,.0f}",
        "",
        "## Period — Crypto",
        f"- Trades: {crypto_count}",
        f"- P&L: {crypto_pnl:+.2f} USDT (≈ Rs.{crypto_pnl * INR_RATE:+,.0f})",
        "",
        "## Period — US Stocks",
        f"- Trades: {us_count}",
        f"- P&L: ${us_pnl:+.2f} (≈ Rs.{us_pnl * INR_RATE:+,.0f})",
        "",
        "## P&L Summary",
        f"- Realized (period, all markets): Rs.{realized_combined:+,.0f}",
        f"- NSE open unrealized (live MTM): Rs.{unrealized:+,.0f}",
        f"- **True combined P&L: Rs.{true_combined:+,.0f}**",
        "",
    ]

    # Open NSE positions (live MTM) — biggest losers first
    if position_rows:
        lines += [
            "## Open NSE Positions (live MTM)",
            "",
            "| Symbol | Qty | Entry | Current | Cost | MTM | Unrealized | % |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in position_rows:
            mark = "" if r["live"] else " *"
            lines.append(
                f"| {r['symbol']}{mark} | {r['qty']} | "
                f"{r['entry']:.2f} | {r['current']:.2f} | "
                f"Rs.{r['cost']:,.0f} | Rs.{r['mtm']:,.0f} | "
                f"Rs.{r['unrealized']:+,.0f} | {r['pct']:+.2f}% |"
            )
        if any(not r["live"] for r in position_rows):
            lines.append("\n_\\* = live price unavailable, using entry as fallback_")
        lines.append("")

    if nse_trades:
        lines += [
            "## NSE Closed Trades", "",
            "| Exit Date | Symbol | Qty | Entry | Exit | P&L | P&L % |",
            "|---|---|---|---|---|---|---|",
        ]
        for t in nse_trades[:50]:
            exit_dt = (t.get("exit_time") or "")[:10]
            lines.append(
                f"| {exit_dt} | {t.get('symbol','')} | {t.get('qty', 0)} | "
                f"{t.get('entry_price', 0):.2f} | {t.get('exit_price', 0):.2f} | "
                f"Rs.{(t.get('pnl') or 0):+,.0f} | {(t.get('pnl_pct') or 0):+.2f}% |"
            )
        if len(nse_trades) > 50:
            lines.append(f"\n_…and {len(nse_trades)-50} more trades_")
        lines.append("")

    if fno_trades:
        lines += [
            "## F&O Closed Trades", "",
            "| Exit Date | Instrument | Type | Strike | Expiry | Lots | Entry | Exit | P&L | P&L % | Reason |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for t in fno_trades[:100]:
            exit_dt = (t.get("exit_time") or "")[:10]
            lines.append(
                f"| {exit_dt} | {t.get('instrument','')} | "
                f"{t.get('option_type','') or '-'} | "
                f"{t.get('strike','') or '-'} | "
                f"{t.get('expiry','') or '-'} | "
                f"{t.get('lots', 0)} | "
                f"{(t.get('entry_premium') or 0):.2f} | "
                f"{(t.get('exit_premium') or 0):.2f} | "
                f"Rs.{(t.get('pnl') or 0):+,.0f} | "
                f"{(t.get('pnl_pct') or 0):+.2f}% | "
                f"{(t.get('exit_reason') or '-')} |"
            )
        if len(fno_trades) > 100:
            lines.append(f"\n_…and {len(fno_trades)-100} more trades_")
        lines.append("")

    if crypto_trades:
        lines += [
            "## Crypto Closed Trades", "",
            "| Exit Date | Symbol | Dir | Qty | Entry | Exit | P&L (USDT) | P&L % | Reason |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for t in crypto_trades[:100]:
            exit_dt = (t.get("exit_time") or "")[:10]
            lines.append(
                f"| {exit_dt} | {t.get('symbol','')} | "
                f"{t.get('direction','')} | "
                f"{(t.get('qty') or 0):.4f} | "
                f"{(t.get('entry_price') or 0):.4f} | "
                f"{(t.get('exit_price') or 0):.4f} | "
                f"{(t.get('pnl_usdt') or 0):+.2f} | "
                f"{(t.get('pnl_pct') or 0):+.2f}% | "
                f"{(t.get('exit_reason') or '-')} |"
            )
        if len(crypto_trades) > 100:
            lines.append(f"\n_…and {len(crypto_trades)-100} more trades_")
        lines.append("")

    if us_trades:
        lines += [
            "## US Closed Trades", "",
            "| Exit Date | Symbol | Dir | Qty | Entry | Exit | P&L ($) | P&L % | Reason |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for t in us_trades[:100]:
            exit_dt = (t.get("exit_time") or "")[:10]
            lines.append(
                f"| {exit_dt} | {t.get('symbol','')} | "
                f"{t.get('direction','')} | "
                f"{(t.get('qty') or 0):.2f} | "
                f"{(t.get('entry_price') or 0):.2f} | "
                f"{(t.get('exit_price') or 0):.2f} | "
                f"${(t.get('pnl_usd') or 0):+.2f} | "
                f"{(t.get('pnl_pct') or 0):+.2f}% | "
                f"{(t.get('exit_reason') or '-')} |"
            )
        if len(us_trades) > 100:
            lines.append(f"\n_…and {len(us_trades)-100} more trades_")
        lines.append("")

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    send_weekly_summary()
