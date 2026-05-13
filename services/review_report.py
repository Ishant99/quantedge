import json
import os
from datetime import datetime

_PROJECT_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGS_DIR          = os.path.join(_PROJECT_ROOT, "logs")
REVIEW_REPORT_JSON = os.path.join(_LOGS_DIR, "agent_review_report.json")
REVIEW_REPORT_MD   = os.path.join(_LOGS_DIR, "agent_review_report.md")


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


def _fmt_money(value, market: str = "nse") -> str:
    amount = _safe_float(value)
    sign = "+" if amount > 0 else ""
    market_key = (market or "").lower()
    if market_key == "us":
        return f"${sign}{amount:,.2f}"
    if market_key == "crypto":
        return f"USDT {sign}{amount:,.4f}"
    return f"Rs.{sign}{amount:,.2f}"


def _group_by_market(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {"nse": [], "fno": [], "us": [], "crypto": [], "other": []}
    for row in rows:
        market = str(row.get("market", "other") or "other").lower()
        grouped.setdefault(market, []).append(row)
    return grouped


def _capital_summary(rows: list[dict], field: str) -> float:
    return round(sum(_safe_float(row.get(field)) for row in rows), 4)


def build_review_report(state: dict) -> dict:
    summary = state.get("summary") or {}
    treasury = state.get("treasury") or {}
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

    open_by_market = _group_by_market(open_positions)
    trades_by_market = _group_by_market(trades)
    signals_by_market = _group_by_market(signals)

    market_sections = {}
    for market in ["nse", "fno", "us", "crypto", "other"]:
        pos_rows = sorted(open_by_market.get(market, []), key=lambda item: _safe_float(item.get("pnl")), reverse=True)
        trade_rows = sorted(
            trades_by_market.get(market, []),
            key=lambda item: item.get("exit_time") or item.get("entry_time") or "",
            reverse=True,
        )[:8]
        signal_rows = sorted(
            signals_by_market.get(market, []),
            key=lambda item: item.get("timestamp") or "",
            reverse=True,
        )[:8]
        market_sections[market] = {
            "open_position_count": len(pos_rows),
            "open_pnl": round(sum(_safe_float(row.get("pnl")) for row in pos_rows), 4),
            "open_capital_usd": _capital_summary(pos_rows, "capital") if market == "us" else 0.0,
            "open_capital_usdt": _capital_summary(pos_rows, "capital") if market == "crypto" else 0.0,
            "positions": pos_rows[:10],
            "latest_trades": trade_rows,
            "latest_signals": signal_rows,
        }

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
            "shared_nse_cash_pool_only": False,
        },
        "treasury": treasury,
        "markets": by_market,
        "market_sections": market_sections,
        "top_winners": top_winners,
        "top_losers": top_losers,
        "latest_signals": latest_signals,
        "latest_trades": latest_trades,
        "open_positions": open_positions[:25],
        "warnings": list(treasury.get("warnings") or []),
    }


def render_review_markdown(report: dict) -> str:
    summary = report.get("summary") or {}
    markets = report.get("markets") or {}
    market_sections = report.get("market_sections") or {}
    treasury = report.get("treasury") or {}
    lines = [
        "# QuantEdge Agent Review",
        "",
        f"- Generated: `{report.get('generated_at', '')}`",
        f"- Synced state: `{report.get('synced_at', '')}`",
        f"- Open positions: `{summary.get('combined_open_positions', 0)}`",
        f"- Open P&L: `{_fmt_inr(summary.get('combined_open_pnl_inr', 0))}`",
        f"- Treasury cash before reserve: `{_fmt_inr(treasury.get('available_cash_before_reserve_inr', 0))}`",
        f"- Treasury reserved cash: `{_fmt_inr(treasury.get('reserved_cash_inr', 0))}`",
        f"- Treasury over allocation: `{_fmt_inr(treasury.get('over_allocation_inr', 0))}`",
        f"- Treasury spendable cash: `{_fmt_inr(treasury.get('spendable_cash_inr', 0))}`",
        f"- Total equity: `{_fmt_inr(treasury.get('total_equity_inr', 0))}`",
        f"- NSE realized P&L: `{_fmt_inr(summary.get('nse_total_pnl', 0))}`",
        f"- NSE cash: `{_fmt_inr(summary.get('nse_cash', 0))}`",
        f"- Executed signals tracked: `{summary.get('executed_signal_count', 0)}`",
        f"- Closed trades tracked: `{summary.get('closed_trade_count', 0)}`",
        "- Funding note: `NSE, F&O, US, and Crypto now share one paper treasury view; new positions are checked against the same spendable cash pool before entry.`",
        "",
        "## Market Exposure",
        "",
    ]

    warnings = report.get("warnings") or []
    if warnings:
        lines.extend(["## Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")

    if markets:
        for market, bucket in sorted(markets.items()):
            lines.append(
                f"- {market}: `{bucket.get('count', 0)}` open | `{_fmt_inr(bucket.get('open_pnl', 0))}`"
            )
    else:
        lines.append("- No open positions")

    lines.extend(["", "## Treasury Allocation", ""])
    deployed = treasury.get("market_deployed_inr") or {}
    limits = treasury.get("market_allocation_limits_inr") or {}
    for market_key, label in [("nse", "NSE"), ("fno", "F&O"), ("us", "US Stocks"), ("crypto", "Crypto")]:
        lines.append(
            f"- {label}: deployed `{_fmt_inr(deployed.get(market_key, 0))}` | "
            f"cap `{_fmt_inr(limits.get(market_key, 0))}`"
        )

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

    lines.extend(["", "## Market Breakdown", ""])
    ordered_labels = [("nse", "NSE"), ("fno", "F&O"), ("us", "US Stocks"), ("crypto", "Crypto"), ("other", "Other")]
    for market_key, label in ordered_labels:
        section = market_sections.get(market_key) or {}
        section_positions = section.get("positions") or []
        section_trades = section.get("latest_trades") or []
        section_signals = section.get("latest_signals") or []
        lines.append(f"### {label}")
        lines.append("")
        lines.append(f"- Open positions: `{section.get('open_position_count', 0)}`")
        lines.append(f"- Open P&L: `{_fmt_money(section.get('open_pnl', 0), market_key)}`")
        if market_key == "us":
            lines.append(f"- Open capital allocated: `{_fmt_money(section.get('open_capital_usd', 0), 'us')}`")
        if market_key == "crypto":
            lines.append(f"- Open capital allocated: `{_fmt_money(section.get('open_capital_usdt', 0), 'crypto')}`")

        lines.append("- Open positions:")
        if section_positions:
            for row in section_positions:
                lines.append(
                    f"  - {row.get('instrument', row.get('symbol', ''))} | {row.get('side', '')} | "
                    f"qty `{row.get('quantity', 0)}` | P&L `{_fmt_money(row.get('pnl', 0), market_key)}`"
                )
        else:
            lines.append("  - None")

        lines.append("- Latest trades:")
        if section_trades:
            for row in section_trades[:5]:
                lines.append(
                    f"  - {row.get('exit_time') or row.get('entry_time', '')} | "
                    f"{row.get('instrument', row.get('symbol', ''))} | status `{row.get('status', '')}` | "
                    f"P&L `{_fmt_money(row.get('pnl', 0), market_key)}`"
                )
        else:
            lines.append("  - None")

        lines.append("- Latest signals:")
        if section_signals:
            for row in section_signals[:5]:
                lines.append(
                    f"  - {row.get('timestamp', '')} | {row.get('symbol', '')} {row.get('action', '')} | "
                    f"executed `{row.get('executed', 0)}`"
                )
        else:
            lines.append("  - None")
        lines.append("")

    return "\n".join(lines) + "\n"


def write_review_report(state: dict) -> dict:
    os.makedirs(_LOGS_DIR, exist_ok=True)
    report = build_review_report(state)
    with open(REVIEW_REPORT_JSON, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    with open(REVIEW_REPORT_MD, "w", encoding="utf-8") as handle:
        handle.write(render_review_markdown(report))
    return report
