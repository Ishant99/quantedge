import json
import os
import time
from datetime import datetime

import settings.manager as S
from memory.portfolio_memory import PortfolioMemory
from services.dashboard_data import (
    build_activity_feed,
    build_live_watchlist,
    outcome_bucket_analytics,
    signal_analytics,
    signal_time_analytics,
    symbol_edge_analytics,
    trade_analytics,
    unified_position_frame,
    unified_signal_frame,
    unified_state,
    unified_trade_frame,
)
from services.review_report import build_review_report, render_review_markdown
from services.runtime_state import get_health_snapshot


_TTL_CACHE: dict[tuple, tuple[float, object]] = {}


def _cfg(key, default=None):
    return S.get(key, default)


def _cached(key: tuple, ttl: int, loader):
    now = time.time()
    stamp, value = _TTL_CACHE.get(key, (0.0, None))
    if now - stamp < ttl:
        return value
    value = loader()
    _TTL_CACHE[key] = (now, value)
    return value


def _load_json_file(path: str, default=None):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            pass
    return default if default is not None else {}


def _load_portfolio_state():
    path = os.path.join("logs", "virtual_portfolio.json")
    default = {
        "cash": float(_cfg("VIRTUAL_CAPITAL", 1_000_000)),
        "positions": {},
        "total_trades": 0,
        "wins": 0,
    }
    return _cached(("portfolio_state",), 20, lambda: _load_json_file(path, default))


def _recent_signals(limit: int = 25):
    return _cached(("recent_signals", limit), 30, lambda: unified_signal_frame(limit=limit).to_dict(orient="records"))


def _recent_trades(limit: int = 200):
    return _cached(("recent_trades", limit), 30, lambda: unified_trade_frame(limit=limit).to_dict(orient="records"))


def _recent_outcomes(limit: int = 200):
    def _loader():
        try:
            from analysis.outcome_tracker import OutcomeTracker
            return OutcomeTracker.get_recent_outcomes(limit=limit)
        except Exception:
            return []
    return _cached(("recent_outcomes", limit), 60, _loader)


def _broker_market_stats():
    def _loader():
        bundles = {
            "fno": {"stats": {}, "open": [], "closed": []},
            "crypto": {"stats": {}, "open": [], "closed": []},
            "us": {"stats": {}, "open": [], "closed": []},
        }
        try:
            from execution.brokers.fno_paper_broker import FNOPaperBroker
            broker = FNOPaperBroker()
            bundles["fno"] = {
                "stats": broker.get_stats(),
                "open": broker.get_open_positions(),
                "closed": broker.get_closed_trades(limit=20),
            }
        except Exception:
            pass
        try:
            from execution.brokers.crypto_paper_broker import CryptoPaperBroker
            broker = CryptoPaperBroker()
            bundles["crypto"] = {
                "stats": broker.get_stats(),
                "open": broker.get_open_positions(),
                "closed": broker.get_closed_trades(limit=20),
            }
        except Exception:
            pass
        try:
            from execution.brokers.us_paper_broker import USPaperBroker
            broker = USPaperBroker()
            bundles["us"] = {
                "stats": broker.get_stats(),
                "open": broker.get_open_positions(),
                "closed": broker.get_closed_trades(limit=20),
            }
        except Exception:
            pass
        return bundles

    return _cached(("broker_market_stats",), 45, _loader)


def _memory_stats():
    return _cached(("memory_stats",), 45, lambda: PortfolioMemory().get_stats())


def _market_files():
    return {
        "regime": _load_json_file(os.path.join("logs", "market_regime.json"), {}),
        "pcr": _load_json_file(os.path.join("logs", "pcr_signal.json"), {}),
        "fii": _load_json_file(os.path.join("logs", "fii_signal.json"), {}),
    }


def _serialise_tables(payload: dict) -> dict:
    serialised = {}
    for key, value in payload.items():
        if hasattr(value, "to_dict"):
            serialised[key] = value.to_dict(orient="records")
        else:
            serialised[key] = value
    return serialised


def api_meta() -> dict:
    return {
        "service": "QuantEdge API",
        "version": "phase-6",
        "timestamp": datetime.now().isoformat(),
        "endpoints": [
            "/health",
            "/api/health",
            "/api/overview",
            "/api/portfolio",
            "/api/signals?limit=25",
            "/api/watchlist?limit=8",
            "/api/activity?limit=12",
            "/api/analytics/summary",
            "/api/review",
            "/api/review.md",
        ],
    }


def health_payload() -> dict:
    return {
        "ok": True,
        "timestamp": datetime.now().isoformat(),
        "health": get_health_snapshot(_cfg),
    }


def portfolio_payload() -> dict:
    pf = _load_portfolio_state()
    vc = float(_cfg("VIRTUAL_CAPITAL", 1_000_000))
    cash = float(pf.get("cash", vc))
    positions = pf.get("positions", {}) or {}
    nse_pnl = cash - vc
    state = unified_state()
    summary = state.get("summary") or {}
    markets = _broker_market_stats()
    inr_per_usd = float(_cfg("INR_PER_USD", 83.0))
    combined_pnl = (
        nse_pnl
        + float(markets["fno"]["stats"].get("total_pnl", 0) or 0)
        + float(markets["crypto"]["stats"].get("total_pnl_usdt", 0) or 0) * inr_per_usd
        + float(markets["us"]["stats"].get("total_pnl_usd", 0) or 0) * inr_per_usd
    )
    return {
        "timestamp": datetime.now().isoformat(),
        "virtual_capital": vc,
        "cash": cash,
        "nse_pnl": nse_pnl,
        "combined_pnl_inr": combined_pnl,
        "open_positions": positions,
        "open_position_count": int(summary.get("combined_open_positions", len(positions))),
        "unified_open_positions": state.get("positions", []),
        "memory_stats": _memory_stats(),
        "markets": markets,
    }


def signals_payload(limit: int = 25) -> dict:
    signals = _recent_signals(limit=limit)
    return {
        "timestamp": datetime.now().isoformat(),
        "count": len(signals),
        "signals": signals,
    }


def watchlist_payload(limit: int = 8) -> dict:
    pf = _load_portfolio_state()
    watch = build_live_watchlist(_recent_signals(limit=25), pf.get("positions", {}) or {}, limit=limit)
    return {
        "timestamp": datetime.now().isoformat(),
        "count": int(watch.shape[0]) if not watch.empty else 0,
        "watchlist": watch.to_dict(orient="records") if not watch.empty else [],
    }


def activity_payload(limit: int = 12) -> dict:
    feed = build_activity_feed(limit=limit)
    return {
        "timestamp": datetime.now().isoformat(),
        "count": int(feed.shape[0]) if not feed.empty else 0,
        "items": feed.to_dict(orient="records") if not feed.empty else [],
    }


def overview_payload() -> dict:
    market = _market_files()
    portfolio = portfolio_payload()
    state = unified_state()
    return {
        "timestamp": datetime.now().isoformat(),
        "portfolio": {
            "cash": portfolio["cash"],
            "nse_pnl": portfolio["nse_pnl"],
            "combined_pnl_inr": portfolio["combined_pnl_inr"],
            "open_position_count": portfolio["open_position_count"],
        },
        "memory_stats": portfolio["memory_stats"],
        "sync": state.get("summary", {}),
        "market": market,
        "health": get_health_snapshot(_cfg),
    }


def analytics_summary_payload() -> dict:
    trade_frame = unified_trade_frame(limit=500)
    if not trade_frame.empty:
        trade_frame = trade_frame[trade_frame["status"].astype(str).str.lower() == "closed"].copy()
    signal_frame = unified_signal_frame(limit=500)
    outcomes = _recent_outcomes(limit=300)
    outcome_frame = None
    if outcomes:
        import pandas as pd
        outcome_frame = pd.DataFrame(outcomes)

    return {
        "timestamp": datetime.now().isoformat(),
        "trade_analytics": _serialise_tables(trade_analytics(trade_frame)) if trade_frame is not None and not trade_frame.empty else {},
        "signal_analytics": _serialise_tables(signal_analytics(signal_frame)) if signal_frame is not None and not signal_frame.empty else {},
        "signal_time": _serialise_tables(signal_time_analytics(signal_frame)) if signal_frame is not None and not signal_frame.empty else {},
        "outcome_analytics": _serialise_tables(outcome_bucket_analytics(outcome_frame)) if outcome_frame is not None and not outcome_frame.empty else {},
        "symbol_edge": _serialise_tables(symbol_edge_analytics(outcome_frame, min_sample=3)) if outcome_frame is not None and not outcome_frame.empty else {},
    }


def review_payload() -> dict:
    return build_review_report(unified_state())


def review_markdown() -> str:
    return render_review_markdown(review_payload())
