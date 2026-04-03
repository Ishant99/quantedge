import json
import os
import sqlite3
from datetime import datetime

import settings.manager as S
from config import SQLITE_DB_FILE, VIRTUAL_CAPITAL, VIRTUAL_PORTFOLIO_FILE
from utils import get_logger


logger = get_logger("PaperTreasury")
TREASURY_FILE = os.path.join("logs", "paper_treasury.json")


def _cfg(key: str, default=None):
    return S.get(key, default)


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            pass
    return default


def reserve_for_position(row: dict) -> float:
    market = str(row.get("market", "")).lower()
    if market == "us":
        return round(_safe_float(row.get("capital")) * _safe_float(_cfg("INR_PER_USD", 83.0), 83.0), 2)
    if market == "crypto":
        return round(_safe_float(row.get("capital")) * _safe_float(_cfg("INR_PER_USDT", 83.0), 83.0), 2)
    if market == "fno":
        entry = _safe_float(row.get("entry_price"))
        qty = _safe_float(row.get("quantity"))
        strategy = str(row.get("side", "") or row.get("strategy", "")).upper()
        notional = entry * qty
        if strategy.startswith("FUT-"):
            return round(notional * _safe_float(_cfg("FNO_FUT_MARGIN_PCT", 0.15), 0.15), 2)
        if strategy.startswith("SELL-"):
            return round(notional * _safe_float(_cfg("FNO_SELL_RESERVE_MULT", 2.5), 2.5), 2)
        return round(notional, 2)
    return round(_safe_float(row.get("entry_price")) * _safe_float(row.get("quantity")), 2)


def reserve_for_fno_order(index: str, option_type: str, entry_price: float, qty: float) -> float:
    row = {
        "market": "fno",
        "side": option_type,
        "entry_price": entry_price,
        "quantity": qty,
    }
    return reserve_for_position(row)


def reserve_for_us_order(usd_amount: float) -> float:
    return round(_safe_float(usd_amount) * _safe_float(_cfg("INR_PER_USD", 83.0), 83.0), 2)


def reserve_for_crypto_order(usdt_amount: float) -> float:
    return round(_safe_float(usdt_amount) * _safe_float(_cfg("INR_PER_USDT", 83.0), 83.0), 2)


def _empty_state() -> dict:
    return {"synced_at": "", "summary": {}, "positions": [], "trades": [], "signals": []}


def _fresh_state() -> dict:
    try:
        from services.state_sync import compose_unified_state
        return compose_unified_state()
    except Exception as exc:
        logger.warning(f"Could not compose fresh unified state for treasury: {exc}")
        return _load_json(os.path.join("logs", "unified_state.json"), _empty_state())


def build_treasury_snapshot(state: dict | None = None) -> dict:
    state = state or _fresh_state()
    positions = list(state.get("positions") or [])
    trades = list(state.get("trades") or [])
    summary = state.get("summary") or {}

    start_capital = _safe_float(_cfg("VIRTUAL_CAPITAL", VIRTUAL_CAPITAL), VIRTUAL_CAPITAL)
    nse_cash = _safe_float(summary.get("nse_cash"), start_capital)

    closed_fno_pnl = sum(_safe_float(t.get("pnl")) for t in trades if t.get("market") == "fno" and str(t.get("status", "")).lower() == "closed")
    closed_us_pnl_inr = sum(_safe_float(t.get("pnl")) * _safe_float(_cfg("INR_PER_USD", 83.0), 83.0) for t in trades if t.get("market") == "us" and str(t.get("status", "")).lower() == "closed")
    closed_crypto_pnl_inr = sum(_safe_float(t.get("pnl")) * _safe_float(_cfg("INR_PER_USDT", 83.0), 83.0) for t in trades if t.get("market") == "crypto" and str(t.get("status", "")).lower() == "closed")

    base_cash_inr = round(nse_cash + closed_fno_pnl + closed_us_pnl_inr + closed_crypto_pnl_inr, 2)

    allocation_limits = {
        "nse": round(start_capital * _safe_float(_cfg("PAPER_MAX_ALLOC_NSE_PCT", 0.40), 0.40), 2),
        "fno": round(start_capital * _safe_float(_cfg("PAPER_MAX_ALLOC_FNO_PCT", 0.30), 0.30), 2),
        "us": round(start_capital * _safe_float(_cfg("PAPER_MAX_ALLOC_US_PCT", 0.20), 0.20), 2),
        "crypto": round(start_capital * _safe_float(_cfg("PAPER_MAX_ALLOC_CRYPTO_PCT", 0.10), 0.10), 2),
    }

    deployed = {"nse": 0.0, "fno": 0.0, "us": 0.0, "crypto": 0.0, "other": 0.0}
    underlying = {}
    unrealized = 0.0
    open_rows = []
    for row in positions:
        market = str(row.get("market", "other") or "other").lower()
        reserve = reserve_for_position(row)
        pnl = _safe_float(row.get("pnl"))
        unrealized += (
            pnl * _safe_float(_cfg("INR_PER_USDT", 83.0), 83.0) if market == "crypto"
            else pnl * _safe_float(_cfg("INR_PER_USD", 83.0), 83.0) if market == "us"
            else pnl
        )
        row_copy = dict(row)
        row_copy["reserve_inr"] = reserve
        open_rows.append(row_copy)
        deployed[market if market in deployed else "other"] += reserve
        symbol = str(row.get("symbol") or row.get("instrument") or "").upper()
        if market == "fno" and symbol in {"NIFTY", "BANKNIFTY"}:
            bucket = underlying.setdefault(symbol, {"reserve_inr": 0.0, "position_count": 0})
            bucket["reserve_inr"] += reserve
            bucket["position_count"] += 1

    reserved_cash_inr = round(sum(deployed.values()), 2)
    available_cash_inr = round(base_cash_inr - deployed["fno"] - deployed["us"] - deployed["crypto"], 2)
    total_equity_inr = round(base_cash_inr + unrealized, 2)

    warnings = []
    open_fno_underlyings = {k: v for k, v in underlying.items() if v.get("position_count", 0) > 0}
    total_index_reserve = sum(v["reserve_inr"] for v in open_fno_underlyings.values())
    for symbol, bucket in open_fno_underlyings.items():
        limit_key = f"FNO_MAX_UNDERLYING_EXPOSURE_{symbol}_PCT"
        max_pct = _safe_float(_cfg(limit_key, 0.15), 0.15)
        max_allowed = round(start_capital * max_pct, 2)
        if bucket["reserve_inr"] > max_allowed:
            warnings.append(
                f"{symbol} reserve Rs.{bucket['reserve_inr']:,.0f} exceeds configured limit Rs.{max_allowed:,.0f}"
            )
        if total_index_reserve and bucket["reserve_inr"] / total_index_reserve >= 0.60:
            warnings.append(
                f"{symbol} dominates F&O index risk at {bucket['reserve_inr'] / total_index_reserve * 100:.0f}% of open index reserve"
            )

    snapshot = {
        "generated_at": datetime.now().isoformat(),
        "source_synced_at": state.get("synced_at", ""),
        "starting_capital_inr": round(start_capital, 2),
        "base_cash_inr": base_cash_inr,
        "available_cash_inr": available_cash_inr,
        "reserved_cash_inr": reserved_cash_inr,
        "unrealized_pnl_inr": round(unrealized, 2),
        "total_equity_inr": total_equity_inr,
        "market_deployed_inr": {k: round(v, 2) for k, v in deployed.items()},
        "market_allocation_limits_inr": allocation_limits,
        "open_underlying_exposure": {k: {"reserve_inr": round(v["reserve_inr"], 2), "position_count": v["position_count"]} for k, v in underlying.items()},
        "warnings": warnings,
        "open_positions": open_rows[:100],
    }
    return snapshot


def write_treasury_snapshot(state: dict | None = None) -> dict:
    os.makedirs("logs", exist_ok=True)
    snapshot = build_treasury_snapshot(state=state)
    with open(TREASURY_FILE, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2)
    try:
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS treasury_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generated_at TEXT,
                    available_cash_inr REAL,
                    reserved_cash_inr REAL,
                    unrealized_pnl_inr REAL,
                    total_equity_inr REAL,
                    raw_json TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO treasury_snapshots (
                    generated_at, available_cash_inr, reserved_cash_inr,
                    unrealized_pnl_inr, total_equity_inr, raw_json
                ) VALUES (?,?,?,?,?,?)
                """,
                (
                    snapshot["generated_at"],
                    snapshot["available_cash_inr"],
                    snapshot["reserved_cash_inr"],
                    snapshot["unrealized_pnl_inr"],
                    snapshot["total_equity_inr"],
                    json.dumps(snapshot),
                ),
            )
    except sqlite3.Error as exc:
        logger.warning(f"Treasury snapshot DB write failed: {exc}")
    return snapshot


def load_treasury_snapshot(state: dict | None = None, refresh: bool = False) -> dict:
    if refresh or not os.path.exists(TREASURY_FILE):
        return write_treasury_snapshot(state=state)
    return _load_json(TREASURY_FILE, {})


def can_allocate(market: str, reserve_inr: float, state: dict | None = None) -> tuple[bool, str, dict]:
    treasury = build_treasury_snapshot(state=state)
    market_key = str(market or "other").lower()
    available = _safe_float(treasury.get("available_cash_inr"))
    if reserve_inr > available:
        return False, f"insufficient unified treasury cash (need Rs.{reserve_inr:,.0f}, have Rs.{available:,.0f})", treasury
    limits = treasury.get("market_allocation_limits_inr", {}) or {}
    deployed = treasury.get("market_deployed_inr", {}) or {}
    limit = _safe_float(limits.get(market_key), 0.0)
    current = _safe_float(deployed.get(market_key), 0.0)
    if limit and current + reserve_inr > limit:
        return False, f"{market_key.upper()} allocation cap exceeded (limit Rs.{limit:,.0f})", treasury
    return True, "", treasury


def log_treasury_event(event_type: str, market: str, reserve_inr: float, detail: str = "", metadata: dict | None = None):
    try:
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS treasury_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    event_type TEXT,
                    market TEXT,
                    reserve_inr REAL,
                    detail TEXT,
                    metadata_json TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO treasury_events (timestamp, event_type, market, reserve_inr, detail, metadata_json)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    datetime.now().isoformat(),
                    event_type,
                    market,
                    round(_safe_float(reserve_inr), 2),
                    detail,
                    json.dumps(metadata or {}),
                ),
            )
    except sqlite3.Error as exc:
        logger.warning(f"Treasury event log failed: {exc}")
