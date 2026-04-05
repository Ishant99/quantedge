import csv
import json
import os
import sqlite3
from datetime import datetime

from config import INR_PER_USD, INR_PER_USDT, SQLITE_DB_FILE, VIRTUAL_CAPITAL, VIRTUAL_PORTFOLIO_FILE
from services.paper_treasury import reserve_for_fno_order, write_treasury_snapshot
from memory.portfolio_memory import PortfolioMemory
from services.review_report import write_review_report
from utils import get_logger


logger = get_logger("StateSync")
UNIFIED_STATE_FILE = os.path.join("logs", "unified_state.json")


def _load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            pass
    return default


def _safe_table_rows(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[dict]:
    try:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query, params).fetchall()]
    except sqlite3.Error:
        return []


def _nse_positions(portfolio: dict) -> list[dict]:
    rows = []
    positions = portfolio.get("positions", {}) or {}
    for symbol, pos in positions.items():
        qty = float(pos.get("qty", 0) or 0)
        entry = float(pos.get("entry", 0) or 0)
        current = float(pos.get("mark_price", pos.get("entry", 0)) or 0)
        pnl = round((current - entry) * qty, 2)
        pnl_pct = round(((current - entry) / entry) * 100, 2) if entry else 0.0
        # Handle INTRA: prefixed symbols from intraday agent
        display_sym = symbol.replace("INTRA:", "")
        is_intraday = symbol.startswith("INTRA:") or pos.get("trade_type") == "intraday"
        rows.append({
            "position_key": f"nse:{symbol}",
            "market": "nse",
            "symbol": display_sym,
            "instrument": display_sym,
            "side": "LONG",
            "strategy": "intraday" if is_intraday else "swing",
            "status": "open",
            "quantity": qty,
            "entry_price": entry,
            "current_price": current,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "entry_time": pos.get("timestamp", ""),
            "exit_time": "",
            "expiry": "",
            "source": "virtual_portfolio",
            "reasoning": "",
            "capital": round(entry * qty, 2),
        })
    return rows


def _map_nse_trades(memory: PortfolioMemory, limit: int = 500) -> list[dict]:
    rows = []
    for idx, trade in enumerate(memory.get_recent_trades(limit=limit), start=1):
        symbol = trade.get("symbol", "")
        action = (trade.get("action", "") or "").upper()
        rows.append({
            "trade_key": f"nse:{symbol}:{trade.get('entry_time', '')}:{idx}",
            "market": "nse",
            "symbol": symbol,
            "instrument": symbol,
            "side": action,
            "strategy": "swing",
            "status": trade.get("status", ""),
            "quantity": float(trade.get("qty", 0) or 0),
            "entry_price": float(trade.get("entry_price", 0) or 0),
            "current_price": float(trade.get("exit_price", trade.get("entry_price", 0)) or 0),
            "exit_price": float(trade.get("exit_price", 0) or 0),
            "pnl": float(trade.get("pnl", 0) or 0),
            "pnl_pct": float(trade.get("pnl_pct", 0) or 0),
            "entry_time": trade.get("entry_time", ""),
            "exit_time": trade.get("exit_time", ""),
            "expiry": "",
            "source": "portfolio_memory",
            "reasoning": "",
            "capital": round(float(trade.get("entry_price", 0) or 0) * float(trade.get("qty", 0) or 0), 2),
        })
    return rows


def _map_nse_csv_trades(limit: int = 500) -> list[dict]:
    path = os.path.join("logs", "paper_trades.csv")
    if not os.path.exists(path):
        return []
    rows = []
    try:
        with open(path, encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for idx, row in enumerate(reader, start=1):
                symbol = str(row.get("symbol", "") or "")
                trade_type = str(row.get("trade_type", "") or "paper")
                action = str(row.get("action", "") or "")
                timestamp = str(row.get("timestamp", "") or "")
                qty = float(row.get("qty", 0) or 0)
                entry_price = float(row.get("entry_price", 0) or 0)
                exit_price = float(row.get("exit_price", 0) or 0)
                pnl = float(row.get("pnl", 0) or 0)
                pnl_pct = float(row.get("pnl_pct", 0) or 0)
                status = "closed" if action.upper().startswith("CLOSED") or exit_price else "open"
                rows.append({
                    "trade_key": f"nsecsv:{symbol}:{timestamp}:{idx}",
                    "market": "nse",
                    "symbol": symbol,
                    "instrument": symbol,
                    "side": action or "SELL",
                    "strategy": trade_type,
                    "status": status,
                    "quantity": qty,
                    "entry_price": entry_price,
                    "current_price": exit_price or entry_price,
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "entry_time": timestamp,
                    "exit_time": timestamp if status == "closed" else "",
                    "expiry": "",
                    "source": "csv_fallback",
                    "reasoning": "",
                    "capital": round(entry_price * qty, 2),
                })
    except Exception as exc:
        logger.warning(f"Could not read NSE CSV trade audit: {exc}")
        return []
    return list(reversed(rows[-limit:]))


def _dedupe_nse_trade_rows(rows: list[dict]) -> list[dict]:
    deduped = []
    seen = set()
    for row in rows:
        key = (
            row.get("market"),
            row.get("symbol"),
            row.get("status"),
            round(float(row.get("quantity", 0) or 0), 4),
            round(float(row.get("entry_price", 0) or 0), 4),
            round(float(row.get("exit_price", 0) or 0), 4),
            round(float(row.get("pnl", 0) or 0), 4),
            row.get("entry_time"),
            row.get("exit_time"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _map_signal_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = _safe_table_rows(
        conn,
        """
        SELECT id, timestamp, symbol, action, confidence, entry_price,
               stop_loss, take_profit, position_size, ta_score,
               sentiment, setup_type, regime_tag, quality_score,
               expectancy_score, symbol_edge, setup_edge, quality_flags,
               reasoning, executed
        FROM signals
        ORDER BY id DESC LIMIT 500
        """,
    )
    mapped = []
    for row in rows:
        mapped.append({
            "signal_key": f"nse:{row.get('id')}",
            "market": "nse",
            "symbol": row.get("symbol", ""),
            "action": row.get("action", ""),
            "confidence": float(row.get("confidence", 0) or 0),
            "entry_price": float(row.get("entry_price", 0) or 0),
            "stop_loss": float(row.get("stop_loss", 0) or 0),
            "take_profit": float(row.get("take_profit", 0) or 0),
            "position_size": float(row.get("position_size", 0) or 0),
            "ta_score": float(row.get("ta_score", 0) or 0),
            "sentiment": row.get("sentiment", ""),
            "setup_type": row.get("setup_type", ""),
            "regime_tag": row.get("regime_tag", ""),
            "quality_score": float(row.get("quality_score", 0) or 0),
            "expectancy_score": float(row.get("expectancy_score", 0) or 0),
            "symbol_edge": float(row.get("symbol_edge", 0) or 0),
            "setup_edge": float(row.get("setup_edge", 0) or 0),
            "quality_flags": row.get("quality_flags", ""),
            "timestamp": row.get("timestamp", ""),
            "executed": int(row.get("executed", 0) or 0),
            "source": "signals",
            "reasoning": row.get("reasoning", ""),
        })
    return mapped


def _map_fno_rows(conn: sqlite3.Connection) -> tuple[list[dict], list[dict], list[dict]]:
    rows = _safe_table_rows(conn, "SELECT * FROM fno_trades ORDER BY id DESC LIMIT 500")
    positions, trades, signals = [], [], []
    for row in rows:
        side = row.get("option_type", "")
        symbol = row.get("instrument", "")
        status = row.get("status", "")
        current = float(row.get("current_premium", 0) or 0)
        entry = float(row.get("entry_premium", 0) or 0)
        qty = float(row.get("qty", 0) or 0)
        pnl = float(row.get("pnl", 0) or 0)
        pnl_pct = float(row.get("pnl_pct", 0) or 0)
        trade_record = {
            "trade_key": f"fno:{row.get('id')}",
            "market": "fno",
            "symbol": symbol,
            "instrument": f"{symbol} {row.get('strike', 0)} {side}".strip(),
            "side": side,
            "strategy": side,
            "status": status,
            "quantity": qty,
            "entry_price": entry,
            "current_price": current,
            "exit_price": float(row.get("exit_premium", 0) or 0),
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "entry_time": row.get("entry_time", ""),
            "exit_time": row.get("exit_time", ""),
            "expiry": row.get("expiry", ""),
            "source": "fno_trades",
            "reasoning": row.get("reasoning", ""),
            "capital": 0.0,
        }
        trade_record["capital"] = reserve_for_fno_order(symbol, side, entry, qty)
        trades.append(trade_record)
        if status == "open":
            positions.append({**trade_record, "position_key": trade_record["trade_key"]})
            signals.append({
                "signal_key": f"fno-open:{row.get('id')}",
                "market": "fno",
                "symbol": symbol,
                "action": side,
                "confidence": 1.0,
                "entry_price": entry,
                "stop_loss": 0.0,
                "take_profit": 0.0,
                "position_size": qty,
                "ta_score": 0.0,
                "sentiment": "",
                "timestamp": row.get("entry_time", ""),
                "executed": 1,
                "source": "fno_trades",
                "reasoning": row.get("reasoning", ""),
            })
    return positions, trades, signals


def _map_simple_market_rows(conn: sqlite3.Connection, table: str, market: str, pnl_col: str, amount_col: str) -> tuple[list[dict], list[dict], list[dict]]:
    rows = _safe_table_rows(conn, f"SELECT * FROM {table} ORDER BY id DESC LIMIT 500")
    positions, trades, signals = [], [], []
    for row in rows:
        symbol = row.get("symbol", "")
        direction = row.get("direction", "")
        status = row.get("status", "")
        current = float(row.get("current_price", 0) or 0)
        entry = float(row.get("entry_price", 0) or 0)
        qty = float(row.get("qty", 0) or 0)
        trade_record = {
            "trade_key": f"{market}:{row.get('id')}",
            "market": market,
            "symbol": symbol,
            "instrument": symbol,
            "side": direction,
            "strategy": direction,
            "status": status,
            "quantity": qty,
            "entry_price": entry,
            "current_price": current,
            "exit_price": float(row.get("exit_price", 0) or 0),
            "pnl": float(row.get(pnl_col, 0) or 0),
            "pnl_pct": float(row.get("pnl_pct", 0) or 0),
            "entry_time": row.get("entry_time", ""),
            "exit_time": row.get("exit_time", ""),
            "expiry": "",
            "source": table,
            "reasoning": row.get("reasoning", ""),
            "capital": float(row.get(amount_col, 0) or 0),
        }
        trades.append(trade_record)
        if status == "open":
            positions.append({**trade_record, "position_key": trade_record["trade_key"]})
            signals.append({
                "signal_key": f"{market}-open:{row.get('id')}",
                "market": market,
                "symbol": symbol,
                "action": direction,
                "confidence": 1.0,
                "entry_price": entry,
                "stop_loss": float(row.get("sl_price", 0) or 0),
                "take_profit": float(row.get("tp_price", 0) or 0),
                "position_size": qty,
                "ta_score": 0.0,
                "sentiment": "",
                "timestamp": row.get("entry_time", ""),
                "executed": 1,
                "source": table,
                "reasoning": row.get("reasoning", ""),
            })
    return positions, trades, signals


def _ensure_sync_tables(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS unified_positions (
            position_key   TEXT PRIMARY KEY,
            market         TEXT,
            symbol         TEXT,
            instrument     TEXT,
            side           TEXT,
            strategy       TEXT,
            status         TEXT,
            quantity       REAL,
            entry_price    REAL,
            current_price  REAL,
            pnl            REAL,
            pnl_pct        REAL,
            entry_time     TEXT,
            exit_time      TEXT,
            expiry         TEXT,
            source         TEXT,
            reasoning      TEXT,
            raw_json       TEXT,
            synced_at      TEXT
        );
        CREATE TABLE IF NOT EXISTS unified_trades (
            trade_key      TEXT PRIMARY KEY,
            market         TEXT,
            symbol         TEXT,
            instrument     TEXT,
            side           TEXT,
            strategy       TEXT,
            status         TEXT,
            quantity       REAL,
            entry_price    REAL,
            current_price  REAL,
            exit_price     REAL,
            pnl            REAL,
            pnl_pct        REAL,
            entry_time     TEXT,
            exit_time      TEXT,
            expiry         TEXT,
            source         TEXT,
            reasoning      TEXT,
            raw_json       TEXT,
            synced_at      TEXT
        );
        CREATE TABLE IF NOT EXISTS unified_signals (
            signal_key     TEXT PRIMARY KEY,
            market         TEXT,
            symbol         TEXT,
            action         TEXT,
            confidence     REAL,
            entry_price    REAL,
            stop_loss      REAL,
            take_profit    REAL,
            position_size  REAL,
            ta_score       REAL,
            sentiment      TEXT,
            setup_type     TEXT,
            regime_tag     TEXT,
            quality_score  REAL,
            expectancy_score REAL,
            symbol_edge    REAL,
            setup_edge     REAL,
            quality_flags  TEXT,
            timestamp      TEXT,
            executed       INTEGER,
            source         TEXT,
            reasoning      TEXT,
            raw_json       TEXT,
            synced_at      TEXT
        );
        CREATE TABLE IF NOT EXISTS unified_summary (
            id                     INTEGER PRIMARY KEY CHECK(id = 1),
            synced_at              TEXT,
            combined_open_positions INTEGER,
            combined_open_pnl_inr  REAL,
            nse_cash               REAL,
            nse_total_trades       INTEGER,
            nse_wins               INTEGER,
            nse_open_positions     INTEGER,
            nse_total_pnl          REAL,
            fno_open_positions     INTEGER,
            fno_total_pnl          REAL,
            crypto_open_positions  INTEGER,
            crypto_total_pnl       REAL,
            us_open_positions      INTEGER,
            us_total_pnl           REAL
        );
        """
    )


def compose_unified_state() -> dict:
    os.makedirs("logs", exist_ok=True)
    memory = PortfolioMemory()
    portfolio = _load_json(
        VIRTUAL_PORTFOLIO_FILE,
        {"cash": VIRTUAL_CAPITAL, "positions": {}, "total_trades": 0, "wins": 0},
    )

    nse_positions = _nse_positions(portfolio)
    nse_trades = _dedupe_nse_trade_rows(_map_nse_trades(memory) + _map_nse_csv_trades())

    signals = []
    positions = list(nse_positions)
    trades = list(nse_trades)

    try:
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            signals.extend(_map_signal_rows(conn))
            fno_positions, fno_trades, fno_signals = _map_fno_rows(conn)
            crypto_positions, crypto_trades, crypto_signals = _map_simple_market_rows(conn, "crypto_trades", "crypto", "pnl_usdt", "usdt_amount")
            us_positions, us_trades, us_signals = _map_simple_market_rows(conn, "us_trades", "us", "pnl_usd", "usd_amount")
    except sqlite3.Error as exc:
        logger.warning(f"Unified state could not read SQLite sources: {exc}")
        fno_positions, fno_trades, fno_signals = [], [], []
        crypto_positions, crypto_trades, crypto_signals = [], [], []
        us_positions, us_trades, us_signals = [], [], []

    positions.extend(fno_positions + crypto_positions + us_positions)
    trades.extend(fno_trades + crypto_trades + us_trades)
    signals.extend(fno_signals + crypto_signals + us_signals)

    trades.sort(key=lambda item: item.get("exit_time") or item.get("entry_time") or "", reverse=True)
    signals.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    positions.sort(key=lambda item: item.get("entry_time") or "", reverse=True)

    combined_open_pnl_inr = round(
        sum(float(pos.get("pnl", 0) or 0) * (INR_PER_USDT if pos.get("market") == "crypto" else INR_PER_USD if pos.get("market") == "us" else 1)
            for pos in positions),
        2,
    )
    fno_total = sum(float(t.get("pnl", 0) or 0) for t in trades if t.get("market") == "fno" and t.get("status") == "closed")
    crypto_total = sum(float(t.get("pnl", 0) or 0) for t in trades if t.get("market") == "crypto" and t.get("status") == "closed")
    us_total = sum(float(t.get("pnl", 0) or 0) for t in trades if t.get("market") == "us" and t.get("status") == "closed")
    nse_total_pnl = round(float(portfolio.get("cash", VIRTUAL_CAPITAL)) - float(VIRTUAL_CAPITAL), 2)
    nse_closed_rows = [row for row in nse_trades if str(row.get("status", "")).lower() == "closed"]
    nse_detailed_pnl = round(sum(float(row.get("pnl", 0) or 0) for row in nse_closed_rows), 2)
    nse_recon_delta = round(nse_total_pnl - nse_detailed_pnl, 2)

    return {
        "synced_at": datetime.now().isoformat(),
        "summary": {
            "combined_open_positions": len(positions),
            "combined_open_pnl_inr": combined_open_pnl_inr,
            "nse_cash": round(float(portfolio.get("cash", VIRTUAL_CAPITAL) or VIRTUAL_CAPITAL), 2),
            "nse_total_trades": int(portfolio.get("total_trades", 0) or 0),
            "nse_wins": int(portfolio.get("wins", 0) or 0),
            "nse_open_positions": len(nse_positions),
            "nse_total_pnl": nse_total_pnl,
            "nse_detailed_trade_pnl": nse_detailed_pnl,
            "nse_reconciliation_delta": nse_recon_delta,
            "nse_trade_rows": len(nse_closed_rows),
            "nse_summary_mismatch": abs(nse_recon_delta) > 1.0,
            "fno_open_positions": len(fno_positions),
            "fno_total_pnl": round(fno_total, 2),
            "crypto_open_positions": len(crypto_positions),
            "crypto_total_pnl": round(crypto_total, 4),
            "us_open_positions": len(us_positions),
            "us_total_pnl": round(us_total, 4),
        },
        "treasury": {},
        "positions": positions,
        "trades": trades[:500],
        "signals": signals[:500],
    }


def sync_unified_state() -> dict:
    state = compose_unified_state()
    synced_at = state["synced_at"]
    try:
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            _ensure_sync_tables(conn)
            conn.execute("DELETE FROM unified_positions")
            conn.execute("DELETE FROM unified_trades")
            conn.execute("DELETE FROM unified_signals")

            conn.executemany(
                """
                INSERT INTO unified_positions (
                    position_key, market, symbol, instrument, side, strategy, status,
                    quantity, entry_price, current_price, pnl, pnl_pct, entry_time,
                    exit_time, expiry, source, reasoning, raw_json, synced_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        row["position_key"], row["market"], row["symbol"], row["instrument"],
                        row["side"], row["strategy"], row["status"], row["quantity"],
                        row["entry_price"], row["current_price"], row["pnl"], row["pnl_pct"],
                        row["entry_time"], row["exit_time"], row["expiry"], row["source"],
                        row["reasoning"], json.dumps(row), synced_at
                    )
                    for row in state["positions"]
                ],
            )
            conn.executemany(
                """
                INSERT INTO unified_trades (
                    trade_key, market, symbol, instrument, side, strategy, status,
                    quantity, entry_price, current_price, exit_price, pnl, pnl_pct,
                    entry_time, exit_time, expiry, source, reasoning, raw_json, synced_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        row["trade_key"], row["market"], row["symbol"], row["instrument"],
                        row["side"], row["strategy"], row["status"], row["quantity"],
                        row["entry_price"], row["current_price"], row["exit_price"], row["pnl"],
                        row["pnl_pct"], row["entry_time"], row["exit_time"], row["expiry"],
                        row["source"], row["reasoning"], json.dumps(row), synced_at
                    )
                    for row in state["trades"]
                ],
            )
            conn.executemany(
                """
                INSERT INTO unified_signals (
                    signal_key, market, symbol, action, confidence, entry_price,
                    stop_loss, take_profit, position_size, ta_score, sentiment,
                    setup_type, regime_tag, quality_score, expectancy_score,
                    symbol_edge, setup_edge, quality_flags, timestamp, executed,
                    source, reasoning, raw_json, synced_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        row["signal_key"], row["market"], row["symbol"], row["action"],
                        row["confidence"], row["entry_price"], row["stop_loss"], row["take_profit"],
                        row["position_size"], row["ta_score"], row["sentiment"],
                        row.get("setup_type", ""), row.get("regime_tag", ""),
                        row.get("quality_score", 0.0), row.get("expectancy_score", 0.0),
                        row.get("symbol_edge", 0.0), row.get("setup_edge", 0.0),
                        row.get("quality_flags", ""), row["timestamp"],
                        row["executed"], row["source"], row["reasoning"], json.dumps(row), synced_at
                    )
                    for row in state["signals"]
                ],
            )
            summary = state["summary"]
            conn.execute(
                """
                INSERT INTO unified_summary (
                    id, synced_at, combined_open_positions, combined_open_pnl_inr,
                    nse_cash, nse_total_trades, nse_wins, nse_open_positions, nse_total_pnl,
                    fno_open_positions, fno_total_pnl, crypto_open_positions, crypto_total_pnl,
                    us_open_positions, us_total_pnl
                ) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    synced_at=excluded.synced_at,
                    combined_open_positions=excluded.combined_open_positions,
                    combined_open_pnl_inr=excluded.combined_open_pnl_inr,
                    nse_cash=excluded.nse_cash,
                    nse_total_trades=excluded.nse_total_trades,
                    nse_wins=excluded.nse_wins,
                    nse_open_positions=excluded.nse_open_positions,
                    nse_total_pnl=excluded.nse_total_pnl,
                    fno_open_positions=excluded.fno_open_positions,
                    fno_total_pnl=excluded.fno_total_pnl,
                    crypto_open_positions=excluded.crypto_open_positions,
                    crypto_total_pnl=excluded.crypto_total_pnl,
                    us_open_positions=excluded.us_open_positions,
                    us_total_pnl=excluded.us_total_pnl
                """,
                (
                    synced_at,
                    summary["combined_open_positions"],
                    summary["combined_open_pnl_inr"],
                    summary["nse_cash"],
                    summary["nse_total_trades"],
                    summary["nse_wins"],
                    summary["nse_open_positions"],
                    summary["nse_total_pnl"],
                    summary["fno_open_positions"],
                    summary["fno_total_pnl"],
                    summary["crypto_open_positions"],
                    summary["crypto_total_pnl"],
                    summary["us_open_positions"],
                    summary["us_total_pnl"],
                ),
            )
    except sqlite3.Error as exc:
        logger.warning(f"Unified state could not write SQLite sync tables: {exc}")

    with open(UNIFIED_STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    state["treasury"] = write_treasury_snapshot(state)
    with open(UNIFIED_STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    write_review_report(state)
    logger.info(
        "Unified state synced | positions=%s trades=%s signals=%s",
        len(state["positions"]), len(state["trades"]), len(state["signals"])
    )
    return state


def load_unified_state(auto_sync: bool = True) -> dict:
    if os.path.exists(UNIFIED_STATE_FILE):
        try:
            with open(UNIFIED_STATE_FILE, encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            pass
    if auto_sync:
        try:
            return sync_unified_state()
        except Exception as exc:
            logger.warning(f"Unified state auto-sync failed: {exc}")
    return {"synced_at": "", "summary": {}, "positions": [], "trades": [], "signals": []}
