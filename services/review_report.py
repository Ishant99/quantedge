import json
import os
from datetime import datetime


REVIEW_REPORT_JSON = os.path.join("logs", "agent_review_report.json")
REVIEW_REPORT_MD = os.path.join("logs", "agent_review_report.md")


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _fmt_inr(value) -> str:
    amount = _safe_float(value)
    sign = "+" if amount > 0 else ""
    return f"Rs.{sign}{amount:,.2f}"


def _fmt_pct(value) -> str:
    amount = _safe_float(value)
    sign = "+" if amount > 0 else ""
    return f"{sign}{amount:.2f}%"


def build_review_report(state: dict) -> dict:
    summary = state.get("summary") or {}
    positions = list(state.get("positions") or [])
    trades = list(state.get("trades") or [])
    signals = list(state.get("signals") or [])

    open_positions = [row for row in positions if str(row.get("status", "open")).lower() == "open"]
    closed_trades = [row for row in trades if str(row.get("status", "")).lower() == "closed"]
    executed_signals = [row for row in signals if int(row.get("executed", 0) or 0) == 1]

    open_positions.sort(key=lambda item: _safe_float(item.get("pnl")), reverse=True)
    latest_signals = sorted(signals, key=lambda item: item.get("timestamp") or "", reverse=True)[:10]
    latest_trades = sorted(trades, key=lambda item: item.get("exit_time") or item.get("entry_time") or "", reverse=True)[:10]

    by_market = {}
    for row in open_positions:
        market = (row.get("market") or "other").upper()
        bucket = by_market.setdefault(market, {"count": 0, "open_pnl": 0.0})
        bucket["count"] += 1
        bucket["open_pnl"] += _safe_float(row.get("pnl"))

    top_winners = sorted(open_positions, key=lambda item: _safe_float(item.get("pnl")), reverse=True)[:5]
    top_losers = sorted(open_positions, key=lambda item: _safe_float(item.get("pnl")))[:5]

    return {
        "generated_at": datetime.now().isoformat(),
        "synced_at": state.get("synced_at", ""),
        "summary": {
            "combined_open_positions": int(summary.get("combined_open_positions", len(open_positions)) or 0),
            "combined_open_pnl_inr": round(_safe_float(summary.get("combined_open_pnl_inr")), 2),
            "nse_cash": round(_safe_float(summary.get("nse_cash")), 2),
            "nse_total_trades": int(summary.get("nse_total_trades", 0) or 0),
            "nse_wins": int(summary.get("nse_wins", 0) or 0),
            "nse_total_pnl": round(_safe_float(summary.get("nse_total_pnl")), 2),
            "fno_open_positions": int(summary.get("fno_open_positions", 0) or 0),
            "fno_total_pnl": round(_safe_float(summary.get("fno_total_pnl")), 2),
            "crypto_open_positions": int(summary.get("crypto_open_positions", 0) or 0),
            "crypto_total_pnl": round(_safe_float(summary.get("crypto_total_pnl")), 4),
            "us_open_positions": int(summary.get("us_open_positions", 0) or 0),
            "us_total_pnl": round(_safe_float(summary.get("us_total_pnl")), 4),
            "executed_signal_count": len(executed_signals),
            "closed_trade_count": len(closed_trades),
        },
        "markets": by_market,
        "top_winners": top_winners,
        "top_losers": top_losers,
        "latest_signals": latest_signals,
        "latest_trades": latest_trades,
        "open_positions": open_positions[:25],
    }


def render_review_markdown(report: dict) -> str:
    summary = report.get("summary") or {}
    markets = report.get("markets") or {}
    lines = [
        "# QuantEdge Agent Review",
        "",
        f"- Generated: `{report.get('generated_at', '')}`",
        f"- Synced state: `{report.get('synced_at', '')}`",
        f"- Open positions: `{summary.get('combined_open_positions', 0)}`",
        f"- Open P&L: `{_fmt_inr(summary.get('combined_open_pnl_inr', 0))}`",
        f"- NSE realized P&L: `{_fmt_inr(summary.get('nse_total_pnl', 0))}`",
        f"- NSE cash: `{_fmt_inr(summary.get('nse_cash', 0))}`",
        f"- Executed signals tracked: `{summary.get('executed_signal_count', 0)}`",
        f"- Closed trades tracked: `{summary.get('closed_trade_count', 0)}`",
        "",
        "## Market Exposure",
        "",
    ]

    if markets:
        for market, bucket in sorted(markets.items()):
            lines.append(
                f"- {market}: `{bucket.get('count', 0)}` open | `{_fmt_inr(bucket.get('open_pnl', 0))}`"
            )
    else:
        lines.append("- No open positions")

    lines.extend(["", "## Top Winners", ""])
    winners = report.get("top_winners") or []
    if winners:
        for row in winners:
            lines.append(
                f"- {row.get('instrument', row.get('symbol', ''))}: `{_fmt_inr(row.get('pnl', 0))}` | "
                f"entry `{row.get('entry_price', 0)}` -> now `{row.get('current_price', 0)}`"
            )
    else:
        lines.append("- No open winners")

    lines.extend(["", "## Top Losers", ""])
    losers = report.get("top_losers") or []
    if losers:
        for row in losers:
            lines.append(
                f"- {row.get('instrument', row.get('symbol', ''))}: `{_fmt_inr(row.get('pnl', 0))}` | "
                f"entry `{row.get('entry_price', 0)}` -> now `{row.get('current_price', 0)}`"
            )
    else:
        lines.append("- No open losers")

    lines.extend(["", "## Latest Signals", ""])
    for row in report.get("latest_signals") or []:
        lines.append(
            f"- {row.get('timestamp', '')} | {row.get('market', '').upper()} | {row.get('symbol', '')} "
            f"{row.get('action', '')} | conf `{_fmt_pct(_safe_float(row.get('confidence')) * 100)}` | "
            f"executed `{row.get('executed', 0)}`"
        )
    if not (report.get("latest_signals") or []):
        lines.append("- No signals tracked")

    lines.extend(["", "## Latest Trades", ""])
    for row in report.get("latest_trades") or []:
        lines.append(
            f"- {row.get('exit_time') or row.get('entry_time', '')} | {row.get('market', '').upper()} | "
            f"{row.get('instrument', row.get('symbol', ''))} | status `{row.get('status', '')}` | "
            f"P&L `{_fmt_inr(row.get('pnl', 0))}`"
        )
    if not (report.get("latest_trades") or []):
        lines.append("- No trades tracked")

    lines.extend(["", "## Open Positions", ""])
    for row in report.get("open_positions") or []:
        lines.append(
            f"- {row.get('market', '').upper()} | {row.get('instrument', row.get('symbol', ''))} | "
            f"{row.get('side', '')} | qty `{row.get('quantity', 0)}` | "
            f"P&L `{_fmt_inr(row.get('pnl', 0))}`"
        )
    if not (report.get("open_positions") or []):
        lines.append("- No open positions")

    return "\n".join(lines) + "\n"


def write_review_report(state: dict) -> dict:
    os.makedirs("logs", exist_ok=True)
    report = build_review_report(state)
    with open(REVIEW_REPORT_JSON, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    with open(REVIEW_REPORT_MD, "w", encoding="utf-8") as handle:
        handle.write(render_review_markdown(report))
    return report

