# =============================================================================
# dashboard/app.py — QuantEdge Pro  |  Bloomberg-style terminal
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import json, time
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace
import yfinance as yf

import settings.manager as S
from memory.portfolio_memory import PortfolioMemory
from utils.housekeeping import cleanup_runtime_artifacts, summarize_runtime_storage
from services.runtime_state import (
    file_freshness_rows as svc_file_freshness_rows,
    format_age as svc_format_age,
    get_health_snapshot as svc_get_health_snapshot,
    pid_running as svc_pid_running,
    read_scheduler_status as svc_read_scheduler_status,
    safe_mtime as svc_safe_mtime,
)
from services.dashboard_data import (
    build_activity_feed as svc_build_activity_feed,
    build_live_watchlist as svc_build_live_watchlist,
    normalise_trade_frame as svc_normalise_trade_frame,
    outcome_bucket_analytics as svc_outcome_bucket_analytics,
    quote_snapshot as svc_quote_snapshot,
    signal_analytics as svc_signal_analytics,
    signal_time_analytics as svc_signal_time_analytics,
    symbol_edge_analytics as svc_symbol_edge_analytics,
    trade_analytics as svc_trade_analytics,
    unified_signal_frame as svc_unified_signal_frame,
    unified_state as svc_unified_state,
    unified_trade_frame as svc_unified_trade_frame,
)
from services.review_report import (
    REVIEW_REPORT_JSON,
    REVIEW_REPORT_MD,
    render_review_markdown as svc_render_review_markdown,
    write_review_report as svc_write_review_report,
)
from services.state_sync import sync_unified_state as svc_sync_unified_state

# ── read live settings (not cached module-level config) ──────────────────────
def _cfg(key, default=None):
    return S.get(key, default)

st.set_page_config(
    page_title="QuantEdge Pro",
    page_icon="Q",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================================================================
# BLOOMBERG CSS
# =============================================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap');

/* ── Reset & base ── */
*, *::before, *::after { box-sizing: border-box; }
html, body, [class*="css"] {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 13px;
}
.stApp { background: #0a0a0a; color: #cccccc; }
.block-container { padding: 0.75rem 1.25rem !important; max-width: 1600px; }

/* ── Sidebar ── */
section[data-testid="stSidebar"] {
    background: #0d0d0d !important;
    border-right: 1px solid #222222 !important;
    min-width: 190px !important; max-width: 190px !important;
}
section[data-testid="stSidebar"] > div { padding: 12px 10px !important; }
section[data-testid="stSidebar"] .stRadio > label { display: none; }
section[data-testid="stSidebar"] .stRadio div[role="radiogroup"] { gap: 2px !important; }
section[data-testid="stSidebar"] .stRadio label {
    color: #888888 !important; font-size: 12px !important;
    font-family: 'JetBrains Mono', monospace !important;
    padding: 6px 10px !important; border-radius: 0 !important;
    border-left: 2px solid transparent !important;
    display: block !important; cursor: pointer;
    letter-spacing: 0.5px; text-transform: uppercase;
}
section[data-testid="stSidebar"] .stRadio label:hover { color: #FF6B00 !important; }
section[data-testid="stSidebar"] .stRadio label[data-baseweb="radio"] { display: flex !important; }
div[data-testid="stRadio"] label[aria-checked="true"] p {
    color: #FF6B00 !important; font-weight: 700 !important;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    background: #0d0d0d !important; border-bottom: 1px solid #222222 !important;
    gap: 0; border-radius: 0 !important;
}
.stTabs [data-baseweb="tab"] {
    background: transparent !important; color: #666666 !important;
    font-family: 'JetBrains Mono', monospace !important; font-size: 11px !important;
    border-radius: 0 !important; padding: 7px 14px !important;
    text-transform: uppercase; letter-spacing: 0.8px; border: none !important;
}
.stTabs [aria-selected="true"] {
    background: #111111 !important; color: #FF6B00 !important;
    border-bottom: 2px solid #FF6B00 !important;
}
.stTabs [data-baseweb="tab-panel"] { padding: 14px 0 0 !important; }

/* ── Metrics ── */
div[data-testid="metric-container"] {
    background: #111111 !important; border: 1px solid #1e1e1e !important;
    border-radius: 0 !important; padding: 10px 14px !important;
    border-left: 2px solid #FF6B00 !important;
}
div[data-testid="stMetricLabel"] p {
    color: #666666 !important; font-size: 10px !important;
    text-transform: uppercase; letter-spacing: 1px;
    font-family: 'JetBrains Mono', monospace !important;
}
div[data-testid="stMetricValue"] {
    color: #eeeeee !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 1.2rem !important; font-weight: 600 !important;
}
div[data-testid="stMetricDelta"] {
    font-family: 'JetBrains Mono', monospace !important; font-size: 11px !important;
}

/* ── Buttons ── */
.stButton > button {
    background: #111111 !important; color: #FF6B00 !important;
    border: 1px solid #FF6B00 !important; border-radius: 0 !important;
    font-family: 'JetBrains Mono', monospace !important; font-size: 11px !important;
    font-weight: 500 !important; letter-spacing: 0.8px; text-transform: uppercase;
    padding: 6px 14px !important; transition: all 0.15s !important;
}
.stButton > button:hover {
    background: #FF6B00 !important; color: #000000 !important;
}
.stButton > button[kind="primary"] {
    background: #FF6B00 !important; color: #000000 !important;
    font-weight: 700 !important;
}
.stButton > button[kind="primary"]:hover {
    background: #ff8c00 !important;
}

/* ── Inputs ── */
.stTextInput input, .stPasswordInput input, .stSelectbox select,
.stNumberInput input, .stTimeInput input {
    background: #111111 !important; border: 1px solid #2a2a2a !important;
    color: #cccccc !important; border-radius: 0 !important;
    font-family: 'JetBrains Mono', monospace !important; font-size: 12px !important;
}
.stTextInput input:focus, .stPasswordInput input:focus, .stNumberInput input:focus {
    border-color: #FF6B00 !important; box-shadow: none !important;
}
.stSlider [data-baseweb="slider"] { border-radius: 0 !important; }
.stSlider [role="slider"] { background: #FF6B00 !important; border-radius: 0 !important; }

/* ── Selectbox ── */
.stSelectbox [data-baseweb="select"] > div {
    background: #111111 !important; border: 1px solid #2a2a2a !important;
    border-radius: 0 !important; color: #cccccc !important;
    font-family: 'JetBrains Mono', monospace !important;
}

/* ── Toggle ── */
.stCheckbox label, .stToggle label {
    color: #888888 !important; font-family: 'JetBrains Mono', monospace !important;
    font-size: 11px !important; text-transform: uppercase; letter-spacing: 0.5px;
}

/* ── Dividers & misc ── */
hr { border-color: #1e1e1e !important; margin: 8px 0 !important; }
.stInfo, .stSuccess, .stWarning, .stError {
    border-radius: 0 !important; font-family: 'JetBrains Mono', monospace !important;
    font-size: 11px !important;
}
.stInfo    { background: rgba(255,107,0,0.07) !important; border: 1px solid rgba(255,107,0,0.25) !important; }
.stSuccess { background: rgba(0,200,5,0.07) !important;   border: 1px solid rgba(0,200,5,0.25) !important; }
.stWarning { background: rgba(255,180,0,0.07) !important; border: 1px solid rgba(255,180,0,0.25) !important; }
.stError   { background: rgba(255,59,59,0.07) !important; border: 1px solid rgba(255,59,59,0.25) !important; }

/* ── Dataframe / tables ── */
.stDataFrame { border: 1px solid #1e1e1e !important; border-radius: 0 !important; }
.stDataFrame [data-testid="stDataFrameResizable"] { border-radius: 0 !important; }

::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: #0a0a0a; }
::-webkit-scrollbar-thumb { background: #2a2a2a; border-radius: 0; }

/* ── Custom classes ── */
.bb-header {
    font-size: 10px; color: #FF6B00; text-transform: uppercase;
    letter-spacing: 2px; font-weight: 600; margin-bottom: 6px;
    border-bottom: 1px solid #1e1e1e; padding-bottom: 4px;
}

/* ── Multi-market P&L strip ── */
.mkt-strip {
    display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px;
    margin-bottom: 8px;
}
.mkt-cell {
    background: #111111; border: 1px solid #1e1e1e;
    border-top: 2px solid #2a2a2a;
    padding: 8px 10px; text-align: center;
}
.mkt-cell.nse  { border-top-color: #FF6B00; }
.mkt-cell.fno  { border-top-color: #00BFFF; }
.mkt-cell.cr   { border-top-color: #F7931A; }
.mkt-cell.us   { border-top-color: #4169E1; }
.mkt-cell.tot  { border-top-color: #00C805; background: #0d1a0d; }
.mkt-lbl { font-size: 9px; color: #555; text-transform: uppercase;
           letter-spacing: 1px; margin-bottom: 4px; }
.mkt-val { font-size: 13px; font-weight: 600; font-family: 'JetBrains Mono'; }

/* ── Market Intel card ── */
.intel-card {
    background: #0d0d0d; border: 1px solid #1e1e1e;
    padding: 10px 12px; margin-bottom: 8px;
}
.intel-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 4px 0; border-bottom: 1px solid #151515; font-size: 11px;
}
.intel-row:last-child { border-bottom: none; }
.intel-key { color: #555; font-size: 10px; text-transform: uppercase;
             letter-spacing: 0.8px; }
.badge {
    font-size: 9px; font-weight: 700; padding: 2px 7px;
    letter-spacing: 0.8px; text-transform: uppercase;
}
.badge-bull { color: #00C805; border: 1px solid #00C805; }
.badge-bear { color: #FF3B3B; border: 1px solid #FF3B3B; }
.badge-side { color: #FFB347; border: 1px solid #FFB347; }
.badge-buy  { color: #00BFFF; border: 1px solid #00BFFF; }
.badge-sell { color: #FF6B00; border: 1px solid #FF6B00; }
.badge-neu  { color: #888888; border: 1px solid #444444; }
.bb-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 5px 0; border-bottom: 1px solid #151515; font-size: 12px;
}
.bb-label  { color: #666666; text-transform: uppercase; letter-spacing: 0.5px; font-size: 10px; }
.bb-val    { color: #cccccc; font-weight: 500; }
.bb-pos    { color: #00C805; font-weight: 600; }
.bb-neg    { color: #FF3B3B; font-weight: 600; }
.bb-orange { color: #FF6B00; font-weight: 600; }

.sig-card {
    background: #111111; border: 1px solid #1e1e1e; border-left: 3px solid #FF6B00;
    padding: 12px 14px; margin-bottom: 8px;
}
.sig-card:hover { border-left-color: #ff8c00; background: #141414; }
.sig-sym { font-size: 16px; font-weight: 700; color: #eeeeee; }
.sig-buy  { color: #00C805; font-size: 10px; font-weight: 700; letter-spacing: 1px;
            border: 1px solid #00C805; padding: 1px 6px; }
.sig-sell { color: #FF3B3B; font-size: 10px; font-weight: 700; letter-spacing: 1px;
            border: 1px solid #FF3B3B; padding: 1px 6px; }
.sig-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 8px; margin: 8px 0; }
.sig-cell-lbl { font-size: 9px; color: #555555; text-transform: uppercase; letter-spacing: 0.8px; }
.sig-cell-val { font-size: 13px; color: #cccccc; font-weight: 500; }
.sig-bar-wrap { background: #1e1e1e; height: 3px; margin: 2px 0 8px; }
.sig-bar-fill { height: 3px; background: #FF6B00; }
.sig-story { font-size: 11px; color: #888888; line-height: 1.6;
             border-top: 1px solid #1e1e1e; padding-top: 8px; margin-top: 4px; }

.pos-row {
    display: grid; grid-template-columns: 100px 1fr 1fr 1fr 1fr 80px;
    gap: 8px; padding: 8px 12px; border-bottom: 1px solid #151515;
    align-items: center; font-size: 12px;
}
.pos-row:hover { background: #111111; }

.regime-bull { color: #00C805; }
.regime-bear { color: #FF3B3B; }
.regime-side { color: #FFB347; }

.ticker-strip {
    background: #0d0d0d; border-bottom: 1px solid #1e1e1e;
    padding: 5px 0; font-size: 11px; color: #666666; overflow: hidden;
    white-space: nowrap; margin-bottom: 10px;
    display: flex; gap: 24px; align-items: center;
}

/* ── MOBILE RESPONSIVE ── */
@media (max-width: 768px) {
    /* Collapse sidebar on mobile */
    section[data-testid="stSidebar"] {
        min-width: 100% !important; max-width: 100% !important;
    }
    /* Looser padding on small screens */
    .block-container { padding: 0.5rem 0.6rem !important; }
    /* Bigger tap targets for buttons */
    .stButton > button { padding: 10px 16px !important; font-size: 12px !important; }
    /* Signal cards full-width, easier to read */
    .sig-grid { grid-template-columns: repeat(2, 1fr) !important; }
    .sig-card { padding: 10px !important; }
    /* Position rows stack vertically */
    .pos-row {
        grid-template-columns: 1fr 1fr !important;
        gap: 4px !important; font-size: 11px !important;
    }
    /* Ticker strip scrolls horizontally */
    .ticker-strip { overflow-x: auto !important; }
    /* Metrics: 2 per row instead of 4 */
    div[data-testid="column"] { min-width: 45% !important; }
    /* Sidebar nav labels bigger for thumb tap */
    section[data-testid="stSidebar"] .stRadio label {
        font-size: 14px !important; padding: 10px 12px !important;
    }
    /* Hide heavy desktop text */
    .desktop-only { display: none !important; }
    /* Tabs scroll horizontally */
    .stTabs [data-baseweb="tab-list"] { overflow-x: auto !important; flex-wrap: nowrap !important; }
    .stTabs [data-baseweb="tab"] { min-width: max-content !important; }
    /* Charts shorter on mobile */
    div[data-testid="stPlotlyChart"] { max-height: 260px !important; }
    /* Tables smaller font */
    .stDataFrame { font-size: 10px !important; }
    /* Inputs full-width */
    .stTextInput, .stNumberInput, .stSelectbox { width: 100% !important; }
}

@media (max-width: 480px) {
    html, body, [class*="css"] { font-size: 12px !important; }
    .sig-sym { font-size: 14px !important; }
    .sig-grid { grid-template-columns: 1fr 1fr !important; }
    div[data-testid="stMetricValue"] { font-size: 1.0rem !important; }
}
</style>
""", unsafe_allow_html=True)

# =============================================================================
# HELPERS  (all data-loaders are cached to avoid re-fetching on every render)
# =============================================================================

@st.cache_data(ttl=30)
def _load_pf():
    """Cached 30s — portfolio changes only when a trade executes."""
    f = "logs/virtual_portfolio.json"
    if os.path.exists(f):
        with open(f) as fp: return json.load(fp)
    vc = _cfg("VIRTUAL_CAPITAL", 1_000_000)
    return {"cash": vc, "positions": {}, "total_trades": 0, "wins": 0}

@st.cache_data(ttl=60)
def _load_json(path, default=None):
    """Cached 60s — regime/PCR/FII files update at most once per scan."""
    if os.path.exists(path):
        try:
            with open(path) as f: return json.load(f)
        except Exception: pass
    return default or {}

def _plotly_cfg(height=260, **kwargs):
    base = dict(
        height=height, margin=dict(l=0, r=0, t=8, b=0),
        plot_bgcolor="#0a0a0a", paper_bgcolor="#0a0a0a",
        xaxis=dict(gridcolor="#151515", color="#555555", showgrid=True),
        yaxis=dict(gridcolor="#151515", color="#555555", showgrid=True),
        font=dict(family="JetBrains Mono", color="#666666", size=10),
        showlegend=False,
    )
    base.update(kwargs)
    return base

@st.cache_data(ttl=300, show_spinner=False)
def _fetch_chart_data(symbol: str, period: str = "3mo"):
    """Cached 5 min — charts don't need real-time updates."""
    try:
        ticker = f"{symbol}.NS" if not symbol.startswith("^") else symbol
        df = yf.Ticker(ticker).history(period=period, interval="1d")
        if df.empty: return None
        df["EMA20"] = df["Close"].ewm(span=20).mean()
        df["EMA50"] = df["Close"].ewm(span=50).mean()
        return df
    except Exception: return None

def _chart(symbol, period="3mo", height=200):
    df = _fetch_chart_data(symbol, period)
    if df is None: return
    try:
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"],
            low=df["Low"], close=df["Close"], showlegend=False,
            increasing=dict(line=dict(color="#00C805"), fillcolor="rgba(0,200,5,0.2)"),
            decreasing=dict(line=dict(color="#FF3B3B"), fillcolor="rgba(255,59,59,0.2)"),
        ))
        fig.add_trace(go.Scatter(x=df.index, y=df["EMA20"],
            line=dict(color="#FF6B00", width=1), name="EMA20"))
        fig.add_trace(go.Scatter(x=df.index, y=df["EMA50"],
            line=dict(color="#888888", width=1), name="EMA50"))
        fig.update_layout(**_plotly_cfg(height=height),
                          xaxis_rangeslider_visible=False,
                          showlegend=True,
                          legend=dict(orientation="h", y=1.05,
                                      font=dict(color="#666666", size=9)))
        st.plotly_chart(fig, use_container_width=True)
    except Exception: pass

@st.cache_data(ttl=60, show_spinner=False)
def _live_price(symbol):
    """Cached 60s — price per symbol, avoids repeated yfinance calls."""
    try:
        h = yf.Ticker(f"{symbol}.NS").history(period="1d", interval="15m")
        return float(h["Close"].iloc[-1]) if not h.empty else None
    except Exception: return None

@st.cache_data(ttl=120, show_spinner=False)
def _get_banknifty_return():
    """Cached 2 min — BankNifty daily return for ticker strip."""
    try:
        _bn = yf.Ticker("^NSEBANK").history(period="5d", interval="1d")
        return float((_bn["Close"].iloc[-1] - _bn["Close"].iloc[-2]) /
                     _bn["Close"].iloc[-2] * 100) if len(_bn) >= 2 else 0.0
    except Exception: return 0.0

@st.cache_data(ttl=60, show_spinner=False)
def _get_memory_stats():
    """Cached 60s — SQLite stats query."""
    return PortfolioMemory().get_stats()

@st.cache_data(ttl=60, show_spinner=False)
def _get_recent_signals(limit=100):
    """Cached 60s — signal history from SQLite/ChromaDB."""
    return PortfolioMemory().get_recent_signals(limit=limit)

@st.cache_data(ttl=60, show_spinner=False)
def _load_review_report_json():
    if os.path.exists(REVIEW_REPORT_JSON):
        try:
            with open(REVIEW_REPORT_JSON, encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            pass
    state = svc_unified_state(auto_sync=True)
    return svc_write_review_report(state)

@st.cache_data(ttl=60, show_spinner=False)
def _load_review_report_markdown():
    if os.path.exists(REVIEW_REPORT_MD):
        try:
            with open(REVIEW_REPORT_MD, encoding="utf-8") as handle:
                return handle.read()
        except Exception:
            pass
    return svc_render_review_markdown(_load_review_report_json())

@st.cache_data(ttl=60, show_spinner=False)
def _get_equity_snapshots():
    """Cached 60s — equity curve snapshots."""
    return PortfolioMemory().get_snapshots()

@st.cache_data(ttl=60, show_spinner=False)
def _get_fno_stats():
    """Cached 60s — F&O broker stats + positions."""
    try:
        from execution.brokers.fno_paper_broker import FNOPaperBroker
        b = FNOPaperBroker()
        return b.get_stats(), b.get_open_positions(), b.get_closed_trades(limit=20)
    except Exception as e:
        return {}, [], []

@st.cache_data(ttl=60, show_spinner=False)
def _get_crypto_stats():
    """Cached 60s — Crypto broker stats + positions."""
    try:
        from execution.brokers.crypto_paper_broker import CryptoPaperBroker
        b = CryptoPaperBroker()
        return b.get_stats(), b.get_open_positions(), b.get_closed_trades(limit=20)
    except Exception as e:
        return {}, [], []

@st.cache_data(ttl=60, show_spinner=False)
def _get_us_stats():
    """Cached 60s — US broker stats + positions."""
    try:
        from execution.brokers.us_paper_broker import USPaperBroker
        b = USPaperBroker()
        return b.get_stats(), b.get_open_positions(), b.get_closed_trades(limit=20)
    except Exception as e:
        return {}, [], []

def _sig_ns(d):
    return SimpleNamespace(
        symbol=d.get("symbol",""), action=d.get("action","HOLD"),
        confidence=d.get("confidence",0.5),
        entry_price=d.get("entry_price",0) or 0,
        stop_loss=d.get("stop_loss",0) or 0,
        take_profit=d.get("take_profit",0) or 0,
        position_size=d.get("position_size",0) or 0,
        ta_score=d.get("ta_score",5), sentiment=d.get("sentiment","neutral"),
        sentiment_score=d.get("sentiment_score",0),
        raw_ta=d.get("raw_ta",{}), reasoning=d.get("reasoning",""),
    )

@st.cache_resource
def _narrator():
    try:
        from analysis.signal_narrator import SignalNarrator
        return SignalNarrator(use_llm=False)
    except Exception: return None

def _story(d):
    n = _narrator()
    if not n: return ""
    try: return n.narrate(_sig_ns(d))
    except Exception: return ""

def _bb_row(label, val, color="#cccccc"):
    return f"""<div class="bb-row">
        <span class="bb-label">{label}</span>
        <span style="color:{color};font-weight:500;">{val}</span>
    </div>"""


def _safe_mtime(path):
    return svc_safe_mtime(path)


def _format_age(ts: float) -> str:
    return svc_format_age(ts)


def _pid_running(pid: int) -> bool:
    return svc_pid_running(pid)


def _load_scheduler_status():
    return svc_read_scheduler_status()


@st.cache_data(ttl=30, show_spinner=False)
def _get_health_snapshot():
    return svc_get_health_snapshot(_cfg)


def _render_health_panel(snapshot: dict, show_actions: bool = False):
    scheduler_state = "RUNNING" if snapshot["scheduler_running"] else "STOPPED"
    scheduler_delta = f"PID {snapshot['scheduler_pid']}" if snapshot["scheduler_pid"] else "No PID file"
    db_state = "OK" if snapshot["db_ok"] else "ERROR"
    db_delta = f"{snapshot['storage']['db_size_mb']:.2f} MB"
    alert_ready = int(snapshot["telegram_ready"]) + int(snapshot["discord_ready"])
    alert_delta = []
    if snapshot["telegram_ready"]:
        alert_delta.append("TG")
    if snapshot["discord_ready"]:
        alert_delta.append("DC")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("SCHEDULER", scheduler_state, delta=scheduler_delta)
    c2.metric("DATABASE", db_state, delta=db_delta)
    c3.metric("ALERT CHANNELS", f"{alert_ready}/2", delta=", ".join(alert_delta) if alert_delta else "None")
    c4.metric("LOG FOOTPRINT", f"{snapshot['storage']['log_size_mb']:.2f} MB",
              delta=f"{snapshot['storage']['log_files']} files")

    status_rows = pd.DataFrame([
        {"Component": "Latest scan artifact", "Status": _format_age(snapshot["latest_signal_ts"])},
        {"Component": "Latest agent log", "Status": _format_age(snapshot["latest_log_ts"])},
        {"Component": "Settings sync", "Status": _format_age(snapshot["settings_ts"])},
        {"Component": "Market cache", "Status": f"{snapshot['storage']['cache_files']} files / {snapshot['storage']['cache_size_mb']:.2f} MB"},
        {"Component": "Chroma storage", "Status": f"{snapshot['storage']['chroma_size_mb']:.2f} MB"},
        {"Component": "Backtest artifacts", "Status": f"{snapshot['storage']['backtest_files']} files / {snapshot['storage']['backtest_size_mb']:.2f} MB"},
        {"Component": "SQLite journal", "Status": "Present" if snapshot["storage"]["db_journal_present"] else "Clear"},
    ])
    st.dataframe(status_rows, use_container_width=True, hide_index=True, height=286)

    st.markdown('<div class="bb-header" style="margin-top:10px;">DATA FRESHNESS</div>', unsafe_allow_html=True)
    st.dataframe(pd.DataFrame(_file_freshness_rows()), use_container_width=True, hide_index=True, height=216)

    if not snapshot["db_ok"] and snapshot["db_error"]:
        st.warning(f"Database health check failed: {snapshot['db_error']}")

    jobs = (snapshot.get("scheduler_status") or {}).get("jobs", {})
    if jobs:
        st.markdown('<div class="bb-header" style="margin-top:10px;">JOB STATUS</div>', unsafe_allow_html=True)
        job_rows = []
        for name in [
            "daily_scan", "price_monitor", "fno_monitor", "intraday_scan",
            "eod_close", "outcome_tracker", "us_scan", "crypto_scan",
            "eod_digest", "weekly_summary", "housekeeping",
        ]:
            item = jobs.get(name, {})
            if not item:
                continue
            job_rows.append({
                "Job": name.replace("_", " ").upper(),
                "State": item.get("state", "---").upper(),
                "Updated": item.get("timestamp", "---"),
                "Detail": item.get("detail", ""),
            })
        if job_rows:
            st.dataframe(pd.DataFrame(job_rows), use_container_width=True, hide_index=True, height=320)

    if show_actions:
        a1, a2 = st.columns([1, 1])
        if a1.button("RUN CLEANUP NOW", use_container_width=True):
            result = cleanup_runtime_artifacts()
            st.cache_data.clear()
            st.success(
                f"Cleanup finished. Removed {result.get('removed_files', 0)} file(s); "
                f"logs now {result.get('log_size_mb', 0.0):.2f} MB."
            )
            st.rerun()
        if a2.button("REFRESH HEALTH", use_container_width=True):
            st.cache_data.clear()
            st.rerun()


def _should_live_refresh(page_name: str) -> bool:
    enabled = bool(st.session_state.get("live_refresh_enabled", False))
    return enabled and page_name in {"TODAY", "PORTFOLIO"}


def _queue_live_refresh(page_name: str):
    if _should_live_refresh(page_name):
        refresh_sec = int(_cfg("DASHBOARD_REFRESH_SEC", 30))
        st.caption(f"LIVE REFRESH ACTIVE · reruns every {refresh_sec}s on this page")
        time.sleep(refresh_sec)
        st.rerun()


def _file_freshness_rows() -> list[dict]:
    return svc_file_freshness_rows()


def _trade_analytics(closed: pd.DataFrame) -> dict:
    return svc_trade_analytics(closed)


def _normalise_trade_frame(trades) -> pd.DataFrame:
    return svc_normalise_trade_frame(trades)


def _unified_state(auto_sync: bool = True) -> dict:
    return svc_unified_state(auto_sync=auto_sync)


def _treasury_snapshot(auto_sync: bool = True) -> dict:
    return (_unified_state(auto_sync=auto_sync).get("treasury") or {})


def _render_treasury_warning(snapshot: dict):
    warnings = list(snapshot.get("warnings") or [])
    if warnings:
        for warning in warnings[:3]:
            st.warning(f"Risk Warning: {warning}")


def _unified_trade_frame(limit: int = 500, auto_sync: bool = True) -> pd.DataFrame:
    return svc_unified_trade_frame(limit=limit, auto_sync=auto_sync)


def _unified_signal_frame(limit: int = 500, auto_sync: bool = True) -> pd.DataFrame:
    return svc_unified_signal_frame(limit=limit, auto_sync=auto_sync)


def _signal_analytics(signals: pd.DataFrame) -> dict:
    return svc_signal_analytics(signals)


def _outcome_bucket_analytics(outcomes: pd.DataFrame) -> dict:
    return svc_outcome_bucket_analytics(outcomes)


def _symbol_edge_analytics(outcomes: pd.DataFrame, min_sample: int = 3) -> dict:
    return svc_symbol_edge_analytics(outcomes, min_sample=min_sample)


def _signal_time_analytics(signals: pd.DataFrame) -> dict:
    return svc_signal_time_analytics(signals)


@st.cache_data(ttl=60, show_spinner=False)
def _quote_snapshot(symbol: str) -> dict:
    return svc_quote_snapshot(symbol)


def _live_watchlist(signals: list[dict], positions: dict, limit: int = 8) -> pd.DataFrame:
    return svc_build_live_watchlist(signals, positions, limit=limit)


def _activity_feed(limit: int = 12) -> pd.DataFrame:
    return svc_build_activity_feed(limit=limit)


def _scheduler_heartbeat_label(snapshot: dict) -> str:
    status = snapshot.get("scheduler_status") or {}
    heartbeat = status.get("heartbeat", "")
    return heartbeat or "No heartbeat yet"


def _render_config_summary(cfg: dict, snapshot: dict):
    st.markdown('<div class="bb-header">ADMIN SNAPSHOT</div>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("TRADING MODE", str(cfg.get("TRADING_MODE", "paper")).upper())
    c2.metric("AGENT MODE", str(cfg.get("AGENT_MODE", "copilot")).upper())
    c3.metric(
        "ALERT READY",
        f"{int(bool(cfg.get('TELEGRAM_BOT_TOKEN') and cfg.get('TELEGRAM_CHAT_ID'))) + int(bool(cfg.get('DISCORD_BOT_TOKEN') and cfg.get('DISCORD_CHANNEL_ID')))}/2",
        delta="TG/DC",
    )
    c4.metric(
        "SCHEDULER",
        "RUNNING" if snapshot.get("scheduler_running") else "STOPPED",
        delta=_scheduler_heartbeat_label(snapshot),
    )

    q1, q2, q3 = st.columns(3)
    if q1.button("RELOAD SETTINGS", use_container_width=True, key="cfg_reload_settings"):
        S.reload()
        st.cache_data.clear()
        st.rerun()
    if q2.button("CLEAR DASHBOARD CACHE", use_container_width=True, key="cfg_clear_cache"):
        st.cache_data.clear()
        st.success("Dashboard caches cleared.")
        st.rerun()
    if q3.button("REFRESH OPS SNAPSHOT", use_container_width=True, key="cfg_refresh_health"):
        st.cache_data.clear()
        st.rerun()


memory = PortfolioMemory()

# =============================================================================
# SIDEBAR
# =============================================================================
with st.sidebar:
    st.markdown("""
    <div style="padding:10px 0 14px;border-bottom:1px solid #222;margin-bottom:12px;">
        <div style="font-size:16px;font-weight:700;color:#FF6B00;letter-spacing:1px;">QE PRO</div>
        <div style="font-size:9px;color:#444;text-transform:uppercase;letter-spacing:2px;margin-top:2px;">
            QuantEdge Terminal</div>
    </div>
    """, unsafe_allow_html=True)

    # Status bar
    mode = _cfg("TRADING_MODE", "paper")
    agent_mode = _cfg("AGENT_MODE", "copilot")
    mc = "#00C805" if mode == "paper" else "#FF3B3B"
    reg = _load_json("logs/market_regime.json")
    regime_str = "---"
    regime_col = "#666666"
    if reg:
        rg = reg.get("regime", "unknown")
        regime_col = {"bull":"#00C805","bear":"#FF3B3B"}.get(rg,"#FFB347")
        regime_str = f"NIFTY [{rg.upper()}]"

    _sb_health = _get_health_snapshot()
    _sb_sched_col = "#00C805" if _sb_health["scheduler_running"] else "#FF3B3B"
    _sb_sched_lbl = "RUNNING" if _sb_health["scheduler_running"] else "STOPPED"

    st.markdown(f"""
    <div style="font-size:10px;margin-bottom:10px;">
        <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
            <span style="color:#444;text-transform:uppercase;letter-spacing:0.5px;">Mode</span>
            <span style="color:{mc};font-weight:600;">{mode.upper()}</span>
        </div>
        <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
            <span style="color:#444;text-transform:uppercase;letter-spacing:0.5px;">Agent</span>
            <span style="color:#FF6B00;font-weight:600;">{agent_mode.upper()}</span>
        </div>
        <div style="display:flex;justify-content:space-between;">
            <span style="color:#444;text-transform:uppercase;letter-spacing:0.5px;">Market</span>
            <span style="color:{regime_col};font-weight:600;">{regime_str}</span>
        </div>
        <div style="display:flex;justify-content:space-between;margin-top:4px;">
            <span style="color:#444;text-transform:uppercase;letter-spacing:0.5px;">Scheduler</span>
            <span style="color:{_sb_sched_col};font-weight:600;">{_sb_sched_lbl}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div style="border-top:1px solid #1e1e1e;margin:8px 0;"></div>', unsafe_allow_html=True)

    page = st.radio("NAV", [
        "TODAY",
        "PORTFOLIO",
        "RESEARCH",
        "HISTORY",
        "CONFIG",
    ], label_visibility="collapsed")

    st.markdown('<div style="border-top:1px solid #1e1e1e;margin:8px 0;"></div>', unsafe_allow_html=True)

    # Mini portfolio card
    pf   = _load_pf()
    cash = pf.get("cash", _cfg("VIRTUAL_CAPITAL", 1_000_000))
    vc   = _cfg("VIRTUAL_CAPITAL", 1_000_000)
    pnl  = cash - vc
    pc   = "#00C805" if pnl >= 0 else "#FF3B3B"
    # Combined P&L across all markets (cached)
    _sb_inr  = _cfg("INR_PER_USD", 83.0)
    _sb_fs, _, _ = _get_fno_stats()
    _sb_cs, _, _ = _get_crypto_stats()
    _sb_us_s,_, _= _get_us_stats()
    _sb_fno  = _sb_fs.get("total_pnl", 0) or 0
    _sb_cry  = (_sb_cs.get("total_pnl_usdt", 0) or 0) * _sb_inr
    _sb_us   = (_sb_us_s.get("total_pnl_usd", 0) or 0) * _sb_inr
    _sb_comb = pnl + _sb_fno + _sb_cry + _sb_us
    _sb_extra = True
    _cpc = "#00C805" if _sb_comb >= 0 else "#FF3B3B"
    st.markdown(f"""
    <div style="background:#111111;border:1px solid #1e1e1e;padding:10px;
                border-left:2px solid #FF6B00;">
        <div style="font-size:9px;color:#444;text-transform:uppercase;
                    letter-spacing:1px;margin-bottom:6px;">Portfolio</div>
        <div style="font-size:17px;font-weight:700;color:#eeeeee;">
            Rs.{cash:,.0f}</div>
        <div style="font-size:10px;color:{pc};margin-top:3px;">
            NSE {'+' if pnl>=0 else ''}Rs.{pnl:,.0f}</div>
        {'<div style="font-size:10px;color:'+_cpc+';margin-top:2px;border-top:1px solid #1e1e1e;padding-top:3px;">ALL '+('+' if _sb_comb>=0 else '')+'Rs.'+f"{_sb_comb:,.0f}"+'</div>' if _sb_extra else ''}
    </div>
    """, unsafe_allow_html=True)

    if st.button("⟳ REFRESH", use_container_width=True, key="sidebar_refresh"):
        st.cache_data.clear()
        st.rerun()

    refresh_sec = int(_cfg("DASHBOARD_REFRESH_SEC", 30))
    st.toggle(
        "LIVE REFRESH",
        key="live_refresh_enabled",
        help=f"Opt-in lightweight rerun loop for Today and Portfolio pages ({refresh_sec}s interval).",
    )
    st.caption(f"Interval: {refresh_sec}s")

    st.markdown(f"""
    <div style="font-size:9px;color:#333;margin-top:4px;text-align:right;">
        {datetime.now().strftime('%d %b %Y  %H:%M')}</div>
    """, unsafe_allow_html=True)


# =============================================================================
# PAGE: TODAY
# =============================================================================
if page == "TODAY":
    stats = _get_memory_stats()
    pf    = _load_pf()
    cash  = pf.get("cash", _cfg("VIRTUAL_CAPITAL", 1_000_000))
    vc    = _cfg("VIRTUAL_CAPITAL", 1_000_000)
    pnl   = cash - vc
    pos   = pf.get("positions", {})

    # ── Live market data for strips ───────────────────────────────────────────
    reg     = _load_json("logs/market_regime.json")
    rsi_val = reg.get("rsi", 0) if reg else 0
    ret_1m  = reg.get("ret_1m", 0) if reg else 0
    pcr_d   = _load_json("logs/pcr_signal.json")
    pcr_val = pcr_d.get("pcr", 0) if pcr_d else 0

    # BankNifty 1-day return (cached 2 min)
    bn_ret = _get_banknifty_return()

    # FII net from file
    fii_d   = _load_json("logs/fii_signal.json")
    fii_net = fii_d.get("fii_net", 0) if fii_d else 0
    fii_sig = fii_d.get("signal", "") if fii_d else ""

    # Market open/closed indicator
    from datetime import time as _dtime
    import pytz as _pytz
    _ist  = _pytz.timezone("Asia/Kolkata")
    _now  = datetime.now(_ist)
    _mkt_open = _dtime(9, 15) <= _now.time() <= _dtime(15, 30) and _now.weekday() < 5
    _mkt_label = "LIVE" if _mkt_open else "CLOSED"
    _mkt_color = "#00C805" if _mkt_open else "#555555"

    rg_str = reg.get("regime", "unknown").upper() if reg else "---"
    rg_col = {"BULL": "#00C805", "BEAR": "#FF3B3B"}.get(rg_str, "#FFB347")

    # Multi-market stats for strip (all cached 60s)
    _inr = _cfg("INR_PER_USD", 83.0)
    _fno_s,  _, _ = _get_fno_stats()
    _cry_s,  _, _ = _get_crypto_stats()
    _us_s,   _, _ = _get_us_stats()

    _fno_pnl  = _fno_s.get("total_pnl", 0) or 0
    _cry_pnl  = (_cry_s.get("total_pnl_usdt", 0) or 0) * _inr
    _us_pnl   = (_us_s.get("total_pnl_usd",   0) or 0) * _inr
    _comb     = pnl + _fno_pnl + _cry_pnl + _us_pnl
    _fno_open = _fno_s.get("open_positions", 0) or 0
    _cry_open = _cry_s.get("open_positions", 0) or 0
    _us_open  = _us_s.get("open_positions",  0) or 0
    _total_open = len(pos) + _fno_open + _cry_open + _us_open
    _treasury = _treasury_snapshot(auto_sync=False)

    # ── Ticker strip ──────────────────────────────────────────────────────────
    st.markdown(f"""
    <div class="ticker-strip">
        <span style="color:{_mkt_color};font-weight:700;letter-spacing:1px;">
            ● {_mkt_label}</span>
        <span>NIFTY &nbsp;<span style="color:{'#00C805' if ret_1m>=0 else '#FF3B3B'}">
            {ret_1m:+.2f}%</span></span>
        <span>BANKNIFTY &nbsp;<span style="color:{'#00C805' if bn_ret>=0 else '#FF3B3B'}">
            {bn_ret:+.2f}%</span></span>
        <span>RSI <span style="color:#FF6B00">{rsi_val:.1f}</span></span>
        <span>PCR <span style="color:#FF6B00">{pcr_val:.2f}</span></span>
        <span>REGIME <span style="color:{rg_col}">{rg_str}</span></span>
        <span>FII <span style="color:{'#00C805' if fii_net>=0 else '#FF3B3B'}">
            {'▲' if fii_net>=0 else '▼'}{abs(fii_net):,.0f}Cr</span></span>
        <span>POS <span style="color:#FF6B00">{_total_open}</span></span>
        <span>WIN <span style="color:#FF6B00">{stats['win_rate_pct']:.1f}%</span></span>
        <span style="color:#444;">{_now.strftime('%H:%M IST')}</span>
    </div>
    """, unsafe_allow_html=True)

    # ── Multi-market P&L strip ────────────────────────────────────────────────
    def _pnl_color(v): return "#00C805" if v >= 0 else "#FF3B3B"
    def _pnl_fmt(v, prefix="Rs."): return f"{prefix}{'+' if v>=0 else ''}{v:,.0f}"

    st.markdown(f"""
    <div class="mkt-strip">
      <div class="mkt-cell nse">
        <div class="mkt-lbl">NSE Equity</div>
        <div class="mkt-val" style="color:{_pnl_color(pnl)};">{_pnl_fmt(pnl)}</div>
        <div style="font-size:9px;color:#444;margin-top:2px;">{len(pos)} positions</div>
      </div>
      <div class="mkt-cell fno">
        <div class="mkt-lbl">F&amp;O Paper</div>
        <div class="mkt-val" style="color:{_pnl_color(_fno_pnl)};">{_pnl_fmt(_fno_pnl)}</div>
        <div style="font-size:9px;color:#444;margin-top:2px;">{_fno_open} positions</div>
      </div>
      <div class="mkt-cell cr">
        <div class="mkt-lbl">Crypto</div>
        <div class="mkt-val" style="color:{_pnl_color(_cry_pnl)};">{_pnl_fmt(_cry_pnl)}</div>
        <div style="font-size:9px;color:#444;margin-top:2px;">{_cry_open} positions</div>
      </div>
      <div class="mkt-cell us">
        <div class="mkt-lbl">US Stocks</div>
        <div class="mkt-val" style="color:{_pnl_color(_us_pnl)};">{_pnl_fmt(_us_pnl)}</div>
        <div style="font-size:9px;color:#444;margin-top:2px;">{_us_open} positions</div>
      </div>
      <div class="mkt-cell tot">
        <div class="mkt-lbl">Combined P&amp;L</div>
        <div class="mkt-val" style="color:{_pnl_color(_comb)};font-size:15px;">{_pnl_fmt(_comb)}</div>
        <div style="font-size:9px;color:#444;margin-top:2px;">{_total_open} total open</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    _render_treasury_warning(_treasury)

    # ── KPI row ───────────────────────────────────────────────────────────────
    k1,k2,k3 = st.columns(3)
    k1.metric("PORTFOLIO",      f"Rs.{cash:,.0f}")
    k2.metric("NSE P&L",        f"Rs.{pnl:+,.0f}",     delta=f"{pnl/vc*100:+.2f}%")
    k3.metric("OPEN POSITIONS", _total_open, delta=f"NSE:{len(pos)} F&O:{_fno_open}")
    k4,k5,k6 = st.columns(3)
    k4.metric("FREE CASH",      f"Rs.{float(_treasury.get('available_cash_inr', cash) or cash):,.0f}")
    k5.metric("RESERVED",       f"Rs.{float(_treasury.get('reserved_cash_inr', 0) or 0):,.0f}")
    k6.metric("COMBINED P&L",   f"Rs.{_comb:+,.0f}")

    st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)

    tab_ov, tab_sig, tab_live, tab_alerts, tab_fno, tab_crypto, tab_us, tab_health = st.tabs([
        "OVERVIEW", "SIGNALS", "LIVE DESK", "ALERT CENTER", "F&O BOOK", "CRYPTO", "US STOCKS", "HEALTH"
    ])

    # ── Overview ──────────────────────────────────────────────────────────────
    with tab_ov:
        left, right = st.columns([2, 1])

        with left:
            # Regime banner
            if reg:
                rg = reg.get("regime","unknown")
                css = {"bull":"regime-bull","bear":"regime-bear"}.get(rg,"regime-side")
                icon= {"bull":"[BULL]","bear":"[BEAR]","sideways":"[SIDE]"}.get(rg,"[?]")
                allow = rg != "bear"
                st.markdown(f"""
                <div style="background:#111111;border:1px solid #1e1e1e;border-left:3px solid
                            {'#00C805' if rg=='bull' else '#FF3B3B' if rg=='bear' else '#FFB347'};
                            padding:8px 12px;font-size:11px;margin-bottom:8px;">
                    <span class="{css}" style="font-weight:700;margin-right:12px;">{icon}</span>
                    RSI {reg.get("rsi",0):.1f} &nbsp;|&nbsp;
                    1M {reg.get("ret_1m",0):+.1f}% &nbsp;|&nbsp;
                    <span style="color:{'#00C805' if allow else '#FF3B3B'}">
                        {'TRADING ACTIVE' if allow else 'TRADING BLOCKED'}</span>
                </div>
                """, unsafe_allow_html=True)

            # Equity curve
            snaps = _get_equity_snapshots()
            if snaps:
                st.markdown('<div class="bb-header">EQUITY CURVE</div>', unsafe_allow_html=True)
                df_s = pd.DataFrame(snaps)
                lc = "#00C805" if pnl >= 0 else "#FF3B3B"
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=df_s["timestamp"], y=df_s["portfolio_value"], mode="lines",
                    line=dict(color=lc, width=1.5),
                    fill="tozeroy", fillcolor=f"rgba({'0,200,5' if pnl>=0 else '255,59,59'},0.05)",
                ))
                fig.add_hline(y=vc, line_dash="dot", line_color="#333333", line_width=1)
                fig.update_layout(**_plotly_cfg(260))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No equity history — run the agent to start")

            # Recent signals
            st.markdown('<div class="bb-header">RECENT SIGNALS</div>', unsafe_allow_html=True)
            sigs = _get_recent_signals(limit=6)
            if sigs:
                sig_rows = []
                for s in sigs:
                    sig_rows.append({
                        "Symbol": s.get("symbol", ""),
                        "Action": s.get("action", ""),
                        "Conf": f"{s.get('confidence', 0):.0%}",
                        "Entry": f"Rs.{(s.get('entry_price') or 0):,.0f}",
                        "Time": (s.get("timestamp") or "")[:16],
                    })
                st.dataframe(pd.DataFrame(sig_rows), use_container_width=True, hide_index=True, height=220)
            else:
                st.info("No signals yet")

        with right:
            # ── Market Intel (PCR / FII / Regime) ─────────────────────────
            st.markdown('<div class="bb-header">MARKET INTEL</div>', unsafe_allow_html=True)
            _pcr_d   = _load_json("logs/pcr_signal.json")
            _fii_d   = _load_json("logs/fii_signal.json")
            _pcr_sig = _pcr_d.get("signal", "neutral") if _pcr_d else "neutral"
            _fii_sig = _fii_d.get("signal", "neutral") if _fii_d else "neutral"
            _rg_now  = reg.get("regime", "unknown") if reg else "unknown"
            _pcr_badge = ("badge-bull" if _pcr_sig == "bullish" else
                          "badge-bear" if _pcr_sig == "bearish" else "badge-neu")
            _fii_badge = ("badge-bull" if _fii_sig == "bullish" else
                          "badge-bear" if _fii_sig == "bearish" else "badge-neu")
            _rg_badge  = ("badge-bull" if _rg_now == "bull" else
                          "badge-bear" if _rg_now == "bear" else "badge-side")
            _pcr_num   = _pcr_d.get("pcr", 0) if _pcr_d else 0
            _fii_num   = _fii_d.get("fii_net", 0) if _fii_d else 0
            _dii_num   = _fii_d.get("dii_net", 0) if _fii_d else 0
            st.markdown(f"""
            <div class="intel-card">
              <div class="intel-row">
                <span class="intel-key">Regime</span>
                <span class="badge {_rg_badge}">{_rg_now.upper()}</span>
              </div>
              <div class="intel-row">
                <span class="intel-key">PCR {_pcr_num:.2f}</span>
                <span class="badge {_pcr_badge}">{_pcr_sig.upper()}</span>
              </div>
              <div class="intel-row">
                <span class="intel-key">FII {'+' if _fii_num>=0 else ''}{_fii_num:,.0f}Cr</span>
                <span class="badge {_fii_badge}">{_fii_sig.upper()}</span>
              </div>
              <div class="intel-row">
                <span class="intel-key">DII {'+' if _dii_num>=0 else ''}{_dii_num:,.0f}Cr</span>
                <span style="color:{'#00C805' if _dii_num>=0 else '#FF3B3B'};font-size:11px;">
                    {'▲ BUY' if _dii_num>=0 else '▼ SELL'}</span>
              </div>
              <div class="intel-row">
                <span class="intel-key">Nifty RSI</span>
                <span style="color:{'#FF3B3B' if rsi_val>70 else '#00C805' if rsi_val<35 else '#FF6B00'};
                             font-weight:600;font-size:11px;">{rsi_val:.1f}</span>
              </div>
              <div class="intel-row">
                <span class="intel-key">BankNifty</span>
                <span style="color:{'#00C805' if bn_ret>=0 else '#FF3B3B'};font-size:11px;">
                    {bn_ret:+.2f}%</span>
              </div>
            </div>
            """, unsafe_allow_html=True)

            # Performance block
            st.markdown('<div class="bb-header">PERFORMANCE</div>', unsafe_allow_html=True)
            rows_html = ""
            for label, val, good in [
                ("Win Rate",     f"{stats['win_rate_pct']:.1f}%",     stats['win_rate_pct'] >= 52),
                ("Profit Factor",f"{stats['profit_factor']:.2f}",     stats['profit_factor'] >= 1.2),
                ("Avg Win",      f"Rs.{stats['avg_win']:,.0f}",       True),
                ("Avg Loss",     f"Rs.{stats['avg_loss']:,.0f}",      False),
                ("Max Drawdown", f"{stats['max_drawdown_pct']:.1f}%", stats['max_drawdown_pct'] <= 10),
                ("Total Trades", str(stats['total_trades']),           True),
            ]:
                c = "#00C805" if good else "#FF3B3B"
                rows_html += _bb_row(label, val, c)
            st.markdown(rows_html, unsafe_allow_html=True)

            st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)

            # Run agent
            st.markdown('<div class="bb-header">AGENT CONTROL</div>', unsafe_allow_html=True)
            dry = (agent_mode == "copilot")
            if st.button("RUN AGENT NOW", type="primary", use_container_width=True):
                with st.spinner("Running pipeline..."):
                    from main import run_agent
                    sigs = run_agent(dry_run=dry)
                    st.success(f"Done — {len(sigs)} signals")
                    time.sleep(1); st.rerun()
            if st.button("REFRESH PAGE", use_container_width=True):
                st.cache_data.clear()
                st.rerun()
            st.markdown(f"""
            <div style="font-size:10px;color:#444;margin-top:6px;">
                Mode: <span style="color:#FF6B00">{agent_mode.upper()}</span>
                &nbsp;|&nbsp; {'Dry run (no orders)' if dry else 'LIVE orders'}
            </div>
            """, unsafe_allow_html=True)

    # ── Signals ───────────────────────────────────────────────────────────────
    with tab_sig:
        sc1, sc2, sc3, sc4 = st.columns([2,1,1,1])
        auto = sc1.toggle("AUTO-REFRESH 60s", value=False)
        if sc2.button("RUN SCAN", type="primary"):
            with st.spinner("Scanning NSE 500..."):
                from main import run_agent
                run_agent(dry_run=True)
                st.rerun()
        if sc3.button("REFRESH"):
            st.rerun()
        show_signal_charts = sc4.toggle("LOAD CHARTS", value=False)

        all_sigs  = _get_recent_signals(limit=100)
        today_str = datetime.now().strftime("%Y-%m-%d")
        buy_sigs  = [s for s in all_sigs if s["action"]=="BUY" and s["timestamp"].startswith(today_str)]
        if not buy_sigs:
            buy_sigs = [s for s in all_sigs if s["action"]=="BUY"][:15]

        if buy_sigs:
            top_n = int(_cfg("TOP_N_SIGNALS", 10))
            display_count = min(top_n, 5 if not show_signal_charts else top_n)
            k1,k2,k3,k4 = st.columns(4)
            k1.metric("BUY SIGNALS", len(buy_sigs))
            k2.metric("AVG CONF",    f"{sum(s['confidence'] for s in buy_sigs)/len(buy_sigs):.0%}")
            k3.metric("AVG TA",      f"{sum(s['ta_score'] for s in buy_sigs)/len(buy_sigs):.1f}/10")
            k4.metric("POS SENT",    sum(1 for s in buy_sigs if s.get("sentiment")=="positive"))
            st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)

            for sig in buy_sigs[:display_count]:
                ep  = sig.get("entry_price") or 0
                sl  = sig.get("stop_loss")   or 0
                tp  = sig.get("take_profit") or 0
                qty = sig.get("position_size") or 0
                sl_pct = f"{(sl-ep)/ep*100:.1f}%" if ep else "--"
                tp_pct = f"{(tp-ep)/ep*100:.1f}%" if ep else "--"
                conf_w = int(sig["confidence"]*100)
                ta_w   = int(sig["ta_score"]/10*100)
                story  = _story(sig)
                sent_c = "#00C805" if sig.get("sentiment")=="positive" else (
                         "#FF3B3B" if sig.get("sentiment")=="negative" else "#888888")

                st.markdown(f"""
                <div class="sig-card">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                        <div style="display:flex;align-items:center;gap:10px;">
                            <span class="sig-sym">{sig['symbol']}</span>
                            <span class="sig-buy">BUY</span>
                            <span style="font-size:10px;color:{sent_c};">{sig.get("sentiment","").upper()}</span>
                        </div>
                        <span style="font-size:10px;color:#444;">{sig['timestamp'][:16]}</span>
                    </div>
                    <div class="sig-grid">
                        <div><div class="sig-cell-lbl">Entry</div>
                             <div class="sig-cell-val">Rs.{ep:,.2f}</div></div>
                        <div><div class="sig-cell-lbl">Stop Loss</div>
                             <div class="sig-cell-val" style="color:#FF3B3B;">
                                 Rs.{sl:,.2f} <span style="color:#555;font-size:10px;">{sl_pct}</span></div></div>
                        <div><div class="sig-cell-lbl">Take Profit</div>
                             <div class="sig-cell-val" style="color:#00C805;">
                                 Rs.{tp:,.2f} <span style="color:#555;font-size:10px;">+{tp_pct}</span></div></div>
                        <div><div class="sig-cell-lbl">Qty</div>
                             <div class="sig-cell-val">{qty} shares</div></div>
                    </div>
                    <div style="display:flex;gap:12px;margin-bottom:6px;">
                        <div style="flex:1;">
                            <div style="font-size:9px;color:#555;margin-bottom:2px;">
                                CONF {sig['confidence']:.0%}</div>
                            <div class="sig-bar-wrap">
                                <div class="sig-bar-fill" style="width:{conf_w}%;
                                     background:{'#00C805' if sig['confidence']>=0.6 else '#FF3B3B'};"></div>
                            </div>
                        </div>
                        <div style="flex:1;">
                            <div style="font-size:9px;color:#555;margin-bottom:2px;">
                                TA {sig['ta_score']:.1f}/10</div>
                            <div class="sig-bar-wrap">
                                <div class="sig-bar-fill" style="width:{ta_w}%;
                                     background:{'#FF6B00' if sig['ta_score']>=6 else '#888'};"></div>
                            </div>
                        </div>
                    </div>
                    {"<div class='sig-story'>" + story + "</div>" if story else ""}
                </div>
                """, unsafe_allow_html=True)

                if agent_mode == "copilot":
                    ca, cr = st.columns([1,1])
                    if ca.button(f"APPROVE {sig['symbol']}", key=f"ap_{sig['symbol']}"):
                        st.success(f"Order queued: BUY {sig['symbol']} x{qty} @ Rs.{ep:,.2f}")
                    if cr.button(f"SKIP", key=f"sk_{sig['symbol']}"):
                        st.info(f"{sig['symbol']} skipped")

                if show_signal_charts:
                    with st.expander(f"CHART  {sig['symbol']}", expanded=False):
                        _chart(sig["symbol"])
            if not show_signal_charts and len(buy_sigs) > display_count:
                st.caption(f"Showing top {display_count} signals. Enable LOAD CHARTS to inspect the full set with charts.")
        else:
            st.markdown("""
            <div style="text-align:center;padding:40px;color:#444;font-size:12px;">
                NO BUY SIGNALS TODAY<br>
                <span style="font-size:10px;color:#333;">Run the agent to generate signals.</span>
            </div>
            """, unsafe_allow_html=True)

        if auto:
            time.sleep(60); st.rerun()

    with tab_live:
        st.markdown('<div class="bb-header">LIVE WATCHLIST</div>', unsafe_allow_html=True)
        live_signals = _get_recent_signals(limit=20)
        watch_df = _live_watchlist(live_signals, pos, limit=8)
        if watch_df.empty:
            st.info("No watchlist symbols yet. Run the agent or open positions to populate this panel.")
        else:
            st.dataframe(watch_df, use_container_width=True, hide_index=True, height=320)

        desk_left, desk_right = st.columns(2)
        with desk_left:
            st.markdown('<div class="bb-header" style="margin-top:10px;">OPEN POSITIONS SNAPSHOT</div>',
                        unsafe_allow_html=True)
            if pos:
                pos_rows = []
                for sym, p in list(pos.items())[:6]:
                    quote = _quote_snapshot(sym)
                    curr = quote.get("price") if quote.get("price") is not None else p["entry"]
                    pnl_now = (curr - p["entry"]) * p["qty"]
                    pos_rows.append({
                        "Symbol": sym,
                        "Entry": round(p["entry"], 2),
                        "Now": round(curr, 2),
                        "Qty": p["qty"],
                        "P&L": round(pnl_now, 2),
                    })
                st.dataframe(pd.DataFrame(pos_rows), use_container_width=True, hide_index=True, height=220)
            else:
                st.info("No open NSE positions.")
        with desk_right:
            st.markdown('<div class="bb-header" style="margin-top:10px;">MARKET PULSE</div>',
                        unsafe_allow_html=True)
            pulse_rows = pd.DataFrame([
                {"Metric": "Nifty 1M", "Value": f"{ret_1m:+.2f}%"},
                {"Metric": "BankNifty 1D", "Value": f"{bn_ret:+.2f}%"},
                {"Metric": "PCR", "Value": f"{pcr_val:.2f}"},
                {"Metric": "FII Flow", "Value": f"{fii_net:+,.0f} Cr"},
                {"Metric": "Regime", "Value": rg_str},
                {"Metric": "Open Positions", "Value": _total_open},
            ])
            st.dataframe(pulse_rows, use_container_width=True, hide_index=True, height=220)

    with tab_alerts:
        st.markdown('<div class="bb-header">ACTIVITY FEED</div>', unsafe_allow_html=True)
        feed = _activity_feed(limit=16)
        if feed.empty:
            st.info("No recent activity yet.")
        else:
            st.dataframe(feed, use_container_width=True, hide_index=True, height=360)

        alert_left, alert_right = st.columns(2)
        with alert_left:
            st.markdown('<div class="bb-header" style="margin-top:10px;">SCHEDULER JOBS</div>',
                        unsafe_allow_html=True)
            jobs = (_load_scheduler_status().get("jobs") or {})
            job_rows = []
            for name, payload in jobs.items():
                job_rows.append({
                    "Job": name.replace("_", " ").upper(),
                    "State": payload.get("state", "").upper(),
                    "Updated": payload.get("timestamp", ""),
                })
            if job_rows:
                st.dataframe(pd.DataFrame(job_rows), use_container_width=True, hide_index=True, height=220)
            else:
                st.info("No scheduler job activity yet.")
        with alert_right:
            st.markdown('<div class="bb-header" style="margin-top:10px;">LATEST OUTCOMES</div>',
                        unsafe_allow_html=True)
            try:
                from analysis.outcome_tracker import OutcomeTracker
                outcome_rows = OutcomeTracker.get_recent_outcomes(limit=8)
            except Exception:
                outcome_rows = []
            if outcome_rows:
                latest_outcomes = pd.DataFrame(outcome_rows)[["symbol", "outcome", "confidence", "outcome_date"]]
                latest_outcomes.columns = ["Symbol", "Outcome", "Confidence", "Date"]
                latest_outcomes["Confidence"] = latest_outcomes["Confidence"].map(lambda v: f"{v:.0%}")
                st.dataframe(latest_outcomes, use_container_width=True, hide_index=True, height=220)
            else:
                st.info("No resolved outcomes yet.")

    with tab_health:
        st.markdown('<div class="bb-header">SYSTEM HEALTH</div>', unsafe_allow_html=True)
        _render_health_panel(_get_health_snapshot(), show_actions=False)
        with st.expander("RUNTIME NOTES", expanded=False):
            st.markdown(
                """
                - Scheduler status comes from `logs/scheduler.pid`.
                - Database health runs a lightweight SQLite ping.
                - Storage totals help keep the Oracle Free VM healthy over long runs.
                - If scans look stale, refresh this page first and then restart the scheduler if needed.
                """
            )

    # ── CRYPTO ────────────────────────────────────────────────────────────────
    with tab_crypto:
        st.markdown('<div class="bb-header">CRYPTO PAPER TRADING</div>',
                    unsafe_allow_html=True)
        c_stats, c_open, c_closed = _get_crypto_stats()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("OPEN",       c_stats.get("open_positions", 0))
        c2.metric("CLOSED",     c_stats.get("total", 0))
        c3.metric("WIN RATE",   f"{c_stats.get('win_rate', 0):.1f}%")
        c4.metric("TOTAL P&L",  f"{c_stats.get('total_pnl_usdt', 0):+.2f} USDT")

        if st.button("RUN CRYPTO SCAN NOW", type="primary"):
            with st.spinner("Scanning crypto markets..."):
                try:
                    from data.crypto_scanner import CryptoScanner
                    from analysis.technical_agent import TechnicalAgent
                    from config import MIN_CONFIDENCE
                    mdata = CryptoScanner().run(max_workers=10)
                    ta    = TechnicalAgent().analyse_all(mdata)
                    opened = 0
                    for sym, r in ta.items():
                        if r.tradeable and r.signal == "bullish" and r.confidence >= MIN_CONFIDENCE:
                            if cb.open_position(sym, "LONG", usd_amount=100,
                                                reasoning=r.reasoning[:100]):
                                opened += 1
                    st.success(f"Opened {opened} crypto paper positions")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

        if c_open:
            st.markdown('<div class="bb-header" style="margin-top:10px;">OPEN POSITIONS</div>',
                        unsafe_allow_html=True)
            for p in c_open:
                entry = p["entry_price"]; curr = p["current_price"] or entry
                pnl   = p["pnl_usdt"] or 0
                chg   = (curr - entry) / entry * 100 if entry else 0
                color = "#00C805" if pnl >= 0 else "#FF3B3B"
                st.markdown(f"""
                <div style="background:#111;border:1px solid #1e1e1e;
                            border-left:3px solid {'#00C805' if p['direction']=='LONG' else '#FF3B3B'};
                            padding:10px 14px;margin-bottom:6px;font-family:JetBrains Mono;font-size:11px;">
                  <div style="display:flex;gap:14px;align-items:center;">
                    <span style="font-weight:700;color:#eee;">{p['symbol']}</span>
                    <span style="color:{'#00C805' if p['direction']=='LONG' else '#FF3B3B'};">
                      {p['direction']}</span>
                    <span style="color:#555;">Entry {entry:.4f}</span>
                    <span style="color:#888;">Now {curr:.4f}</span>
                    <span style="color:{color};font-weight:700;margin-left:auto;">
                      {pnl:+.2f} USDT ({chg:+.1f}%)</span>
                  </div>
                </div>
                """, unsafe_allow_html=True)

        if c_closed:
            st.markdown('<div class="bb-header" style="margin-top:10px;">CLOSED TRADES</div>',
                        unsafe_allow_html=True)
            df_cc = pd.DataFrame(c_closed)[
                ["symbol","direction","entry_price","exit_price","pnl_usdt","pnl_pct","exit_reason"]
            ]
            st.dataframe(df_cc, use_container_width=True, height=220)

    # ── US STOCKS ─────────────────────────────────────────────────────────────
    with tab_us:
        st.markdown('<div class="bb-header">US STOCKS PAPER TRADING</div>',
                    unsafe_allow_html=True)
        u_stats, u_open, u_closed = _get_us_stats()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("OPEN",       u_stats.get("open_positions", 0))
        c2.metric("CLOSED",     u_stats.get("total", 0))
        c3.metric("WIN RATE",   f"{u_stats.get('win_rate', 0):.1f}%")
        c4.metric("TOTAL P&L",  f"${u_stats.get('total_pnl_usd', 0):+.2f}")

        st.caption("US market scan runs automatically at 7:00 PM IST (US market open, Mon-Fri)")

        if st.button("RUN US SCAN NOW", type="primary"):
            with st.spinner("Scanning US stocks..."):
                try:
                    from data.us_scanner import USScanner
                    from analysis.technical_agent import TechnicalAgent
                    from config import MIN_CONFIDENCE
                    mdata = USScanner().run(max_workers=15)
                    ta    = TechnicalAgent().analyse_all(mdata)
                    opened = 0
                    for sym, r in ta.items():
                        if r.tradeable and r.signal == "bullish" and r.confidence >= MIN_CONFIDENCE:
                            if ub.open_position(sym, "LONG", usd_amount=500,
                                                reasoning=r.reasoning[:100]):
                                opened += 1
                    st.success(f"Opened {opened} US paper positions")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

        if u_open:
            st.markdown('<div class="bb-header" style="margin-top:10px;">OPEN POSITIONS</div>',
                        unsafe_allow_html=True)
            for p in u_open:
                entry = p["entry_price"]; curr = p["current_price"] or entry
                pnl   = p["pnl_usd"] or 0
                chg   = (curr - entry) / entry * 100 if entry else 0
                color = "#00C805" if pnl >= 0 else "#FF3B3B"
                st.markdown(f"""
                <div style="background:#111;border:1px solid #1e1e1e;
                            border-left:3px solid #00C805;
                            padding:10px 14px;margin-bottom:6px;
                            font-family:JetBrains Mono;font-size:11px;">
                  <div style="display:flex;gap:14px;align-items:center;">
                    <span style="font-weight:700;color:#eee;">{p['symbol']}</span>
                    <span style="color:#00C805;">{p['direction']}</span>
                    <span style="color:#555;">Entry ${entry:.2f}</span>
                    <span style="color:#888;">Now ${curr:.2f}</span>
                    <span style="color:{color};font-weight:700;margin-left:auto;">
                      ${pnl:+.2f} ({chg:+.1f}%)</span>
                  </div>
                </div>
                """, unsafe_allow_html=True)

        if u_closed:
            st.markdown('<div class="bb-header" style="margin-top:10px;">CLOSED TRADES</div>',
                        unsafe_allow_html=True)
            df_uc = pd.DataFrame(u_closed)[
                ["symbol","direction","entry_price","exit_price","pnl_usd","pnl_pct","exit_reason"]
            ]
            st.dataframe(df_uc, use_container_width=True, height=220)

    # ── F&O BOOK ──────────────────────────────────────────────────────────────
    with tab_fno:
        st.markdown('<div class="bb-header">F&O PAPER TRADING BOOK</div>',
                    unsafe_allow_html=True)
        fno_stats, open_pos, closed_pos = _get_fno_stats()

        # KPIs
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("OPEN POSITIONS",  fno_stats.get("open_positions", 0))
        c2.metric("CLOSED TRADES",   fno_stats.get("total", 0))
        c3.metric("WIN RATE",        f"{fno_stats.get('win_rate', 0):.1f}%")
        c4.metric("TOTAL F&O P&L",   f"Rs.{fno_stats.get('total_pnl', 0):+,.0f}")

        # Action buttons
        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
        b1, b2, b3 = st.columns(3)

        if b1.button("BUY OPTIONS (CE/PE)", type="primary", use_container_width=True):
            with st.spinner("Generating signals..."):
                try:
                    from analysis.options_signals import OptionsSignalGenerator
                    opened = 0
                    for s in OptionsSignalGenerator().run():
                        if fno.open_position(index=s.index, direction=s.direction,
                                             strike=s.strike, expiry=s.expiry,
                                             lots=1, reasoning=s.reasoning):
                            opened += 1
                    st.success(f"Opened {opened} options position(s)")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

        if b2.button("SELL STRADDLE/STRANGLE", use_container_width=True):
            with st.spinner("Generating sell signals..."):
                try:
                    from analysis.options_selling import OptionsSellingGenerator
                    opened = 0
                    for s in OptionsSellingGenerator().run():
                        ce_id, pe_id = fno.open_selling_position(
                            index=s.index, ce_strike=s.ce_strike, pe_strike=s.pe_strike,
                            ce_premium=s.ce_premium, pe_premium=s.pe_premium,
                            expiry=s.expiry, lots=1, strategy=s.strategy,
                            reasoning=s.reasoning,
                        )
                        if ce_id:
                            opened += 2
                    st.success(f"Opened {opened} sell leg(s)" if opened
                               else "No sell signals today (runs Tue/Wed/Thu only)")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

        if b3.button("FUTURES LONG/SHORT", use_container_width=True):
            with st.spinner("Generating futures signals..."):
                try:
                    from analysis.futures_signals import FuturesSignalGenerator
                    opened = 0
                    for s in FuturesSignalGenerator().run():
                        if fno.open_futures(index=s.index, direction=s.direction,
                                            expiry=s.expiry, lots=1,
                                            reasoning=s.reasoning):
                            opened += 1
                    st.success(f"Opened {opened} futures position(s)")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

        # Open positions — all types
        st.markdown('<div class="bb-header" style="margin-top:12px;">OPEN POSITIONS</div>',
                    unsafe_allow_html=True)
        if not open_pos:
            st.info("No open F&O positions.")
        else:
            for p in open_pos:
                entry   = p["entry_premium"]
                curr    = p["current_premium"] or entry
                pnl_rs  = p["pnl"] or 0
                chg     = ((curr - entry) / entry * 100) if entry else 0
                opt     = p["option_type"]   # CE | PE | FUT-LONG | FUT-SHORT | SELL-CE-* | SELL-PE-*
                is_sell = opt.startswith("SELL")
                is_fut  = opt.startswith("FUT")

                if is_sell:
                    # Seller profit = premium decay, so invert display
                    pnl_rs = round((entry - curr) * p["qty"], 2)
                    chg    = (entry - curr) / entry * 100 if entry else 0
                    border = "#FFB347"
                    label  = f"SELL {opt.replace('SELL-','').split('-')[0]}"
                elif is_fut:
                    border = "#00BFFF"
                    label  = opt.replace("FUT-", "FUT ")
                elif opt == "CE":
                    border = "#00C805"; label = "BUY CE"
                else:
                    border = "#FF3B3B"; label = "BUY PE"

                color = "#00C805" if pnl_rs >= 0 else "#FF3B3B"
                strike_label = str(p["strike"]) if p["strike"] else "—"

                st.markdown(f"""
                <div style="background:#111;border:1px solid #1e1e1e;
                            border-left:3px solid {border};
                            padding:12px 16px;margin-bottom:8px;
                            font-family:JetBrains Mono;font-size:11px;">
                  <div style="display:flex;gap:16px;align-items:center;margin-bottom:6px;">
                    <span style="font-weight:700;color:#eee;font-size:13px;">
                      {p['instrument']} {strike_label}
                    </span>
                    <span style="color:{border};font-size:10px;">{label}</span>
                    <span style="color:#555;">Expiry {p['expiry']}</span>
                    <span style="color:#555;">{p['lots']} lot × {p['lot_size']}</span>
                    <span style="color:{color};font-weight:700;margin-left:auto;">
                      Rs.{pnl_rs:+,.0f} ({chg:+.1f}%)
                    </span>
                  </div>
                  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;color:#666;">
                    <div>Entry <span style="color:#ccc;">Rs.{entry:.1f}</span></div>
                    <div>Current <span style="color:{color};">Rs.{curr:.1f}</span></div>
                    <div>
                      {"SL@2x Rs."+f"{entry*2:.1f}" if is_sell else
                       "SL@50% Rs."+f"{entry*0.5:.1f}" if not is_fut else
                       "SL@-2% Rs."+f"{entry*0.98:.0f}"}
                    </div>
                  </div>
                </div>
                """, unsafe_allow_html=True)

        # Closed trades
        if closed_pos:
            st.markdown('<div class="bb-header" style="margin-top:12px;">CLOSED TRADES</div>',
                        unsafe_allow_html=True)
            df_cl = pd.DataFrame(closed_pos)[
                ["instrument","option_type","strike","expiry",
                 "lots","entry_premium","exit_premium","pnl","pnl_pct","exit_reason","exit_time"]
            ]
            df_cl.columns = ["Index","Type","Strike","Expiry",
                              "Lots","Entry","Exit","P&L","P&L%","Reason","Closed At"]
            st.dataframe(df_cl, use_container_width=True, height=280)

    _queue_live_refresh("TODAY")


# =============================================================================
# PAGE: PORTFOLIO
# =============================================================================
elif page == "PORTFOLIO":
    stats = _get_memory_stats()
    pf    = _load_pf()
    cash  = pf.get("cash", _cfg("VIRTUAL_CAPITAL", 1_000_000))
    vc    = _cfg("VIRTUAL_CAPITAL", 1_000_000)
    pnl   = cash - vc
    snaps = _get_equity_snapshots()
    positions = pf.get("positions", {})

    st.markdown('<div class="bb-header" style="font-size:12px;">PORTFOLIO</div>',
                unsafe_allow_html=True)

    # Combined P&L across all markets (all cached 60s)
    _pf_inr = _cfg("INR_PER_USD", 83.0)
    fno_s,  _, _ = _get_fno_stats()
    cry_s,  _, _ = _get_crypto_stats()
    us_s,   _, _ = _get_us_stats()
    fno_pnl     = fno_s.get("total_pnl", 0) or 0
    cry_pnl_inr = (cry_s.get("total_pnl_usdt", 0) or 0) * _pf_inr
    us_pnl_inr  = (us_s.get("total_pnl_usd",  0) or 0) * _pf_inr
    combined_pnl = pnl + fno_pnl + cry_pnl_inr + us_pnl_inr
    treasury = _treasury_snapshot(auto_sync=False)
    _render_treasury_warning(treasury)

    c1,c2,c3 = st.columns(3)
    c1.metric("NSE EQUITY",   f"Rs.{cash:,.0f}", delta=f"Rs.{pnl:+,.0f}")
    c2.metric("F&O P&L",      f"Rs.{fno_pnl:+,.0f}")
    c3.metric("CRYPTO P&L",   f"Rs.{cry_pnl_inr:+,.0f}")
    c4,c5,c6 = st.columns(3)
    c4.metric("US P&L",       f"Rs.{us_pnl_inr:+,.0f}")
    c5.metric("FREE CASH",    f"Rs.{float(treasury.get('available_cash_inr', cash) or cash):,.0f}")
    c6.metric("RESERVED",     f"Rs.{float(treasury.get('reserved_cash_inr', 0) or 0):,.0f}")
    c7,c8,c9 = st.columns(3)
    c7.metric("COMBINED P&L", f"Rs.{combined_pnl:+,.0f}")
    c8.metric("TOTAL EQUITY", f"Rs.{float(treasury.get('total_equity_inr', cash) or cash):,.0f}")
    c9.metric("WIN RATE",     f"{stats['win_rate_pct']:.1f}%")

    tab_eq, tab_pos = st.tabs(["EQUITY", "POSITIONS"])

    with tab_eq:
        l, r = st.columns([3, 1])
        with l:
            if snaps:
                df_s = pd.DataFrame(snaps)
                # Equity curve
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=df_s["timestamp"], y=df_s["portfolio_value"], mode="lines",
                    line=dict(color="#00C805" if pnl>=0 else "#FF3B3B", width=1.5),
                    fill="tozeroy",
                    fillcolor="rgba(0,200,5,0.05)" if pnl>=0 else "rgba(255,59,59,0.05)",
                ))
                fig.add_hline(y=vc, line_dash="dot", line_color="#333333", line_width=1)
                fig.update_layout(**_plotly_cfg(220))
                st.plotly_chart(fig, use_container_width=True)
                # Drawdown
                if len(df_s) > 1:
                    peak = df_s["portfolio_value"].cummax()
                    dd   = (df_s["portfolio_value"] - peak) / peak * 100
                    fig2 = go.Figure()
                    fig2.add_trace(go.Scatter(
                        x=df_s["timestamp"], y=dd, mode="lines",
                        line=dict(color="#FF3B3B", width=1),
                        fill="tozeroy", fillcolor="rgba(255,59,59,0.05)",
                    ))
                    fig2.update_layout(**_plotly_cfg(90))
                    st.caption("DRAWDOWN %")
                    st.plotly_chart(fig2, use_container_width=True)
            else:
                st.info("No equity data yet")
        with r:
            wins   = stats.get("wins", 0)
            losses = stats.get("losses", 0)
            if wins + losses > 0:
                fig_p = go.Figure(go.Pie(
                    values=[wins, losses], labels=["W","L"],
                    hole=0.65, marker_colors=["#00C805","#FF3B3B"], textinfo="none",
                ))
                fig_p.update_layout(
                    height=160, paper_bgcolor="#0a0a0a", showlegend=False,
                    margin=dict(l=0,r=0,t=0,b=0),
                    annotations=[dict(text=f"{stats['win_rate_pct']:.0f}%",
                                      font_size=18, showarrow=False,
                                      font_color="#eeeeee",
                                      font_family="JetBrains Mono")],
                )
                st.plotly_chart(fig_p, use_container_width=True)

            st.markdown('<div class="bb-header">STATS</div>', unsafe_allow_html=True)
            for lbl, v in [
                ("Trades", stats["total_trades"]),
                ("P&L",    f"Rs.{stats['total_pnl']:+,.0f}"),
                ("Avg Win",f"Rs.{stats['avg_win']:,.0f}"),
                ("Avg Loss",f"Rs.{stats['avg_loss']:,.0f}"),
            ]:
                st.markdown(_bb_row(lbl, str(v)), unsafe_allow_html=True)

    with tab_pos:
        pos_actions = st.columns([1, 1, 1])
        load_position_charts = pos_actions[0].toggle("POSITION CHARTS", value=False)
        if pos_actions[1].button("UPDATE PRICES + TRAILING STOPS", type="primary"):
            with st.spinner("Fetching live prices..."):
                from risk.trailing_stop import TrailingStopMonitor
                TrailingStopMonitor().run()
                st.rerun()
        compact_positions = pos_actions[2].toggle("COMPACT VIEW", value=True)

        if not positions:
            st.markdown("""
            <div style="text-align:center;padding:60px;color:#333;font-size:12px;">
                NO OPEN POSITIONS — AGENT IS FULLY IN CASH
            </div>
            """, unsafe_allow_html=True)
        else:
            live = {}
            total_invested, total_unr = 0, 0
            for sym, p in positions.items():
                curr = _live_price(sym) or p["entry"]
                live[sym] = curr
                total_invested += p["entry"] * p["qty"]
                total_unr += (curr - p["entry"]) * p["qty"]

            s1,s2,s3 = st.columns(3)
            s1.metric("POSITIONS",     len(positions))
            s2.metric("INVESTED",      f"Rs.{total_invested:,.0f}")
            s3.metric("UNREALISED P&L",f"Rs.{total_unr:+,.0f}",
                      delta=f"{total_unr/total_invested*100:+.1f}%" if total_invested else "0%")

            sorted_pos = sorted(positions.items(),
                key=lambda x: (live.get(x[0],x[1]["entry"]) - x[1]["entry"])*x[1]["qty"],
                reverse=True)

            position_rows = []
            for sym, p in sorted_pos:
                curr = live.get(sym, p["entry"])
                pnl_pos = (curr - p["entry"]) * p["qty"]
                pnl_pct = (curr - p["entry"]) / p["entry"] * 100
                sl_r = p["take_profit"] - p["stop_loss"]
                prog = max(0, min(100, (curr - p["stop_loss"]) / sl_r * 100)) if sl_r > 0 else 50
                position_rows.append({
                    "Symbol": sym,
                    "Entry": round(p["entry"], 2),
                    "Current": round(curr, 2),
                    "SL": round(p["stop_loss"], 2),
                    "TP": round(p["take_profit"], 2),
                    "Qty": p["qty"],
                    "P&L": round(pnl_pos, 2),
                    "P&L %": round(pnl_pct, 2),
                    "Progress %": round(prog, 0),
                })

            if compact_positions:
                st.dataframe(pd.DataFrame(position_rows), use_container_width=True, hide_index=True, height=320)
            else:
                # Header row
                st.markdown("""
                <div class="pos-row" style="color:#444;font-size:10px;text-transform:uppercase;
                                            border-bottom:1px solid #2a2a2a;padding:4px 12px;">
                    <span>Symbol</span><span>Entry / Current</span><span>SL / TP</span>
                    <span>Qty</span><span>Unrealised P&L</span><span>Progress</span>
                </div>
                """, unsafe_allow_html=True)
                for row in position_rows:
                    pc = "#00C805" if row["P&L"] > 0 else "#FF3B3B"
                    pc2 = "#00C805" if row["Progress %"] > 50 else "#FF3B3B"
                    st.markdown(f"""
                    <div class="pos-row">
                        <span style="color:#eeeeee;font-weight:600;">{row['Symbol']}</span>
                        <span>
                            <span style="color:#888;font-size:10px;">Rs.{row['Entry']:,.2f}</span><br>
                            <span style="color:{pc};">Rs.{row['Current']:,.2f}</span>
                        </span>
                        <span>
                            <span style="color:#FF3B3B;font-size:10px;">SL {row['SL']:,.0f}</span><br>
                            <span style="color:#00C805;font-size:10px;">TP {row['TP']:,.0f}</span>
                        </span>
                        <span style="color:#cccccc;">{row['Qty']}</span>
                        <span style="color:{pc};font-weight:600;">
                            Rs.{row['P&L']:+,.0f}<br>
                            <span style="font-size:10px;">{row['P&L %']:+.2f}%</span>
                        </span>
                        <span>
                            <div style="background:#1e1e1e;height:4px;width:60px;">
                                <div style="background:{pc2};height:4px;width:{row['Progress %']:.0f}%;"></div>
                            </div>
                            <span style="font-size:9px;color:#555;">{row['Progress %']:.0f}%</span>
                        </span>
                    </div>
                    """, unsafe_allow_html=True)

            if load_position_charts:
                for row in position_rows[: min(len(position_rows), 6)]:
                    with st.expander(f"  {row['Symbol']}", expanded=False):
                        _chart(row["Symbol"])

    _queue_live_refresh("PORTFOLIO")


# =============================================================================
# PAGE: RESEARCH
# =============================================================================
elif page == "RESEARCH":
    st.markdown('<div class="bb-header" style="font-size:12px;">RESEARCH</div>',
                unsafe_allow_html=True)

    tab_intel, tab_heat, tab_bt, tab_screen, tab_attrib, tab_opts = st.tabs([
        "MARKET INTEL", "SECTOR MAP", "BACKTEST", "SCREENER", "ATTRIBUTION", "OPTIONS"
    ])

    with tab_intel:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown('<div class="bb-header">NIFTY 50</div>', unsafe_allow_html=True)
            _chart("^NSEI", period="6mo", height=200)
        with c2:
            st.markdown('<div class="bb-header">BANK NIFTY</div>', unsafe_allow_html=True)
            _chart("^NSEBANK", period="6mo", height=200)

        st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)
        a, b, c = st.columns(3)

        with a:
            if st.button("SECTOR ROTATION", use_container_width=True, type="primary"):
                from analysis.sector_rotation import SectorRotationAnalyser
                r = SectorRotationAnalyser().analyse()
                if r.sector_returns:
                    df_s = pd.DataFrame(list(r.sector_returns.items()),
                                        columns=["Sector","Return %"]).sort_values("Return %", ascending=False)
                    fig = go.Figure(go.Scatterpolar(
                        r=df_s["Return %"].tolist() + [df_s["Return %"].iloc[0]],
                        theta=df_s["Sector"].tolist() + [df_s["Sector"].iloc[0]],
                        fill="toself",
                        fillcolor="rgba(255,107,0,0.08)",
                        line=dict(color="#FF6B00"),
                    ))
                    fig.update_layout(
                        polar=dict(bgcolor="#0d0d0d",
                                   radialaxis=dict(color="#444", gridcolor="#1e1e1e"),
                                   angularaxis=dict(color="#888", gridcolor="#1e1e1e")),
                        paper_bgcolor="#0a0a0a", height=300,
                        margin=dict(l=40,r=40,t=20,b=20),
                        font=dict(family="JetBrains Mono", color="#666", size=9),
                    )
                    st.plotly_chart(fig, use_container_width=True)

        with b:
            if st.button("PCR + FII/DII", use_container_width=True, type="primary"):
                from analysis.pcr_signal import PCRAnalyser
                from analysis.fii_dii import FIIDIIAnalyser
                pcr = PCRAnalyser().get_signal()
                fii = FIIDIIAnalyser().get_signal()
                st.metric("PCR", f"{pcr.pcr:.2f}", delta=pcr.signal.upper())
                st.caption(pcr.message)
                st.metric("FII FLOW", f"Rs.{fii.fii_net:+,.0f}Cr", delta=fii.signal.upper())
                st.caption(fii.message)

        with c:
            if st.button("IPO WATCH", use_container_width=True):
                try:
                    from analysis.ipo_alert import IPOAlertSystem
                    ipos = IPOAlertSystem().check()
                    for ipo in (ipos or [])[:5]:
                        c2 = "#00C805" if ipo.watchable else "#888"
                        st.markdown(f'<span style="color:{c2}">{ipo.symbol}</span> '
                                    f'{ipo.return_from_issue:+.1f}%', unsafe_allow_html=True)
                except Exception as e:
                    st.error(str(e))

    with tab_heat:
        st.markdown('<div class="bb-header">SECTOR HEATMAP</div>', unsafe_allow_html=True)
        if st.button("LOAD SECTOR DATA", type="primary"):
            st.rerun()
        try:
            from analysis.sector_rotation import SectorRotationAnalyser
            with st.spinner("Loading..."):
                r = SectorRotationAnalyser().analyse()
            if r.sector_returns:
                df_h = pd.DataFrame([
                    {"Sector": k, "Return %": v, "Size": max(abs(v)*8+4, 3)}
                    for k, v in r.sector_returns.items()
                ])
                df_h["Label"] = df_h.apply(
                    lambda row: f"{row['Sector']}<br>{row['Return %']:+.1f}%", axis=1)
                fig = go.Figure(go.Treemap(
                    labels=df_h["Label"].tolist(),
                    parents=[""] * len(df_h),
                    values=df_h["Size"].tolist(),
                    marker=dict(
                        colors=df_h["Return %"].tolist(),
                        colorscale=[[0,"rgba(255,59,59,0.85)"],[0.5,"rgba(20,20,20,0.9)"],
                                    [1,"rgba(0,200,5,0.85)"]],
                        showscale=True,
                        colorbar=dict(
                            tickfont=dict(color="#666", size=9, family="JetBrains Mono"),
                            title=dict(text="Ret%", font=dict(color="#666", size=9)),
                            thickness=10,
                        ),
                    ),
                    textfont=dict(color="#eeeeee", size=11, family="JetBrains Mono"),
                    hovertemplate="<b>%{label}</b><extra></extra>",
                ))
                fig.update_layout(height=380, paper_bgcolor="#0a0a0a",
                                  margin=dict(l=0,r=0,t=0,b=0),
                                  font=dict(family="JetBrains Mono"))
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(
                    df_h[["Sector","Return %"]].sort_values("Return %", ascending=False),
                    use_container_width=True, height=250,
                )
        except Exception as e:
            st.info(f"Run a scan first to populate sector data. ({e})")

    with tab_bt:
        b1,b2,b3 = st.columns(3)
        bt_sym  = b1.text_input("SYMBOL", "BRITANNIA")
        bt_yr   = b2.selectbox("YEARS", [1,2,3,5], index=2)
        bt_all  = b3.checkbox("TOP 10 STOCKS")

        c1b, c2b = st.columns(2)
        comm_pct = c1b.slider("COMMISSION %", 0.0, 0.5, 0.03, 0.01,
                               format="%.2f")
        slip_pct = c2b.slider("SLIPPAGE %",   0.0, 0.5, 0.05, 0.01,
                               format="%.2f")

        if st.button("RUN BACKTEST", type="primary"):
            syms = (["BRITANNIA","TITAN","BAJFINANCE","HDFCBANK","RELIANCE",
                     "ICICIBANK","SBIN","AXISBANK","INFY","TCS"]
                    if bt_all else [bt_sym.upper()])
            with st.spinner(f"Backtesting {len(syms)} stock(s)..."):
                try:
                    from backtest.engine import BacktestEngine
                    engine = BacktestEngine()
                    end   = datetime.today().strftime("%Y-%m-%d")
                    start = (datetime.today()-timedelta(days=365*bt_yr)).strftime("%Y-%m-%d")
                    for sym in syms:
                        engine.run(sym, start, end,
                                   commission_pct=comm_pct/100,
                                   slippage_pct=slip_pct/100)
                    st.success("Done!"); st.rerun()
                except Exception as e:
                    st.error(str(e))

        results_dir = "logs/backtest_results"
        if os.path.exists(results_dir):
            files = [f for f in os.listdir(results_dir) if f.endswith("_backtest.json")]
            if files:
                all_r = []
                for fname in sorted(files):
                    try:
                        with open(os.path.join(results_dir, fname)) as f:
                            all_r.append(json.load(f)["result"])
                    except Exception: pass
                if all_r:
                    btr1, btr2, btr3 = st.tabs(["RETURNS", "WIN RATE", "TABLE"])
                    df_r = pd.DataFrame(all_r).sort_values("total_return_pct", ascending=False)

                    with btr1:
                        fig = go.Figure(go.Bar(
                            x=df_r["symbol"], y=df_r["total_return_pct"],
                            marker_color=df_r["total_return_pct"].apply(
                                lambda v: "#00C805" if v>0 else "#FF3B3B").tolist(),
                            text=df_r["total_return_pct"].apply(lambda v: f"{v:.1f}%"),
                            textposition="outside",
                            textfont=dict(color="#888", size=10, family="JetBrains Mono"),
                        ))
                        fig.update_layout(**_plotly_cfg(260))
                        st.plotly_chart(fig, use_container_width=True)

                    with btr2:
                        fig = go.Figure(go.Bar(
                            x=df_r["symbol"], y=df_r["win_rate_pct"],
                            marker_color=df_r["win_rate_pct"].apply(
                                lambda v: "#00C805" if v>=52 else "#FF6B00").tolist(),
                            text=df_r["win_rate_pct"].apply(lambda v: f"{v:.1f}%"),
                            textposition="outside",
                            textfont=dict(color="#888", size=10),
                        ))
                        fig.add_hline(y=52, line_dash="dot", line_color="#333", line_width=1)
                        fig.update_layout(**_plotly_cfg(260))
                        st.plotly_chart(fig, use_container_width=True)

                    with btr3:
                        cols = ["symbol","total_return_pct","win_rate_pct",
                                "max_drawdown_pct","sharpe_ratio","profit_factor",
                                "total_trades","avg_hold_days"]
                        avail = [c for c in cols if c in df_r.columns]
                        st.dataframe(df_r[avail], use_container_width=True)

    with tab_screen:
        st.markdown('<div class="bb-header">STOCK SCREENER</div>', unsafe_allow_html=True)
        f1, f2, f3 = st.columns(3)
        filt_sector = f1.selectbox("SECTOR", ["All","IT","Banking","Pharma","Auto","Energy",
                                               "FMCG","Metals","Realty","Capital Goods"])
        filt_index  = f2.selectbox("INDEX",  ["All","NIFTY50","NIFTY100","NIFTY200","NIFTY500"])
        filt_min_ta = f3.slider("MIN TA SCORE", 0.0, 10.0, 5.0, 0.5)

        if st.button("SCREEN NOW", type="primary"):
            with st.spinner("Scanning..."):
                try:
                    df_nse = pd.read_csv("data/nse500_symbols.csv")
                    if filt_sector != "All":
                        df_nse = df_nse[df_nse["sector"] == filt_sector]
                    if filt_index != "All":
                        df_nse = df_nse[df_nse["index_membership"].str.contains(filt_index, na=False)]
                    st.markdown(f"""<div style="font-size:11px;color:#888;margin-bottom:8px;">
                        {len(df_nse)} stocks match filters</div>""", unsafe_allow_html=True)
                    st.dataframe(df_nse[["symbol","name","sector","market_cap_rank","index_membership"]],
                                 use_container_width=True, height=400)
                except Exception as e:
                    st.error(str(e))
        else:
            try:
                df_nse = pd.read_csv("data/nse500_symbols.csv")
                if filt_sector != "All":
                    df_nse = df_nse[df_nse["sector"] == filt_sector]
                if filt_index != "All":
                    df_nse = df_nse[df_nse["index_membership"].str.contains(filt_index, na=False)]
                st.dataframe(df_nse[["symbol","name","sector","market_cap_rank","index_membership"]],
                             use_container_width=True, height=400)
            except Exception: pass

    with tab_attrib:
        st.markdown('<div class="bb-header">INDICATOR PERFORMANCE ATTRIBUTION</div>',
                    unsafe_allow_html=True)
        st.caption("Which of the 8 TA indicators actually predict winning trades? "
                   "Based on resolved signals (TP_HIT vs SL_HIT) in the database.")

        try:
            from analysis.performance_attribution import PerformanceAttributor
            pa = PerformanceAttributor.get_cached()
        except Exception as e:
            pa = {}
            st.warning(f"Attribution error: {e}")

        if not pa or pa.get("total_resolved", 0) < 2:
            st.info("Attribution requires at least 2 resolved signals (TP_HIT or SL_HIT). "
                    "Check back after a few trading days.")
        else:
            total = pa.get("total_resolved", 0)
            wr    = pa.get("overall_win_rate", 0)
            c1, c2 = st.columns(2)
            c1.metric("RESOLVED SIGNALS", total)
            c2.metric("OVERALL WIN RATE", f"{wr:.1f}%")

            st.markdown('<div class="bb-header" style="margin-top:14px;">'
                        'INDICATOR WIN RATES</div>', unsafe_allow_html=True)

            ranked = pa.get("ranked", [])
            if ranked:
                # Bar chart
                import plotly.graph_objects as go
                labels = [r[0] for r in ranked]
                rates  = [r[1]["win_rate"] for r in ranked]
                colors = ["#00C805" if r >= 55 else
                          "#FFB347" if r >= 45 else
                          "#FF3B3B" for r in rates]
                fig = go.Figure(go.Bar(
                    x=rates, y=labels, orientation="h",
                    marker_color=colors,
                    text=[f"{r:.0f}%" for r in rates],
                    textposition="outside",
                    textfont=dict(family="JetBrains Mono", size=10, color="#cccccc"),
                ))
                fig.add_vline(x=50, line_dash="dash",
                              line_color="#444444", line_width=1)
                fig.add_vline(x=wr, line_dash="dot",
                              line_color="#FF6B00", line_width=1,
                              annotation_text="Overall",
                              annotation_font_color="#FF6B00",
                              annotation_font_size=9)
                fig.update_layout(**_plotly_cfg(280),
                                  xaxis=dict(range=[0, 100],
                                             ticksuffix="%",
                                             color="#555",
                                             gridcolor="#1a1a1a"),
                                  yaxis=dict(color="#888"),
                                  showlegend=False)
                st.plotly_chart(fig, use_container_width=True)

                # Detail table
                st.markdown('<div class="bb-header" style="margin-top:10px;">'
                            'DETAIL</div>', unsafe_allow_html=True)
                for name, stat in ranked:
                    wr_ind = stat["win_rate"]
                    color  = "#00C805" if wr_ind >= 55 else "#FFB347" if wr_ind >= 45 else "#FF3B3B"
                    edge   = wr_ind - 50
                    st.markdown(f"""
                    <div style="display:grid;grid-template-columns:110px 60px 50px 50px 80px 1fr;
                                gap:8px;padding:6px 0;border-bottom:1px solid #151515;
                                font-family:JetBrains Mono;font-size:11px;align-items:center;">
                      <span style="color:#cccccc;font-weight:600;">{name}</span>
                      <span style="color:{color};font-weight:700;">{wr_ind:.0f}%</span>
                      <span style="color:#555;">n={stat['signals']}</span>
                      <span style="color:#00C805;">{stat['wins']}W</span>
                      <span style="color:#FF3B3B;">{stat['losses']}L</span>
                      <span style="color:{'#00C805' if edge>=0 else '#FF3B3B'};font-size:10px;">
                        {'edge +' if edge>=0 else 'edge '}{edge:.1f}%</span>
                    </div>
                    """, unsafe_allow_html=True)

    with tab_opts:
        st.markdown('<div class="bb-header">NIFTY / BANKNIFTY OPTIONS SIGNALS</div>',
                    unsafe_allow_html=True)
        st.caption("Weekly CE/PE trade ideas based on index trend, IV environment, "
                   "and strike selection. For reference only — verify with live option chain.")

        if st.button("GENERATE OPTIONS SIGNALS", type="primary", use_container_width=False):
            with st.spinner("Analysing indices..."):
                try:
                    from analysis.options_signals import OptionsSignalGenerator
                    opt_signals = OptionsSignalGenerator().run()
                    st.session_state["opt_signals"] = opt_signals
                    st.session_state["opt_ts"] = datetime.now().strftime("%H:%M:%S")
                except Exception as e:
                    st.error(f"Options signal error: {e}")
                    st.session_state["opt_signals"] = []

        opt_signals = st.session_state.get("opt_signals", [])
        opt_ts      = st.session_state.get("opt_ts", "")

        if opt_ts:
            st.caption(f"Last updated: {opt_ts}")

        if not opt_signals:
            st.info("Click GENERATE OPTIONS SIGNALS to fetch Nifty & BankNifty data "
                    "and produce CE/PE trade ideas.")
        else:
            for sig in opt_signals:
                direction_color = "#00C805" if sig.direction == "CALL" else "#FF3B3B"
                direction_label = f"{'▲ CALL' if sig.direction == 'CALL' else '▼ PUT'}"
                rr_dist = abs(sig.target_idx - sig.index_spot)
                sl_dist = abs(sig.stop_loss_idx - sig.index_spot)
                rr_ratio = round(rr_dist / sl_dist, 1) if sl_dist > 0 else 0

                st.markdown(f"""
                <div style="background:#111111;border:1px solid #1e1e1e;
                            border-left:3px solid {direction_color};
                            padding:14px 18px;margin-bottom:12px;
                            font-family:JetBrains Mono;border-radius:0;">
                  <div style="display:flex;align-items:center;gap:14px;margin-bottom:8px;">
                    <span style="font-size:15px;font-weight:700;color:#eeeeee;">{sig.index}</span>
                    <span style="color:{direction_color};font-weight:700;font-size:14px;">
                      {direction_label}
                    </span>
                    <span style="color:#aaaaaa;font-size:13px;">Strike <b style="color:#eeeeee;">{sig.strike}</b></span>
                    <span style="color:#555;font-size:11px;">Expiry {sig.expiry}</span>
                    <span style="color:#FF6B00;font-size:11px;margin-left:auto;">
                      Conf {sig.confidence:.0%}
                    </span>
                  </div>
                  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;
                              font-size:11px;color:#888;margin-bottom:8px;">
                    <div><span style="color:#555;display:block;">SPOT</span>
                         <span style="color:#cccccc;">{sig.index_spot:,.0f}</span></div>
                    <div><span style="color:#555;display:block;">ENTRY ZONE</span>
                         <span style="color:#cccccc;">{sig.entry_zone}</span></div>
                    <div><span style="color:#555;display:block;">SL (INDEX)</span>
                         <span style="color:#FF3B3B;">{sig.stop_loss_idx:,.0f}</span></div>
                    <div><span style="color:#555;display:block;">TARGET (INDEX)</span>
                         <span style="color:#00C805;">{sig.target_idx:,.0f}</span></div>
                  </div>
                  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;font-size:11px;">
                    <div style="color:#888888;">{sig.iv_note}</div>
                    <div style="color:#555;text-align:right;">
                      Lot size {sig.lot_size} &nbsp;|&nbsp; R:R {rr_ratio}x
                    </div>
                  </div>
                  <div style="margin-top:8px;color:#555555;font-size:10px;
                              border-top:1px solid #1a1a1a;padding-top:6px;">
                    {sig.reasoning}
                  </div>
                </div>
                """, unsafe_allow_html=True)

            st.markdown("""
            <div style="background:#0d0d0d;border:1px solid #1a1a1a;padding:10px 14px;
                        font-size:10px;color:#555555;font-family:JetBrains Mono;margin-top:8px;">
              ⚠ DISCLAIMER: These are educational signals only. Options involve substantial
              risk. Entry zone is approximate — use live NSE option chain for actual premiums.
              Always trade with defined risk using stop-loss orders.
            </div>
            """, unsafe_allow_html=True)


# =============================================================================
# PAGE: HISTORY
# =============================================================================
elif page == "HISTORY":
    st.markdown('<div class="bb-header" style="font-size:12px;">HISTORY</div>',
                unsafe_allow_html=True)
    tab_tr, tab_sig, tab_qual, tab_rd = st.tabs(["TRADE LOG", "SIGNAL OUTCOMES", "SIGNAL QUALITY", "READINESS"])

    with tab_tr:
        df = _unified_trade_frame(limit=500)
        if df.empty:
            st.info("No closed trades yet — paper trades will appear here after SL/TP hits")
        else:
            closed = df[df["status"].str.lower() == "closed"].copy()

            if not closed.empty:
                c1,c2,c3,c4 = st.columns(4)
                c1.metric("TOTAL P&L",   f"Rs.{closed['pnl'].sum():+,.0f}")
                c2.metric("BEST TRADE",  f"Rs.{closed['pnl'].max():+,.0f}")
                c3.metric("WORST TRADE", f"Rs.{closed['pnl'].min():+,.0f}")
                c4.metric("AVG P&L",     f"Rs.{closed['pnl'].mean():+,.0f}")

                analytics = _trade_analytics(closed)
                a1, a2, a3, a4 = st.columns(4)
                a1.metric("EXPECTANCY", f"Rs.{analytics.get('expectancy', 0):+,.0f}")
                a2.metric("AVG HOLD", f"{analytics.get('avg_hold_hours', 0):.1f}h")
                a3.metric("AVG WIN", f"Rs.{analytics.get('avg_win', 0):+,.0f}")
                a4.metric("AVG LOSS", f"Rs.{analytics.get('avg_loss', 0):+,.0f}")

                fig = go.Figure()
                colors = closed["pnl"].apply(lambda x: "#00C805" if x>0 else "#FF3B3B").tolist()
                fig.add_trace(go.Bar(x=list(range(len(closed))), y=closed["pnl"],
                                     marker_color=colors, name="P&L"))
                fig.add_trace(go.Scatter(x=list(range(len(closed))), y=closed["pnl"].cumsum(),
                                         mode="lines", line=dict(color="#FF6B00", width=1.5),
                                         name="Cumulative", yaxis="y2"))
                fig.update_layout(**_plotly_cfg(220),
                                  showlegend=True,
                                  yaxis2=dict(overlaying="y", side="right",
                                              color="#FF6B00", gridcolor="rgba(0,0,0,0)"),
                                  legend=dict(orientation="h",
                                              font=dict(color="#666", size=9, family="JetBrains Mono")))
                st.plotly_chart(fig, use_container_width=True)

                t_left, t_right = st.columns(2)
                with t_left:
                    st.markdown('<div class="bb-header" style="margin-top:10px;">BEST DAYS</div>',
                                unsafe_allow_html=True)
                    st.dataframe(analytics.get("weekday", pd.DataFrame()), use_container_width=True,
                                 hide_index=True, height=240)
                with t_right:
                    st.markdown('<div class="bb-header" style="margin-top:10px;">BEST SYMBOLS</div>',
                                unsafe_allow_html=True)
                    st.dataframe(analytics.get("symbols", pd.DataFrame()), use_container_width=True,
                                 hide_index=True, height=240)

                p_left, p_right = st.columns(2)
                with p_left:
                    st.markdown('<div class="bb-header" style="margin-top:10px;">HOLDING PERIOD EDGE</div>',
                                unsafe_allow_html=True)
                    st.dataframe(analytics.get("hold_buckets", pd.DataFrame()), use_container_width=True,
                                 hide_index=True, height=220)
                with p_right:
                    st.markdown('<div class="bb-header" style="margin-top:10px;">ENTRY TIME EDGE</div>',
                                unsafe_allow_html=True)
                    st.dataframe(analytics.get("entry_hours", pd.DataFrame()), use_container_width=True,
                                 hide_index=True, height=220)

            f1, f2 = st.columns(2)
            sf    = f1.selectbox("FILTER STATUS", ["All","open","closed"])
            sym_f = f2.text_input("FILTER SYMBOL", "")
            filtered = df.copy()
            if sf != "All":
                filtered = filtered[filtered["status"].str.lower() == sf.lower()]
            if sym_f:
                filtered = filtered[filtered["symbol"].str.upper().str.contains(sym_f.upper(), na=False)]
            st.dataframe(filtered, use_container_width=True, height=360)

    with tab_sig:
        # Signal Outcome Tracker — strategy accuracy panel
        try:
            from analysis.outcome_tracker import OutcomeTracker
            ot_stats   = OutcomeTracker.get_stats()
            ot_records = OutcomeTracker.get_recent_outcomes(limit=100)
        except Exception:
            ot_stats   = {}
            ot_records = []

        if ot_stats and ot_stats.get("total_resolved", 0) > 0:
            s = ot_stats
            c1,c2,c3,c4,c5 = st.columns(5)
            c1.metric("RESOLVED",    s.get("total_resolved", 0))
            tp_col = "#00C805"
            sl_col = "#FF3B3B"
            c2.metric("TP HIT",      s.get("tp_count", 0),
                      delta=f"{s.get('tp_rate_pct',0):.0f}%")
            c3.metric("SL HIT",      s.get("sl_count", 0),
                      delta=f"-{s.get('sl_rate_pct',0):.0f}%")
            c4.metric("AVG DAYS TP", f"{s.get('avg_days_tp',0):.0f}d")
            c5.metric("AVG DAYS SL", f"{s.get('avg_days_sl',0):.0f}d")

            # TP vs SL donut
            if s.get("tp_count", 0) + s.get("sl_count", 0) > 0:
                fig_d = go.Figure(go.Pie(
                    labels=["TP HIT","SL HIT","EXPIRED"],
                    values=[s.get("tp_count",0), s.get("sl_count",0), s.get("expired_count",0)],
                    hole=0.65,
                    marker_colors=[tp_col, sl_col, "#555555"],
                    textfont=dict(family="JetBrains Mono", size=10),
                ))
                fig_d.update_layout(**_plotly_cfg(200))
                st.plotly_chart(fig_d, use_container_width=True)
        else:
            st.info("Signal outcomes will appear here after the market closes today. "
                    "The tracker runs at 3:30 PM IST every weekday.")
            if st.button("RUN OUTCOME CHECK NOW", type="primary"):
                with st.spinner("Checking signal outcomes..."):
                    try:
                        from analysis.outcome_tracker import OutcomeTracker
                        result = OutcomeTracker().run()
                        st.success(f"Done — TP: {result['tp_hit']}  "
                                   f"SL: {result['sl_hit']}  "
                                   f"Expired: {result['expired']}  "
                                   f"Still open: {result['still_open']}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

        if ot_records:
            st.markdown('<div class="bb-header" style="font-size:11px;margin-top:12px;">'
                        'RECENT SIGNAL OUTCOMES</div>', unsafe_allow_html=True)
            outcome_color = {"TP_HIT": "#00C805", "SL_HIT": "#FF3B3B",
                             "EXPIRED": "#888888", "OPEN": "#FF6B00"}
            for r in ot_records[:30]:
                oc    = r.get("outcome", "OPEN")
                color = outcome_color.get(oc, "#888")
                ep    = r.get("entry_price", 0) or 0
                op    = r.get("outcome_price", ep) or ep
                pnl_p = round((op - ep) / ep * 100, 1) if ep else 0
                days  = r.get("days_to_outcome") or "—"
                conf  = r.get("confidence", 0) or 0
                st.markdown(f"""
                <div style="display:grid;grid-template-columns:90px 70px 60px 70px 70px 60px 1fr;
                            gap:8px;padding:6px 0;border-bottom:1px solid #1e1e1e;
                            font-family:JetBrains Mono;font-size:11px;align-items:center;">
                  <span style="color:#cccccc;">{r.get('symbol','')}</span>
                  <span style="color:{color};font-weight:600;">{oc}</span>
                  <span style="color:{color};">{pnl_p:+.1f}%</span>
                  <span style="color:#888;">Entry {ep:,.0f}</span>
                  <span style="color:#888;">Exit {op:,.0f}</span>
                  <span style="color:#666;">{days}d</span>
                  <span style="color:#555;font-size:10px;">{r.get('outcome_date','')[:10]}</span>
                </div>
                """, unsafe_allow_html=True)

    with tab_qual:
        sig_df = _unified_signal_frame(limit=500)
        if sig_df.empty:
            st.info("No signal history yet.")
        else:
            qa = _signal_analytics(sig_df)
            time_qa = _signal_time_analytics(sig_df)

            q1, q2, q3, q4, q5 = st.columns(5)
            q1.metric("SIGNALS", qa.get("total_signals", 0))
            q2.metric("EXECUTED", qa.get("executed_signals", 0))
            q3.metric("EXEC RATE", f"{qa.get('execution_rate', 0):.1f}%")
            q4.metric("AVG CONF", f"{qa.get('avg_confidence', 0):.1f}%")
            q5.metric("AVG TA", f"{qa.get('avg_ta_score', 0):.2f}")

            c_left, c_right = st.columns(2)
            with c_left:
                st.markdown('<div class="bb-header" style="margin-top:10px;">CONFIDENCE BUCKETS</div>',
                            unsafe_allow_html=True)
                st.dataframe(qa.get("confidence_table", pd.DataFrame()), use_container_width=True,
                             hide_index=True, height=220)
            with c_right:
                st.markdown('<div class="bb-header" style="margin-top:10px;">TA BUCKETS</div>',
                            unsafe_allow_html=True)
                st.dataframe(qa.get("ta_table", pd.DataFrame()), use_container_width=True,
                             hide_index=True, height=220)

            st.markdown('<div class="bb-header" style="margin-top:10px;">ACTION MIX</div>',
                        unsafe_allow_html=True)
            st.dataframe(qa.get("action_table", pd.DataFrame()), use_container_width=True,
                         hide_index=True, height=180)

            daily_signal_trend = time_qa.get("daily", pd.DataFrame())
            if not daily_signal_trend.empty:
                st.markdown('<div class="bb-header" style="margin-top:10px;">RECENT SIGNAL FLOW</div>',
                            unsafe_allow_html=True)
                trend_fig = go.Figure()
                trend_fig.add_trace(go.Bar(
                    x=daily_signal_trend["day"],
                    y=daily_signal_trend["Signals"],
                    marker_color="#444444",
                    name="Signals",
                ))
                trend_fig.add_trace(go.Scatter(
                    x=daily_signal_trend["day"],
                    y=daily_signal_trend["Exec %"],
                    mode="lines+markers",
                    line=dict(color="#FF6B00", width=2),
                    marker=dict(size=6),
                    name="Exec %",
                    yaxis="y2",
                ))
                trend_fig.update_layout(
                    **_plotly_cfg(260, showlegend=True),
                    yaxis=dict(title="Signals", color="#666666", gridcolor="#1a1a1a"),
                    yaxis2=dict(
                        title="Exec %",
                        overlaying="y",
                        side="right",
                        color="#FF6B00",
                        gridcolor="rgba(0,0,0,0)",
                    ),
                    legend=dict(orientation="h", font=dict(color="#666", size=9, family="JetBrains Mono")),
                )
                st.plotly_chart(trend_fig, use_container_width=True)

            try:
                from analysis.outcome_tracker import OutcomeTracker
                outcome_rows = OutcomeTracker.get_recent_outcomes(limit=300)
            except Exception:
                outcome_rows = []

            if outcome_rows:
                outcome_df = pd.DataFrame(outcome_rows)
                ob = _outcome_bucket_analytics(outcome_df)

                st.markdown('<div class="bb-header" style="margin-top:14px;">RESOLVED SIGNAL EDGE</div>',
                            unsafe_allow_html=True)
                o1, o2, o3, o4 = st.columns(4)
                o1.metric("RESOLVED", ob.get("resolved", 0))
                o2.metric("TP RATE", f"{ob.get('tp_rate', 0):.1f}%")
                o3.metric("AVG CONF", f"{ob.get('avg_confidence', 0):.1f}%")
                o4.metric("AVG TA", f"{ob.get('avg_ta_score', 0):.2f}")

                ob_left, ob_right = st.columns(2)
                with ob_left:
                    st.markdown('<div class="bb-header" style="margin-top:10px;">CONFIDENCE VS OUTCOME</div>',
                                unsafe_allow_html=True)
                    st.dataframe(ob.get("confidence_outcomes", pd.DataFrame()), use_container_width=True,
                                 hide_index=True, height=220)
                with ob_right:
                    st.markdown('<div class="bb-header" style="margin-top:10px;">TA VS OUTCOME</div>',
                                unsafe_allow_html=True)
                    st.dataframe(ob.get("ta_outcomes", pd.DataFrame()), use_container_width=True,
                                 hide_index=True, height=220)

                st.markdown('<div class="bb-header" style="margin-top:10px;">SENTIMENT VS OUTCOME</div>',
                            unsafe_allow_html=True)
                st.dataframe(ob.get("sentiment_outcomes", pd.DataFrame()), use_container_width=True,
                             hide_index=True, height=180)

                o_left, o_right = st.columns(2)
                with o_left:
                    st.markdown('<div class="bb-header" style="margin-top:10px;">WEEKDAY VS OUTCOME</div>',
                                unsafe_allow_html=True)
                    st.dataframe(ob.get("weekday_outcomes", pd.DataFrame()), use_container_width=True,
                                 hide_index=True, height=220)
                with o_right:
                    st.markdown('<div class="bb-header" style="margin-top:10px;">TIME TO OUTCOME</div>',
                                unsafe_allow_html=True)
                    st.dataframe(ob.get("time_to_outcome", pd.DataFrame()), use_container_width=True,
                                 hide_index=True, height=220)

                symbol_edge = _symbol_edge_analytics(outcome_df, min_sample=3)
                st.markdown('<div class="bb-header" style="margin-top:10px;">SYMBOL EDGE (MIN 3 RESOLVED)</div>',
                            unsafe_allow_html=True)
                s_left, s_right = st.columns(2)
                with s_left:
                    st.dataframe(symbol_edge.get("best_symbols", pd.DataFrame()), use_container_width=True,
                                 hide_index=True, height=240)
                with s_right:
                    st.dataframe(symbol_edge.get("weak_symbols", pd.DataFrame()), use_container_width=True,
                                 hide_index=True, height=240)
                st.caption(
                    f"Qualified symbols: {symbol_edge.get('qualified_symbols', 0)} "
                    f"with at least {symbol_edge.get('min_sample', 3)} resolved outcomes."
                )

    with tab_rd:
        if st.button("RUN READINESS CHECK", type="primary"):
            with st.spinner("Checking gates..."):
                from readiness.checker import ReadinessChecker
                ReadinessChecker().check()
                st.rerun()

        r = _load_json("logs/readiness_report.json")
        if r:
            passed = r.get("passed",0); total = r.get("total",8)
            if r.get("is_ready"):
                st.success("ALL GATES PASSED — Ready for Phase 2")
            else:
                days = r.get("days_remaining")
                st.warning(f"{passed}/{total} gates passed — "
                           f"{'~'+str(days)+' more trading days' if days else 'keep paper trading'}")

            st.caption(f"Checked: {r.get('timestamp','')}")
            for gate in r.get("gates", []):
                ok = gate["passed"]
                c  = "#00C805" if ok else "#FF3B3B"
                pct_g = min(gate["actual"]/gate["required"], 1.0) if gate["required"]>0 else 1.0
                st.markdown(f"""
                <div style="margin-bottom:10px;">
                    <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
                        <span style="font-size:11px;color:#cccccc;">
                            <span style="color:{c};margin-right:6px;">{'[+]' if ok else '[-]'}</span>
                            {gate['label']}</span>
                        <span style="font-family:JetBrains Mono;font-size:11px;color:{c};">
                            {gate['actual']:.1f} / {gate['required']:.1f}</span>
                    </div>
                    <div style="background:#1e1e1e;height:3px;">
                        <div style="background:{c};height:3px;width:{pct_g*100:.0f}%;"></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            st.info(r.get("recommendation",""))


# =============================================================================
# PAGE: CONFIG
# =============================================================================
elif page == "CONFIG":
    st.markdown('<div class="bb-header" style="font-size:12px;">CONFIGURATION</div>',
                unsafe_allow_html=True)

    # Reload from disk on every page visit
    S.reload()
    cfg = S.all_settings()
    cfg_health = _get_health_snapshot()

    st.info("All settings are saved to logs/user_settings.json on the server. "
            "Changes take effect immediately in the dashboard. "
            "Click RESTART SCHEDULER below to apply changes to the scheduler process.")
    _render_config_summary(cfg, cfg_health)

    tab_api, tab_mode, tab_strat, tab_risk, tab_sched, tab_markets, tab_tools, tab_ops = st.tabs([
        "API KEYS", "MODE", "STRATEGY", "RISK", "SCHEDULER", "MARKETS", "TOOLS", "OPS"
    ])

    # ── API Keys ──────────────────────────────────────────────────────────────
    with tab_api:
        st.markdown('<div class="bb-header">TELEGRAM</div>', unsafe_allow_html=True)
        tg_ok = bool(cfg.get("TELEGRAM_BOT_TOKEN") and cfg.get("TELEGRAM_CHAT_ID"))
        if tg_ok:
            st.success("Telegram configured")
        else:
            st.warning("Telegram not configured — alerts disabled")

        tg_tok  = st.text_input("BOT TOKEN",  value=cfg.get("TELEGRAM_BOT_TOKEN",""),
                                 type="password", help="From @BotFather on Telegram")
        tg_chat = st.text_input("CHAT ID",    value=cfg.get("TELEGRAM_CHAT_ID",""),
                                 help="Your Telegram chat/group ID")

        if st.button("TEST TELEGRAM", use_container_width=True):
            try:
                import requests as _req
                r = _req.get(
                    f"https://api.telegram.org/bot{tg_tok}/sendMessage",
                    params={"chat_id": tg_chat, "text": "QuantEdge: Telegram test OK"},
                    timeout=8,
                )
                st.success("Telegram OK!") if r.ok else st.error(f"Failed: {r.text[:100]}")
            except Exception as e:
                st.error(str(e))

        st.markdown('<div class="bb-header" style="margin-top:14px;">DISCORD</div>',
                    unsafe_allow_html=True)
        dc_ok = bool(cfg.get("DISCORD_BOT_TOKEN") and cfg.get("DISCORD_CHANNEL_ID"))
        if dc_ok:
            st.success("Discord configured")
        else:
            st.warning("Discord not configured — bot commands disabled")

        dc_tok = st.text_input("BOT TOKEN", value=cfg.get("DISCORD_BOT_TOKEN", ""),
                                type="password", help="From Discord Developer Portal → Bot → Token",
                                key="dc_tok")
        dc_ch  = st.text_input("CHANNEL ID", value=cfg.get("DISCORD_CHANNEL_ID", ""),
                                help="Right-click channel → Copy ID (enable Developer Mode first)",
                                key="dc_ch")

        if st.button("TEST DISCORD", use_container_width=True):
            try:
                import requests as _req
                r = _req.post(
                    f"https://discord.com/api/v10/channels/{dc_ch}/messages",
                    headers={"Authorization": f"Bot {dc_tok}",
                             "Content-Type": "application/json"},
                    json={"content": "QuantEdge: Discord test OK ✅"},
                    timeout=8,
                )
                st.success("Discord OK!") if r.status_code in (200, 201) \
                    else st.error(f"Failed ({r.status_code}): {r.text[:150]}")
            except Exception as e:
                st.error(str(e))

        if st.button("SAVE DISCORD KEYS", use_container_width=True):
            S.save({"DISCORD_BOT_TOKEN": dc_tok, "DISCORD_CHANNEL_ID": dc_ch})
            st.success("Discord keys saved. Restart scheduler to apply.")
            st.rerun()

        st.markdown('<div class="bb-header" style="margin-top:14px;">ZERODHA KITE</div>',
                    unsafe_allow_html=True)
        kite_ok = bool(cfg.get("KITE_API_KEY"))
        if kite_ok:
            st.success("Kite API key configured")
        else:
            st.info("Not configured — only needed for live trading")

        kite_key    = st.text_input("KITE API KEY",    value=cfg.get("KITE_API_KEY",""),
                                     type="password")
        kite_secret = st.text_input("KITE API SECRET", value=cfg.get("KITE_API_SECRET",""),
                                     type="password")

        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
        if st.button("SAVE API KEYS", type="primary", use_container_width=True):
            S.save({
                "TELEGRAM_BOT_TOKEN": tg_tok,
                "TELEGRAM_CHAT_ID":   tg_chat,
                "KITE_API_KEY":       kite_key,
                "KITE_API_SECRET":    kite_secret,
            })
            st.success("Saved. Restart the scheduler for Telegram alerts to use new keys.")
            st.rerun()

    # ── Mode ──────────────────────────────────────────────────────────────────
    with tab_mode:
        st.markdown('<div class="bb-header">TRADING MODE</div>', unsafe_allow_html=True)
        new_trading = st.radio(
            "Trading Mode",
            ["paper", "live"],
            index=0 if cfg.get("TRADING_MODE","paper") == "paper" else 1,
            horizontal=True,
            help="Paper = virtual orders only. Live = real Zerodha orders.",
            label_visibility="collapsed",
        )
        if new_trading == "live":
            st.warning("LIVE MODE: Real money will be used. Make sure Kite API keys are configured.")

        st.markdown('<div class="bb-header" style="margin-top:14px;">AGENT MODE</div>',
                    unsafe_allow_html=True)
        new_agent = st.radio(
            "Agent Mode",
            ["copilot", "autopilot"],
            index=0 if cfg.get("AGENT_MODE","copilot") == "copilot" else 1,
            horizontal=True,
            help="Copilot = signals shown, you approve each trade. Autopilot = agent executes automatically.",
            label_visibility="collapsed",
        )
        st.markdown(f"""
        <div style="font-size:10px;color:#666;margin-top:6px;">
            <b>Copilot</b>: signals shown on Today page with Approve/Skip buttons.
            You decide which trades to take.<br>
            <b>Autopilot</b>: scheduler auto-executes trades at scan times without approval.
        </div>
        """, unsafe_allow_html=True)

        st.markdown('<div class="bb-header" style="margin-top:14px;">VIRTUAL CAPITAL</div>',
                    unsafe_allow_html=True)
        new_capital = st.number_input("Starting Capital (Rs.)",
                                       value=int(cfg.get("VIRTUAL_CAPITAL", 1_000_000)),
                                       step=100_000, min_value=100_000)
        st.caption("Changing capital resets the P&L baseline — does not affect existing positions.")

        if st.button("SAVE MODE SETTINGS", type="primary", use_container_width=True):
            S.save({
                "TRADING_MODE":    new_trading,
                "AGENT_MODE":      new_agent,
                "VIRTUAL_CAPITAL": new_capital,
            })
            st.success("Saved. Refresh the page to see updated mode in sidebar.")
            st.rerun()

    # ── Strategy ──────────────────────────────────────────────────────────────
    with tab_strat:
        st.markdown('<div class="bb-header">SIGNAL GENERATION</div>', unsafe_allow_html=True)
        st.caption("Tune entry quality first. These settings control how strict the shortlist becomes.")
        s1, s2 = st.columns(2)
        with s1:
            new_min_ta   = st.slider("MIN TA SCORE",    1.0, 9.0,
                                      float(cfg.get("MIN_TA_SCORE", 5.0)), 0.5)
            new_min_conf = st.slider("MIN CONFIDENCE",  0.3, 0.95,
                                      float(cfg.get("MIN_CONFIDENCE", 0.60)), 0.05)
            new_top_n    = st.slider("TOP N SIGNALS",   1, 20,
                                      int(cfg.get("TOP_N_SIGNALS", 10)), 1)
        with s2:
            new_ta_wt   = st.slider("TA WEIGHT",       0.1, 0.9,
                                     float(cfg.get("TA_WEIGHT", 0.50)), 0.05)
            new_sent_wt = st.slider("SENTIMENT WEIGHT",0.0, 0.5,
                                     float(cfg.get("SENTIMENT_WEIGHT", 0.30)), 0.05)
            new_refresh = st.slider("DASHBOARD REFRESH (SEC)", 15, 300,
                                     int(cfg.get("DASHBOARD_REFRESH_SEC", 30)), 15)

        if st.button("SAVE STRATEGY SETTINGS", type="primary", use_container_width=True):
            S.save({
                "MIN_TA_SCORE":          new_min_ta,
                "MIN_CONFIDENCE":        new_min_conf,
                "TOP_N_SIGNALS":         new_top_n,
                "TA_WEIGHT":             new_ta_wt,
                "SENTIMENT_WEIGHT":      new_sent_wt,
                "DASHBOARD_REFRESH_SEC": new_refresh,
            })
            st.success("Strategy settings saved.")
            st.rerun()

        st.markdown('<div class="bb-header" style="margin-top:14px;">STRATEGY PROFILES</div>',
                    unsafe_allow_html=True)
        st.caption("Apply a preset if you want a faster admin workflow than moving each slider manually.")
        p1, p2, p3 = st.columns(3)
        if p1.button("CONSERVATIVE", use_container_width=True, key="strat_conservative"):
            S.save({
                "MIN_TA_SCORE": 6.5,
                "MIN_CONFIDENCE": 0.70,
                "TOP_N_SIGNALS": 6,
                "TA_WEIGHT": 0.60,
                "SENTIMENT_WEIGHT": 0.20,
                "DASHBOARD_REFRESH_SEC": 45,
            })
            st.success("Applied conservative strategy profile.")
            st.rerun()
        if p2.button("BALANCED", use_container_width=True, key="strat_balanced"):
            S.save({
                "MIN_TA_SCORE": 5.5,
                "MIN_CONFIDENCE": 0.60,
                "TOP_N_SIGNALS": 10,
                "TA_WEIGHT": 0.50,
                "SENTIMENT_WEIGHT": 0.30,
                "DASHBOARD_REFRESH_SEC": 30,
            })
            st.success("Applied balanced strategy profile.")
            st.rerun()
        if p3.button("AGGRESSIVE", use_container_width=True, key="strat_aggressive"):
            S.save({
                "MIN_TA_SCORE": 4.5,
                "MIN_CONFIDENCE": 0.50,
                "TOP_N_SIGNALS": 14,
                "TA_WEIGHT": 0.45,
                "SENTIMENT_WEIGHT": 0.35,
                "DASHBOARD_REFRESH_SEC": 20,
            })
            st.success("Applied aggressive strategy profile.")
            st.rerun()

    # ── Risk ──────────────────────────────────────────────────────────────────
    with tab_risk:
        st.markdown('<div class="bb-header">POSITION SIZING & STOPS</div>', unsafe_allow_html=True)
        st.caption("These settings control capital deployment, stop behavior, and portfolio concentration.")
        r1, r2 = st.columns(2)
        with r1:
            new_risk    = st.slider("RISK PER TRADE %",    0.5, 5.0,
                                     float(cfg.get("RISK_PER_TRADE_PCT",0.02))*100, 0.25,
                                     format="%.2f%%")
            new_maxpos  = st.slider("MAX OPEN POSITIONS",  1, 15,
                                     int(cfg.get("MAX_OPEN_POSITIONS", 5)), 1)
            new_rr      = st.slider("REWARD / RISK RATIO", 1.0, 5.0,
                                     float(cfg.get("REWARD_RISK_RATIO", 2.0)), 0.25)
        with r2:
            new_trail   = st.slider("TRAILING STOP %",    0.5, 8.0,
                                     float(cfg.get("TRAIL_PCT", 0.02))*100, 0.25,
                                     format="%.2f%%")
            new_dd_day  = st.slider("MAX DAILY LOSS %",   0.5, 10.0,
                                     float(cfg.get("MAX_DAILY_LOSS_PCT",0.03))*100, 0.5,
                                     format="%.1f%%")
            new_sector  = st.slider("MAX SAME SECTOR",    1, 5,
                                     int(cfg.get("MAX_SAME_SECTOR", 2)), 1)

        if st.button("SAVE RISK SETTINGS", type="primary", use_container_width=True):
            S.save({
                "RISK_PER_TRADE_PCT":  new_risk / 100,
                "MAX_OPEN_POSITIONS":  new_maxpos,
                "REWARD_RISK_RATIO":   new_rr,
                "TRAIL_PCT":           new_trail / 100,
                "MAX_DAILY_LOSS_PCT":  new_dd_day / 100,
                "MAX_SAME_SECTOR":     new_sector,
            })
            st.success("Risk settings saved.")
            st.rerun()

        st.markdown('<div class="bb-header" style="margin-top:14px;">RISK PROFILES</div>',
                    unsafe_allow_html=True)
        rp1, rp2, rp3 = st.columns(3)
        if rp1.button("LOW RISK", use_container_width=True, key="risk_low"):
            S.save({
                "RISK_PER_TRADE_PCT": 0.01,
                "MAX_OPEN_POSITIONS": 4,
                "REWARD_RISK_RATIO": 2.0,
                "TRAIL_PCT": 0.015,
                "MAX_DAILY_LOSS_PCT": 0.02,
                "MAX_SAME_SECTOR": 1,
            })
            st.success("Applied low-risk profile.")
            st.rerun()
        if rp2.button("STANDARD", use_container_width=True, key="risk_standard"):
            S.save({
                "RISK_PER_TRADE_PCT": 0.02,
                "MAX_OPEN_POSITIONS": 5,
                "REWARD_RISK_RATIO": 2.0,
                "TRAIL_PCT": 0.02,
                "MAX_DAILY_LOSS_PCT": 0.03,
                "MAX_SAME_SECTOR": 2,
            })
            st.success("Applied standard risk profile.")
            st.rerun()
        if rp3.button("HIGH CONVICTION", use_container_width=True, key="risk_high_conviction"):
            S.save({
                "RISK_PER_TRADE_PCT": 0.03,
                "MAX_OPEN_POSITIONS": 7,
                "REWARD_RISK_RATIO": 2.5,
                "TRAIL_PCT": 0.025,
                "MAX_DAILY_LOSS_PCT": 0.04,
                "MAX_SAME_SECTOR": 2,
            })
            st.success("Applied high-conviction risk profile.")
            st.rerun()

    # ── Scheduler ────────────────────────────────────────────────────────────
    with tab_sched:
        st.markdown('<div class="bb-header">SCAN TIMES (IST, MON-FRI)</div>',
                    unsafe_allow_html=True)
        st.caption(f"Scheduler heartbeat: {_scheduler_heartbeat_label(cfg_health)}")
        sc1, sc2 = st.columns(2)
        new_t1 = sc1.text_input("SCAN 1 (HH:MM)", value=cfg.get("SCAN_TIME_1","09:15"),
                                  help="e.g. 09:15")
        new_t2 = sc2.text_input("SCAN 2 (HH:MM)", value=cfg.get("SCAN_TIME_2","15:00"),
                                  help="e.g. 15:00")

        # Validate
        def _valid_time(t):
            try:
                h, m = t.split(":")
                return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
            except Exception: return False

        if not _valid_time(new_t1) or not _valid_time(new_t2):
            st.error("Invalid time format. Use HH:MM (e.g. 09:15)")

        if st.button("SAVE SCHEDULER TIMES", type="primary", use_container_width=True):
            if _valid_time(new_t1) and _valid_time(new_t2):
                S.save({"SCAN_TIME_1": new_t1, "SCAN_TIME_2": new_t2})
                st.success(f"Saved. Scans will run at {new_t1} and {new_t2} IST after restart.")
            else:
                st.error("Fix time format before saving.")

        st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
        st.markdown('<div class="bb-header">RESTART SCHEDULER</div>', unsafe_allow_html=True)
        st.info("After changing scan times, API keys, or mode — restart the scheduler "
                "for changes to take effect.")

        if st.button("RESTART SCHEDULER", use_container_width=True):
            try:
                import subprocess, sys

                pid_file = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "logs", "scheduler.pid"
                )
                killed = 0
                try:
                    if os.path.exists(pid_file):
                        with open(pid_file) as f:
                            old_pid = int(f.read().strip())
                        result = subprocess.run(
                            ["taskkill", "/PID", str(old_pid), "/F"],
                            capture_output=True, text=True
                        )
                        if result.returncode == 0:
                            killed = 1
                except Exception:
                    pass

                # Start fresh scheduler process
                scheduler_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "scheduler", "scheduler.py"
                )
                subprocess.Popen(
                    [sys.executable, scheduler_path],
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                    if hasattr(subprocess, "CREATE_NEW_CONSOLE") else 0,
                )
                st.success(f"Scheduler started in background"
                           f"{f' (killed {killed} old process)' if killed else ''}.")
            except Exception as e:
                st.error(f"Could not restart: {e}")
                st.info("Start manually: `python scheduler/scheduler.py`")

        st.markdown('<div class="bb-header" style="margin-top:14px;">CURRENT SETTINGS SUMMARY</div>',
                    unsafe_allow_html=True)
        display = {k: v for k, v in S.all_settings().items()
                   if k not in ("TELEGRAM_BOT_TOKEN","KITE_API_KEY","KITE_API_SECRET")}
        with st.expander("VIEW SETTINGS JSON", expanded=False):
            st.json(display)

    # ── Markets ───────────────────────────────────────────────────────────────
    with tab_markets:
        st.markdown('<div class="bb-header">F&O PAPER TRADING</div>', unsafe_allow_html=True)
        m1, m2 = st.columns(2)
        with m1:
            new_fno_tp    = st.slider("TP MULTIPLIER (options buy)",  1.5, 5.0,
                                       float(cfg.get("FNO_TP_MULT", 2.0)), 0.25,
                                       help="Exit when premium reaches this × entry. Default 2x.")
            new_fno_sl    = st.slider("SL MULTIPLIER (options buy)",  0.10, 0.90,
                                       float(cfg.get("FNO_SL_MULT", 0.50)), 0.05,
                                       help="Exit when premium falls to this × entry. Default 0.5.")
            new_fno_max   = st.slider("MAX F&O POSITIONS",  1, 20,
                                       int(cfg.get("FNO_MAX_POSITIONS", 6)), 1)
        with m2:
            new_hv_strad  = st.slider("HV% STRADDLE THRESHOLD",  10.0, 40.0,
                                       float(cfg.get("FNO_HV_STRADDLE", 18.0)), 1.0,
                                       help="Use straddle when HV > this %")
            new_hv_stran  = st.slider("HV% STRANGLE THRESHOLD",   5.0, 30.0,
                                       float(cfg.get("FNO_HV_STRANGLE", 12.0)), 1.0,
                                       help="Use strangle when HV > this % (below straddle)")
            new_cache_min = st.slider("OPTIONS CHAIN CACHE (min)", 1, 30,
                                       int(cfg.get("FNO_CHAIN_CACHE_MIN", 5)), 1)

        new_sell_days = st.text_input("SELLING DAYS (comma-sep)",
                                       value=cfg.get("FNO_SELL_DAYS", "tue,wed,thu"),
                                       help="Days to run options selling. e.g. tue,wed,thu")

        if st.button("SAVE F&O SETTINGS", type="primary", use_container_width=True):
            S.save({
                "FNO_TP_MULT":         new_fno_tp,
                "FNO_SL_MULT":         new_fno_sl,
                "FNO_MAX_POSITIONS":   new_fno_max,
                "FNO_HV_STRADDLE":     new_hv_strad,
                "FNO_HV_STRANGLE":     new_hv_stran,
                "FNO_CHAIN_CACHE_MIN": new_cache_min,
                "FNO_SELL_DAYS":       new_sell_days,
            })
            st.success("F&O settings saved.")
            st.rerun()

        st.markdown('<div class="bb-header" style="margin-top:14px;">CRYPTO PAPER TRADING</div>',
                    unsafe_allow_html=True)
        cr1, cr2, cr3 = st.columns(3)
        with cr1:
            new_cr_amt = st.number_input("USDT PER TRADE",
                                          value=float(cfg.get("CRYPTO_USDT_PER_TRADE", 100.0)),
                                          min_value=10.0, step=10.0)
        with cr2:
            new_cr_tp  = st.slider("CRYPTO TP %",  1.0, 20.0,
                                    float(cfg.get("CRYPTO_TP_PCT", 0.08)) * 100, 0.5,
                                    format="%.1f%%")
        with cr3:
            new_cr_sl  = st.slider("CRYPTO SL %",  0.5, 10.0,
                                    float(cfg.get("CRYPTO_SL_PCT", 0.04)) * 100, 0.5,
                                    format="%.1f%%")

        if st.button("SAVE CRYPTO SETTINGS", type="primary", use_container_width=True):
            S.save({
                "CRYPTO_USDT_PER_TRADE": new_cr_amt,
                "CRYPTO_TP_PCT":         new_cr_tp / 100,
                "CRYPTO_SL_PCT":         new_cr_sl / 100,
            })
            st.success("Crypto settings saved.")
            st.rerun()

        st.markdown('<div class="bb-header" style="margin-top:14px;">US STOCKS PAPER TRADING</div>',
                    unsafe_allow_html=True)
        us1, us2, us3 = st.columns(3)
        with us1:
            new_us_amt = st.number_input("USD PER TRADE",
                                          value=float(cfg.get("US_USD_PER_TRADE", 500.0)),
                                          min_value=50.0, step=50.0)
        with us2:
            new_us_tp  = st.slider("US STOCKS TP %",  1.0, 20.0,
                                    float(cfg.get("US_TP_PCT", 0.06)) * 100, 0.5,
                                    format="%.1f%%")
        with us3:
            new_us_sl  = st.slider("US STOCKS SL %",  0.5, 10.0,
                                    float(cfg.get("US_SL_PCT", 0.03)) * 100, 0.5,
                                    format="%.1f%%")

        new_inr_rate = st.number_input("USD/USDT → INR RATE",
                                        value=float(cfg.get("INR_PER_USD", 83.0)),
                                        min_value=50.0, step=0.5,
                                        help="Used for combined P&L display in INR")

        if st.button("SAVE US SETTINGS", type="primary", use_container_width=True):
            S.save({
                "US_USD_PER_TRADE": new_us_amt,
                "US_TP_PCT":        new_us_tp / 100,
                "US_SL_PCT":        new_us_sl / 100,
                "INR_PER_USD":      new_inr_rate,
                "INR_PER_USDT":     new_inr_rate,
            })
            st.success("US stocks settings saved.")
            st.rerun()

    # ── Tools ─────────────────────────────────────────────────────────────────
    with tab_tools:
        st.markdown('<div class="bb-header">MODULE TESTS</div>', unsafe_allow_html=True)
        t1, t2, t3 = st.columns(3)
        with t1:
            if st.button("MARKET REGIME", use_container_width=True):
                from analysis.market_regime import MarketRegimeFilter
                r = MarketRegimeFilter().get_regime()
                (st.success if r.allow_buys else st.error)(f"{r.regime.upper()} — {r.message}")
            if st.button("PCR SIGNAL", use_container_width=True):
                from analysis.pcr_signal import PCRAnalyser
                r = PCRAnalyser().get_signal()
                st.info(f"PCR {r.pcr:.2f} — {r.message}")
            if st.button("FII/DII FLOW", use_container_width=True):
                from analysis.fii_dii import FIIDIIAnalyser
                r = FIIDIIAnalyser().get_signal()
                st.info(f"{r.signal.upper()} — {r.message}")

        with t2:
            sym_t = st.text_input("SYMBOL", "RELIANCE", key="tools_sym")
            if st.button("RUN TA", use_container_width=True):
                df = yf.Ticker(f"{sym_t}.NS").history(period="400d",interval="1d",auto_adjust=True)
                df.columns = [c.lower() for c in df.columns]
                from analysis.technical_agent import TechnicalAgent
                res = TechnicalAgent().analyse(sym_t, df)
                if res:
                    st.metric("SCORE",  f"{res.score}/10")
                    st.metric("SIGNAL", res.signal.upper())
                    for rr in res.reasoning:
                        st.caption(f"• {rr}")
            if st.button("SENTIMENT", use_container_width=True):
                from analysis.sentiment_agent import SentimentAgent
                res = SentimentAgent().analyse(sym_t, sym_t)
                st.metric("SCORE", f"{res.score:+.2f}")
                st.metric("LABEL", res.label.upper())

        with t3:
            if st.button("CIRCUIT BREAKER", use_container_width=True):
                pf2 = _load_pf()
                from risk.circuit_breaker import CircuitBreaker
                ok, reason = CircuitBreaker().check(pf2["cash"])
                (st.success if ok else st.error)(reason)
            if st.button("TRAILING STOPS", use_container_width=True):
                from risk.trailing_stop import TrailingStopMonitor
                res = TrailingStopMonitor().run()
                st.success(f"Checked {len(res)} positions")
            if st.button("DAILY REPORT", use_container_width=True):
                from execution.daily_report import DailyReporter
                DailyReporter().send_report()
                st.success("Report sent!")

    with tab_ops:
        st.markdown('<div class="bb-header">OPERATIONS</div>', unsafe_allow_html=True)
        _render_health_panel(_get_health_snapshot(), show_actions=True)

        st.markdown('<div class="bb-header" style="margin-top:14px;">AGENT REVIEW REPORT</div>',
                    unsafe_allow_html=True)
        st.caption("Generate a synced review snapshot for current positions, trades, signals, and exposure without opening the server.")

        rr1, rr2 = st.columns(2)
        with rr1:
            if st.button("GENERATE REVIEW REPORT", use_container_width=True):
                try:
                    svc_sync_unified_state()
                    _load_review_report_json.clear()
                    _load_review_report_markdown.clear()
                    st.success("Review report generated.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not generate review report: {e}")
        with rr2:
            if st.button("REFRESH REVIEW PREVIEW", use_container_width=True):
                _load_review_report_json.clear()
                _load_review_report_markdown.clear()
                st.rerun()

        review = _load_review_report_json()
        review_md = _load_review_report_markdown()
        review_summary = review.get("summary", {}) if isinstance(review, dict) else {}

        sr1, sr2, sr3, sr4 = st.columns(4)
        sr1.metric("OPEN POSITIONS", review_summary.get("combined_open_positions", 0))
        sr2.metric("OPEN P&L", f"Rs.{float(review_summary.get('combined_open_pnl_inr', 0) or 0):,.0f}")
        sr3.metric("EXECUTED SIGNALS", review_summary.get("executed_signal_count", 0))
        sr4.metric("CLOSED TRADES", review_summary.get("closed_trade_count", 0))

        dl1, dl2 = st.columns(2)
        with dl1:
            st.download_button(
                "DOWNLOAD REVIEW.md",
                data=review_md,
                file_name="agent_review_report.md",
                mime="text/markdown",
                use_container_width=True,
            )
        with dl2:
            st.download_button(
                "DOWNLOAD REVIEW.json",
                data=json.dumps(review, indent=2),
                file_name="agent_review_report.json",
                mime="application/json",
                use_container_width=True,
            )

        with st.expander("VIEW REVIEW REPORT", expanded=False):
            st.markdown(review_md)

        st.markdown('<div class="bb-header" style="margin-top:14px;">DASHBOARD REFRESH</div>',
                    unsafe_allow_html=True)
        refresh_sec = st.slider(
            "AUTO-REFRESH INTERVAL (SEC)",
            15, 300, int(cfg.get("DASHBOARD_REFRESH_SEC", 30)), 15,
            help="Used by lightweight live panels and future dashboard polling."
        )
        if st.button("SAVE OPS SETTINGS", type="primary", use_container_width=True):
            S.save({"DASHBOARD_REFRESH_SEC": refresh_sec})
            st.success("Operations settings saved.")
            st.rerun()
