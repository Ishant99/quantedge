import pandas as pd
import yfinance as yf

from memory.portfolio_memory import PortfolioMemory
from services.runtime_state import read_scheduler_status
from services.state_sync import load_unified_state


def normalise_trade_frame(trades) -> pd.DataFrame:
    frame = pd.DataFrame(trades or [])
    expected_defaults = {
        "symbol": "",
        "action": "",
        "status": "",
        "qty": 0,
        "entry_price": 0.0,
        "exit_price": 0.0,
        "entry_time": "",
        "exit_time": "",
        "pnl": 0.0,
        "pnl_pct": 0.0,
        "mode": "",
    }
    for column, default in expected_defaults.items():
        if column not in frame.columns:
            frame[column] = default

    if frame.empty:
        return frame

    frame["symbol"] = frame["symbol"].fillna("").astype(str)
    frame["action"] = frame["action"].fillna("").astype(str)
    frame["status"] = frame["status"].fillna("").astype(str)
    frame["mode"] = frame["mode"].fillna("").astype(str)
    for numeric in ["qty", "entry_price", "exit_price", "pnl", "pnl_pct"]:
        frame[numeric] = pd.to_numeric(frame[numeric], errors="coerce").fillna(0)
    frame["entry_time"] = frame["entry_time"].fillna("").astype(str)
    frame["exit_time"] = frame["exit_time"].fillna("").astype(str)
    return frame


def unified_state(auto_sync: bool = True) -> dict:
    return load_unified_state(auto_sync=auto_sync)


def unified_trade_frame(limit: int = 500, auto_sync: bool = True) -> pd.DataFrame:
    state = unified_state(auto_sync=auto_sync)
    rows = (state.get("trades") or [])[:limit]
    return normalise_trade_frame(rows)


def unified_position_frame(limit: int = 200, auto_sync: bool = True) -> pd.DataFrame:
    state = unified_state(auto_sync=auto_sync)
    rows = (state.get("positions") or [])[:limit]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    for col in ["quantity", "entry_price", "current_price", "pnl", "pnl_pct"]:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0)
    return frame


def unified_signal_frame(limit: int = 500, auto_sync: bool = True) -> pd.DataFrame:
    state = unified_state(auto_sync=auto_sync)
    rows = (state.get("signals") or [])[:limit]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    for col in ["confidence", "entry_price", "stop_loss", "take_profit", "position_size", "ta_score", "executed"]:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0)
    return frame


def quote_snapshot(symbol: str) -> dict:
    try:
        hist = yf.Ticker(f"{symbol}.NS").history(period="5d", interval="1d", auto_adjust=True)
        if hist.empty:
            return {"symbol": symbol, "price": None, "change_pct": None}
        close = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else close
        change_pct = ((close - prev) / prev * 100) if prev else 0.0
        return {
            "symbol": symbol,
            "price": close,
            "prev_close": prev,
            "change_pct": round(change_pct, 2),
        }
    except Exception:
        return {"symbol": symbol, "price": None, "change_pct": None}


def build_live_watchlist(signals: list[dict], positions: dict, limit: int = 8) -> pd.DataFrame:
    ordered: list[str] = []
    for sym in positions.keys():
        if sym not in ordered:
            ordered.append(sym)
    for sig in signals:
        sym = sig.get("symbol")
        if sym and sym not in ordered:
            ordered.append(sym)
    rows = []
    signal_lookup = {s.get("symbol"): s for s in signals}
    for sym in ordered[:limit]:
        quote = quote_snapshot(sym)
        sig = signal_lookup.get(sym, {})
        rows.append({
            "Symbol": sym,
            "LTP": round(quote["price"], 2) if quote.get("price") is not None else None,
            "Change %": quote.get("change_pct"),
            "Signal": sig.get("action", "WATCH"),
            "Conf": f"{sig.get('confidence', 0):.0%}" if sig else "--",
            "Position": "OPEN" if sym in positions else "",
        })
    return pd.DataFrame(rows)


def build_activity_feed(limit: int = 12) -> pd.DataFrame:
    items: list[dict] = []
    state = unified_state(auto_sync=False)

    for sig in (state.get("signals") or [])[:6]:
        items.append({
            "Time": (sig.get("timestamp") or "")[:16],
            "Type": "SIGNAL",
            "Item": f"{sig.get('market', '').upper()} {sig.get('symbol', '')}".strip(),
            "Detail": f"{sig.get('action', '')} {sig.get('confidence', 0):.0%}".strip(),
        })

    for trade in (state.get("trades") or [])[:6]:
        ts = (trade.get("exit_time") or trade.get("entry_time") or "")[:16]
        pnl = trade.get("pnl")
        market = str(trade.get("market", "")).upper()
        pnl_txt = f" | {pnl:+,.0f}" if pnl is not None else ""
        items.append({
            "Time": ts,
            "Type": "TRADE",
            "Item": f"{market} {trade.get('symbol', '')}".strip(),
            "Detail": f"{trade.get('status', '').upper()} {trade.get('side', '')}{pnl_txt}".strip(),
        })

    try:
        from analysis.outcome_tracker import OutcomeTracker
        for outcome in OutcomeTracker.get_recent_outcomes(limit=6):
            items.append({
                "Time": (outcome.get("outcome_date") or "")[:10],
                "Type": "OUTCOME",
                "Item": outcome.get("symbol", ""),
                "Detail": f"{outcome.get('outcome', '')} {outcome.get('confidence', 0):.0%}",
            })
    except Exception:
        pass

    jobs = (read_scheduler_status().get("jobs") or {})
    for name, payload in jobs.items():
        items.append({
            "Time": payload.get("timestamp", "")[:16],
            "Type": "JOB",
            "Item": name.replace("_", " ").upper(),
            "Detail": f"{payload.get('state', '').upper()} {payload.get('detail', '')[:40]}".strip(),
        })

    feed = pd.DataFrame(items)
    if feed.empty:
        return feed
    return feed.sort_values("Time", ascending=False).head(limit)[["Time", "Type", "Item", "Detail"]]


def trade_analytics(closed: pd.DataFrame) -> dict:
    frame = normalise_trade_frame(closed.to_dict(orient="records") if isinstance(closed, pd.DataFrame) else closed)
    if frame.empty:
        return {}
    frame["entry_time"] = pd.to_datetime(frame["entry_time"], errors="coerce")
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], errors="coerce")
    frame["weekday"] = frame["exit_time"].dt.day_name().fillna("Unknown")
    frame["is_win"] = frame["pnl"] > 0
    frame["hold_hours"] = (frame["exit_time"] - frame["entry_time"]).dt.total_seconds() / 3600
    frame["entry_hour"] = frame["entry_time"].dt.hour.fillna(-1).astype(int)
    frame["hold_bucket"] = pd.cut(
        frame["hold_hours"], bins=[-0.01, 4, 24, 72, 168, 10_000],
        labels=["<4h", "4-24h", "1-3d", "3-7d", "7d+"], include_lowest=True,
    )

    wins = frame[frame["is_win"]]
    losses = frame[~frame["is_win"]]
    weekday = frame.groupby("weekday")["pnl"].agg(["count", "mean", "sum"]).reset_index().sort_values("sum", ascending=False)
    weekday.columns = ["Weekday", "Trades", "Avg P&L", "Total P&L"]
    symbols = frame.groupby("symbol")["pnl"].agg(["count", "sum", "mean"]).reset_index().sort_values("sum", ascending=False).head(8)
    symbols.columns = ["Symbol", "Trades", "Total P&L", "Avg P&L"]
    hold_buckets = frame.groupby("hold_bucket", observed=False).agg(Trades=("symbol", "count"), AvgPnL=("pnl", "mean"), TotalPnL=("pnl", "sum")).reset_index()
    hold_buckets.columns = ["Hold Bucket", "Trades", "Avg P&L", "Total P&L"]

    hour_buckets = frame[frame["entry_hour"] >= 0].copy()
    if not hour_buckets.empty:
        hour_buckets = hour_buckets.groupby("entry_hour").agg(Trades=("symbol", "count"), AvgPnL=("pnl", "mean"), TotalPnL=("pnl", "sum")).reset_index().sort_values("TotalPnL", ascending=False)
        hour_buckets["Entry Hour"] = hour_buckets["entry_hour"].apply(lambda h: f"{h:02d}:00")
        hour_buckets = hour_buckets[["Entry Hour", "Trades", "AvgPnL", "TotalPnL"]]
        hour_buckets.columns = ["Entry Hour", "Trades", "Avg P&L", "Total P&L"]
    else:
        hour_buckets = pd.DataFrame(columns=["Entry Hour", "Trades", "Avg P&L", "Total P&L"])

    valid_hold_hours = frame["hold_hours"].dropna()
    return {
        "expectancy": float(frame["pnl"].mean()) if not frame.empty else 0.0,
        "avg_hold_hours": float(valid_hold_hours.mean()) if not valid_hold_hours.empty else 0.0,
        "win_count": int(wins.shape[0]),
        "loss_count": int(losses.shape[0]),
        "avg_win": float(wins["pnl"].mean()) if not wins.empty else 0.0,
        "avg_loss": float(losses["pnl"].mean()) if not losses.empty else 0.0,
        "weekday": weekday,
        "symbols": symbols,
        "hold_buckets": hold_buckets,
        "entry_hours": hour_buckets,
    }


def signal_analytics(signals: pd.DataFrame) -> dict:
    if signals.empty:
        return {}
    frame = signals.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    frame["confidence"] = pd.to_numeric(frame["confidence"], errors="coerce").fillna(0.0)
    frame["ta_score"] = pd.to_numeric(frame["ta_score"], errors="coerce").fillna(0.0)
    frame["executed"] = pd.to_numeric(frame["executed"], errors="coerce").fillna(0).astype(int)
    conf_bins = pd.cut(frame["confidence"], bins=[0.0, 0.5, 0.65, 0.8, 1.01], labels=["0-50%", "50-65%", "65-80%", "80%+"], include_lowest=True)
    ta_bins = pd.cut(frame["ta_score"], bins=[0.0, 4.0, 6.0, 8.0, 10.1], labels=["0-4", "4-6", "6-8", "8-10"], include_lowest=True)
    conf_table = frame.assign(conf_bucket=conf_bins).groupby("conf_bucket", observed=False).agg(Signals=("symbol", "count"), Executed=("executed", "sum")).reset_index()
    conf_table["Exec %"] = conf_table.apply(lambda row: round((row["Executed"] / row["Signals"] * 100), 1) if row["Signals"] else 0.0, axis=1)
    conf_table.columns = ["Confidence", "Signals", "Executed", "Exec %"]
    ta_table = frame.assign(ta_bucket=ta_bins).groupby("ta_bucket", observed=False).agg(Signals=("symbol", "count"), Executed=("executed", "sum")).reset_index()
    ta_table["Exec %"] = ta_table.apply(lambda row: round((row["Executed"] / row["Signals"] * 100), 1) if row["Signals"] else 0.0, axis=1)
    ta_table.columns = ["TA Bucket", "Signals", "Executed", "Exec %"]
    action_table = frame.groupby("action").agg(Signals=("symbol", "count"), Executed=("executed", "sum")).reset_index().sort_values("Signals", ascending=False)
    action_table["Exec %"] = action_table.apply(lambda row: round((row["Executed"] / row["Signals"] * 100), 1) if row["Signals"] else 0.0, axis=1)
    action_table.columns = ["Action", "Signals", "Executed", "Exec %"]
    return {
        "total_signals": int(frame.shape[0]),
        "executed_signals": int(frame["executed"].sum()),
        "execution_rate": round(frame["executed"].mean() * 100, 1) if not frame.empty else 0.0,
        "avg_confidence": round(frame["confidence"].mean() * 100, 1) if not frame.empty else 0.0,
        "avg_ta_score": round(frame["ta_score"].mean(), 2) if not frame.empty else 0.0,
        "confidence_table": conf_table,
        "ta_table": ta_table,
        "action_table": action_table,
    }


def signal_time_analytics(signals: pd.DataFrame) -> dict:
    if signals.empty:
        return {}
    frame = signals.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    frame["executed"] = pd.to_numeric(frame["executed"], errors="coerce").fillna(0).astype(int)
    frame["day"] = frame["timestamp"].dt.strftime("%Y-%m-%d")
    daily = frame.groupby("day").agg(Signals=("symbol", "count"), Executed=("executed", "sum")).reset_index().sort_values("day", ascending=False).head(14).sort_values("day")
    daily["Exec %"] = daily.apply(lambda row: round((row["Executed"] / row["Signals"] * 100), 1) if row["Signals"] else 0.0, axis=1)
    return {"daily": daily}


def outcome_bucket_analytics(outcomes: pd.DataFrame) -> dict:
    if outcomes.empty:
        return {}
    frame = outcomes.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    frame["outcome_date"] = pd.to_datetime(frame["outcome_date"], errors="coerce")
    frame["confidence"] = pd.to_numeric(frame["confidence"], errors="coerce").fillna(0.0)
    frame["ta_score"] = pd.to_numeric(frame["ta_score"], errors="coerce").fillna(0.0)
    frame["is_tp"] = (frame["outcome"] == "TP_HIT").astype(int)
    frame["is_sl"] = (frame["outcome"] == "SL_HIT").astype(int)
    frame["days_to_outcome"] = pd.to_numeric(frame["days_to_outcome"], errors="coerce")
    frame["entry_weekday"] = frame["timestamp"].dt.day_name().fillna("Unknown")
    frame["days_bucket"] = pd.cut(frame["days_to_outcome"], bins=[-0.01, 1, 3, 7, 14, 60], labels=["0-1d", "1-3d", "3-7d", "7-14d", "14d+"], include_lowest=True)
    conf_bins = pd.cut(frame["confidence"], bins=[0.0, 0.5, 0.65, 0.8, 1.01], labels=["0-50%", "50-65%", "65-80%", "80%+"], include_lowest=True)
    ta_bins = pd.cut(frame["ta_score"], bins=[0.0, 4.0, 6.0, 8.0, 10.1], labels=["0-4", "4-6", "6-8", "8-10"], include_lowest=True)
    conf_outcomes = frame.assign(conf_bucket=conf_bins).groupby("conf_bucket", observed=False).agg(Resolved=("symbol", "count"), TP=("is_tp", "sum"), SL=("is_sl", "sum")).reset_index()
    conf_outcomes["TP %"] = conf_outcomes.apply(lambda row: round((row["TP"] / row["Resolved"] * 100), 1) if row["Resolved"] else 0.0, axis=1)
    conf_outcomes.columns = ["Confidence", "Resolved", "TP", "SL", "TP %"]
    ta_outcomes = frame.assign(ta_bucket=ta_bins).groupby("ta_bucket", observed=False).agg(Resolved=("symbol", "count"), TP=("is_tp", "sum"), SL=("is_sl", "sum")).reset_index()
    ta_outcomes["TP %"] = ta_outcomes.apply(lambda row: round((row["TP"] / row["Resolved"] * 100), 1) if row["Resolved"] else 0.0, axis=1)
    ta_outcomes.columns = ["TA Bucket", "Resolved", "TP", "SL", "TP %"]
    sentiment = frame.groupby("sentiment").agg(Resolved=("symbol", "count"), TP=("is_tp", "sum"), SL=("is_sl", "sum")).reset_index().sort_values("Resolved", ascending=False)
    sentiment["TP %"] = sentiment.apply(lambda row: round((row["TP"] / row["Resolved"] * 100), 1) if row["Resolved"] else 0.0, axis=1)
    sentiment.columns = ["Sentiment", "Resolved", "TP", "SL", "TP %"]
    weekday = frame.groupby("entry_weekday").agg(Resolved=("symbol", "count"), TP=("is_tp", "sum"), SL=("is_sl", "sum")).reset_index().sort_values("TP", ascending=False)
    weekday["TP %"] = weekday.apply(lambda row: round((row["TP"] / row["Resolved"] * 100), 1) if row["Resolved"] else 0.0, axis=1)
    weekday.columns = ["Weekday", "Resolved", "TP", "SL", "TP %"]
    time_to_outcome = frame.groupby("days_bucket", observed=False).agg(Resolved=("symbol", "count"), TP=("is_tp", "sum"), SL=("is_sl", "sum")).reset_index()
    time_to_outcome["TP %"] = time_to_outcome.apply(lambda row: round((row["TP"] / row["Resolved"] * 100), 1) if row["Resolved"] else 0.0, axis=1)
    time_to_outcome.columns = ["Days To Outcome", "Resolved", "TP", "SL", "TP %"]
    return {
        "resolved": int(frame.shape[0]),
        "tp_rate": round(frame["is_tp"].mean() * 100, 1) if not frame.empty else 0.0,
        "avg_confidence": round(frame["confidence"].mean() * 100, 1) if not frame.empty else 0.0,
        "avg_ta_score": round(frame["ta_score"].mean(), 2) if not frame.empty else 0.0,
        "confidence_outcomes": conf_outcomes,
        "ta_outcomes": ta_outcomes,
        "sentiment_outcomes": sentiment,
        "weekday_outcomes": weekday,
        "time_to_outcome": time_to_outcome,
    }


def symbol_edge_analytics(outcomes: pd.DataFrame, min_sample: int = 3) -> dict:
    if outcomes.empty:
        return {}
    frame = outcomes.copy()
    frame["confidence"] = pd.to_numeric(frame["confidence"], errors="coerce").fillna(0.0)
    frame["ta_score"] = pd.to_numeric(frame["ta_score"], errors="coerce").fillna(0.0)
    frame["is_tp"] = (frame["outcome"] == "TP_HIT").astype(int)
    frame["is_sl"] = (frame["outcome"] == "SL_HIT").astype(int)
    by_symbol = frame.groupby("symbol").agg(Resolved=("symbol", "count"), TP=("is_tp", "sum"), SL=("is_sl", "sum"), AvgConf=("confidence", "mean"), AvgTA=("ta_score", "mean")).reset_index()
    by_symbol["TP %"] = by_symbol.apply(lambda row: round((row["TP"] / row["Resolved"] * 100), 1) if row["Resolved"] else 0.0, axis=1)
    by_symbol["Edge"] = by_symbol["TP %"] - 50.0
    qualified = by_symbol[by_symbol["Resolved"] >= min_sample].copy()
    best = qualified.sort_values(["Edge", "Resolved"], ascending=[False, False]).head(10)
    weak = qualified.sort_values(["Edge", "Resolved"], ascending=[True, False]).head(10)
    best = best[["symbol", "Resolved", "TP", "SL", "TP %", "Edge", "AvgConf", "AvgTA"]]
    weak = weak[["symbol", "Resolved", "TP", "SL", "TP %", "Edge", "AvgConf", "AvgTA"]]
    best.columns = ["Symbol", "Resolved", "TP", "SL", "TP %", "Edge", "Avg Conf", "Avg TA"]
    weak.columns = ["Symbol", "Resolved", "TP", "SL", "TP %", "Edge", "Avg Conf", "Avg TA"]
    return {"qualified_symbols": int(qualified.shape[0]), "min_sample": min_sample, "best_symbols": best, "weak_symbols": weak}
