# =============================================================================
# dashboard/app.py — Premium Trading Dashboard
# Dark terminal aesthetic — professional, data-dense, zero fluff
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import json, time
from datetime import datetime, timedelta
import yfinance as yf

from config import (TRADING_MODE, VIRTUAL_CAPITAL, TOP_N_SIGNALS,
                    MIN_TA_SCORE, RISK_PER_TRADE_PCT, MAX_OPEN_POSITIONS)
from memory.portfolio_memory import PortfolioMemory

st.set_page_config(
    page_title="AlgoTrader Pro",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ------------------------------------------------------------------
# Premium CSS — Dark terminal aesthetic
# ------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&family=Space+Grotesk:wght@300;400;500;600;700&display=swap');

/* Base */
html, body, [class*="css"] {
    font-family: 'Space Grotesk', sans-serif;
}
.stApp {
    background: #0a0e1a;
}

/* Sidebar */
section[data-testid="stSidebar"] {
    background: #0d1224 !important;
    border-right: 1px solid #1e2d4a;
}
section[data-testid="stSidebar"] .stRadio label {
    color: #7a8ba6 !important;
    font-size: 13px !important;
    padding: 6px 0 !important;
    transition: color 0.2s;
}
section[data-testid="stSidebar"] .stRadio label:hover {
    color: #00d4aa !important;
}

/* Main content */
.block-container {
    padding: 1.5rem 2rem !important;
    max-width: 1400px;
}

/* Page title */
h1 { font-family: 'Space Grotesk', sans-serif !important; font-weight: 700 !important;
     color: #ffffff !important; letter-spacing: -0.5px; }
h2 { font-family: 'Space Grotesk', sans-serif !important; font-weight: 600 !important;
     color: #e2e8f0 !important; font-size: 1.1rem !important; }
h3 { color: #94a3b8 !important; font-size: 0.9rem !important; font-weight: 500 !important;
     text-transform: uppercase; letter-spacing: 1px; }

/* Metric cards */
div[data-testid="metric-container"] {
    background: #0d1224 !important;
    border: 1px solid #1e2d4a !important;
    border-radius: 12px !important;
    padding: 16px 20px !important;
    position: relative;
    overflow: hidden;
}
div[data-testid="metric-container"]::before {
    content: '';
    position: absolute;
    top: 0; left: 0;
    width: 3px; height: 100%;
    background: linear-gradient(180deg, #00d4aa, #0066ff);
    border-radius: 12px 0 0 12px;
}
div[data-testid="stMetricLabel"] {
    color: #64748b !important;
    font-size: 11px !important;
    text-transform: uppercase;
    letter-spacing: 1px;
    font-weight: 500 !important;
}
div[data-testid="stMetricValue"] {
    color: #f1f5f9 !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 1.4rem !important;
    font-weight: 700 !important;
}
div[data-testid="stMetricDelta"] {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 12px !important;
}

/* Buttons */
.stButton > button {
    background: #0d1224 !important;
    color: #00d4aa !important;
    border: 1px solid #00d4aa !important;
    border-radius: 8px !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 12px !important;
    font-weight: 500 !important;
    letter-spacing: 0.5px;
    transition: all 0.2s !important;
}
.stButton > button:hover {
    background: #00d4aa !important;
    color: #0a0e1a !important;
    transform: translateY(-1px);
    box-shadow: 0 4px 20px rgba(0,212,170,0.3) !important;
}
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #00d4aa, #0066ff) !important;
    color: #fff !important;
    border: none !important;
    font-weight: 700 !important;
}
.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #00efc0, #0077ff) !important;
    color: #fff !important;
    box-shadow: 0 4px 24px rgba(0,212,170,0.4) !important;
}

/* Cards */
.card {
    background: #0d1224;
    border: 1px solid #1e2d4a;
    border-radius: 16px;
    padding: 20px 24px;
    margin-bottom: 16px;
}
.card-header {
    font-size: 11px;
    font-weight: 600;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.card-header::before {
    content: '';
    display: inline-block;
    width: 6px; height: 6px;
    border-radius: 50%;
    background: #00d4aa;
    flex-shrink: 0;
}

/* Position cards */
.pos-card {
    background: #111827;
    border: 1px solid #1e2d4a;
    border-radius: 12px;
    padding: 16px 20px;
    margin-bottom: 10px;
    display: grid;
    grid-template-columns: 1fr 1fr 1fr 1fr 1fr;
    gap: 12px;
    align-items: center;
    transition: border-color 0.2s;
}
.pos-card:hover { border-color: #00d4aa; }
.pos-card.profit { border-left: 3px solid #00d4aa; }
.pos-card.loss   { border-left: 3px solid #ef4444; }
.pos-card.neutral{ border-left: 3px solid #3b82f6; }
.pos-sym  { font-family: 'JetBrains Mono'; font-weight: 700; font-size: 15px; color: #f1f5f9; }
.pos-type { font-size: 10px; font-weight: 600; text-transform: uppercase;
            letter-spacing: 1px; color: #64748b; margin-top: 2px; }
.pos-label{ font-size: 10px; color: #64748b; text-transform: uppercase;
             letter-spacing: 0.8px; margin-bottom: 3px; }
.pos-val  { font-family: 'JetBrains Mono'; font-size: 14px; color: #e2e8f0; font-weight: 500; }
.pos-pnl-pos { font-family: 'JetBrains Mono'; font-size: 14px;
               color: #00d4aa; font-weight: 700; }
.pos-pnl-neg { font-family: 'JetBrains Mono'; font-size: 14px;
               color: #ef4444; font-weight: 700; }
.pos-badge-swing  { background: rgba(59,130,246,0.15); color: #60a5fa;
                    border: 1px solid rgba(59,130,246,0.3); font-size: 10px;
                    padding: 2px 8px; border-radius: 99px; font-weight: 600; }
.pos-badge-intra  { background: rgba(245,158,11,0.15); color: #fbbf24;
                    border: 1px solid rgba(245,158,11,0.3); font-size: 10px;
                    padding: 2px 8px; border-radius: 99px; font-weight: 600; }

/* Signal cards */
.sig-card {
    background: #111827;
    border: 1px solid #1e2d4a;
    border-radius: 12px;
    padding: 16px 20px;
    margin-bottom: 10px;
    transition: all 0.2s;
}
.sig-card:hover { border-color: #00d4aa; transform: translateX(4px); }
.sig-header { display: flex; justify-content: space-between; align-items: center;
              margin-bottom: 12px; }
.sig-sym { font-family: 'JetBrains Mono'; font-size: 18px; font-weight: 700; color: #f1f5f9; }
.sig-action-buy  { background: rgba(0,212,170,0.15); color: #00d4aa;
                   border: 1px solid rgba(0,212,170,0.3);
                   padding: 3px 12px; border-radius: 99px; font-size: 11px; font-weight: 700; }
.sig-action-sell { background: rgba(239,68,68,0.15); color: #ef4444;
                   border: 1px solid rgba(239,68,68,0.3);
                   padding: 3px 12px; border-radius: 99px; font-size: 11px; font-weight: 700; }
.sig-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.sig-metric-label { font-size: 10px; color: #64748b; text-transform: uppercase;
                    letter-spacing: 0.8px; margin-bottom: 3px; }
.sig-metric-val   { font-family: 'JetBrains Mono'; font-size: 13px; color: #e2e8f0; font-weight: 500; }
.sig-reason { margin-top: 10px; padding-top: 10px; border-top: 1px solid #1e2d4a;
              font-size: 12px; color: #64748b; line-height: 1.6; }

/* Progress bar */
.prog-wrap { background: #1e2d4a; border-radius: 99px; height: 6px; margin: 6px 0 12px; }
.prog-fill-good { background: linear-gradient(90deg,#00d4aa,#00efc0);
                  border-radius: 99px; height: 6px; transition: width 0.5s; }
.prog-fill-bad  { background: linear-gradient(90deg,#ef4444,#f87171);
                  border-radius: 99px; height: 6px; }
.prog-fill-mid  { background: linear-gradient(90deg,#f59e0b,#fbbf24);
                  border-radius: 99px; height: 6px; }

/* Regime banner */
.regime-bull { background: rgba(0,212,170,0.08); border: 1px solid rgba(0,212,170,0.25);
               border-radius: 10px; padding: 12px 16px; color: #00d4aa;
               font-family: 'JetBrains Mono'; font-size: 13px; font-weight: 500; }
.regime-bear { background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.25);
               border-radius: 10px; padding: 12px 16px; color: #ef4444;
               font-family: 'JetBrains Mono'; font-size: 13px; font-weight: 500; }
.regime-side { background: rgba(245,158,11,0.08); border: 1px solid rgba(245,158,11,0.25);
               border-radius: 10px; padding: 12px 16px; color: #fbbf24;
               font-family: 'JetBrains Mono'; font-size: 13px; font-weight: 500; }

/* Table */
.stDataFrame { background: #0d1224 !important; }

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
    background: #0d1224 !important;
    border-bottom: 1px solid #1e2d4a !important;
    gap: 4px;
}
.stTabs [data-baseweb="tab"] {
    background: transparent !important;
    color: #64748b !important;
    font-family: 'JetBrains Mono' !important;
    font-size: 12px !important;
    border-radius: 8px 8px 0 0 !important;
    padding: 8px 16px !important;
}
.stTabs [aria-selected="true"] {
    background: #111827 !important;
    color: #00d4aa !important;
    border-bottom: 2px solid #00d4aa !important;
}

/* Input */
.stTextInput input, .stSelectbox select {
    background: #111827 !important;
    border: 1px solid #1e2d4a !important;
    color: #e2e8f0 !important;
    border-radius: 8px !important;
    font-family: 'JetBrains Mono', monospace !important;
}

/* Divider */
hr { border-color: #1e2d4a !important; }

/* Success/error/info */
.stSuccess { background: rgba(0,212,170,0.08) !important;
             border: 1px solid rgba(0,212,170,0.2) !important;
             border-radius: 8px !important; }
.stError   { background: rgba(239,68,68,0.08) !important;
             border: 1px solid rgba(239,68,68,0.2) !important;
             border-radius: 8px !important; }
.stInfo    { background: rgba(59,130,246,0.08) !important;
             border: 1px solid rgba(59,130,246,0.2) !important;
             border-radius: 8px !important; }
.stWarning { background: rgba(245,158,11,0.08) !important;
             border: 1px solid rgba(245,158,11,0.2) !important;
             border-radius: 8px !important; }

/* Scrollbar */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: #0a0e1a; }
::-webkit-scrollbar-thumb { background: #1e2d4a; border-radius: 3px; }

/* Log box */
.log-box {
    background: #060a12;
    border: 1px solid #1e2d4a;
    border-radius: 10px;
    padding: 14px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: #64748b;
    max-height: 250px;
    overflow-y: auto;
    line-height: 1.7;
}

/* Ticker strip (top bar) */
.ticker-bar {
    background: #060a12;
    border-bottom: 1px solid #1e2d4a;
    padding: 6px 0;
    font-family: 'JetBrains Mono';
    font-size: 11px;
    color: #64748b;
    display: flex;
    gap: 32px;
    overflow: hidden;
    margin-bottom: 20px;
}
.ticker-item { white-space: nowrap; }
.ticker-pos  { color: #00d4aa; }
.ticker-neg  { color: #ef4444; }
</style>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _load_pf():
    f = "logs/virtual_portfolio.json"
    if os.path.exists(f):
        with open(f) as fp: return json.load(fp)
    return {"cash": VIRTUAL_CAPITAL, "positions": {}, "total_trades": 0, "wins": 0}

def _load_json(path, default=None):
    if os.path.exists(path):
        try:
            with open(path) as f: return json.load(f)
        except Exception: pass
    return default or {}

def _chart(symbol, period="3mo", height=220):
    try:
        df = yf.Ticker(f"{symbol}.NS").history(period=period, interval="1d")
        if df.empty: return
        df["EMA20"] = df["Close"].ewm(span=20).mean()
        df["EMA50"] = df["Close"].ewm(span=50).mean()
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"],
            low=df["Low"], close=df["Close"], showlegend=False,
            increasing=dict(line=dict(color="#00d4aa"), fillcolor="rgba(0,212,170,0.3)"),
            decreasing=dict(line=dict(color="#ef4444"), fillcolor="rgba(239,68,68,0.3)"),
        ))
        fig.add_trace(go.Scatter(x=df.index, y=df["EMA20"],
            line=dict(color="#f59e0b", width=1.2), name="EMA20"))
        fig.add_trace(go.Scatter(x=df.index, y=df["EMA50"],
            line=dict(color="#3b82f6", width=1.2), name="EMA50"))
        fig.update_layout(
            height=height, margin=dict(l=0,r=0,t=0,b=0),
            xaxis_rangeslider_visible=False,
            plot_bgcolor="#0a0e1a", paper_bgcolor="#0a0e1a",
            xaxis=dict(gridcolor="#1e2d4a", color="#64748b"),
            yaxis=dict(gridcolor="#1e2d4a", color="#64748b"),
            legend=dict(orientation="h", y=1.1, font=dict(color="#64748b", size=10)),
            font=dict(family="JetBrains Mono"),
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception: pass

def _fetch_live_price(symbol):
    try:
        hist = yf.Ticker(f"{symbol}.NS").history(period="1d", interval="15m")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception: pass
    return None

memory = PortfolioMemory()

# ------------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------------
with st.sidebar:
    st.markdown("""
    <div style='padding: 8px 0 20px; border-bottom: 1px solid #1e2d4a; margin-bottom: 20px;'>
        <div style='font-family: JetBrains Mono; font-size: 18px; font-weight: 700;
                    color: #f1f5f9; letter-spacing: -0.5px;'>AlgoTrader</div>
        <div style='font-size: 10px; color: #64748b; text-transform: uppercase;
                    letter-spacing: 2px; margin-top: 2px;'>Pro Dashboard</div>
    </div>
    """, unsafe_allow_html=True)

    mode_color = "#00d4aa" if TRADING_MODE == "paper" else "#ef4444"
    st.markdown(f"""
    <div style='display:flex; align-items:center; gap:8px; margin-bottom:8px;'>
        <div style='width:8px;height:8px;border-radius:50%;background:{mode_color};
                    box-shadow:0 0 8px {mode_color};'></div>
        <span style='font-family:JetBrains Mono;font-size:11px;color:{mode_color};
                     font-weight:600;text-transform:uppercase;letter-spacing:1px;'>
            {TRADING_MODE} MODE</span>
    </div>
    """, unsafe_allow_html=True)

    reg = _load_json("logs/market_regime.json")
    if reg:
        regime  = reg.get("regime","unknown")
        rc      = {"bull":"#00d4aa","bear":"#ef4444","sideways":"#f59e0b"}.get(regime,"#64748b")
        st.markdown(f"""
        <div style='display:flex;align-items:center;gap:8px;margin-bottom:16px;'>
            <div style='width:6px;height:6px;border-radius:50%;background:{rc};'></div>
            <span style='font-family:JetBrains Mono;font-size:11px;color:{rc};
                         font-weight:500;'>NIFTY {regime.upper()}</span>
            <span style='font-family:JetBrains Mono;font-size:10px;color:#64748b;margin-left:auto;'>
                RSI {reg.get("rsi",0):.0f}</span>
        </div>
        """, unsafe_allow_html=True)

    st.divider()

    page = st.radio("", [
        "📊  Overview",
        "🎯  Signals",
        "💼  Portfolio",
        "📈  Positions",
        "📋  Trades",
        "🔬  Backtest",
        "🚀  Run Agent",
        "📡  Market Intel",
        "🚦  Readiness",
        "⚙️  Settings",
    ], label_visibility="collapsed")

    st.divider()

    pf    = _load_pf()
    cash  = pf.get("cash", VIRTUAL_CAPITAL)
    pnl   = cash - VIRTUAL_CAPITAL
    pnl_c = "#00d4aa" if pnl >= 0 else "#ef4444"
    pnl_s = "+" if pnl >= 0 else ""
    st.markdown(f"""
    <div style='padding:12px;background:#111827;border-radius:10px;
                border:1px solid #1e2d4a;'>
        <div style='font-size:10px;color:#64748b;text-transform:uppercase;
                    letter-spacing:1px;margin-bottom:6px;'>Portfolio Value</div>
        <div style='font-family:JetBrains Mono;font-size:20px;font-weight:700;
                    color:#f1f5f9;'>Rs.{cash:,.0f}</div>
        <div style='font-family:JetBrains Mono;font-size:12px;color:{pnl_c};
                    margin-top:4px;'>{pnl_s}Rs.{pnl:,.0f} ({pnl/VIRTUAL_CAPITAL*100:+.2f}%)</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.caption(f"🕐 {datetime.now().strftime('%d %b %Y  %H:%M')}")

# ==================================================================
# PAGE: OVERVIEW
# ==================================================================
if page == "📊  Overview":
    st.markdown("# 📊 Overview")

    pf    = _load_pf()
    stats = memory.get_stats()
    cash  = pf.get("cash", VIRTUAL_CAPITAL)
    pnl   = cash - VIRTUAL_CAPITAL
    positions = pf.get("positions", {})

    # KPI row
    k1,k2,k3,k4,k5,k6 = st.columns(6)
    k1.metric("Portfolio",      f"Rs.{cash:,.0f}")
    k2.metric("Total P&L",      f"Rs.{pnl:+,.0f}",
              delta=f"{pnl/VIRTUAL_CAPITAL*100:+.2f}%")
    k3.metric("Open Positions", len(positions))
    k4.metric("Total Trades",   stats["total_trades"])
    k5.metric("Win Rate",       f"{stats['win_rate_pct']:.1f}%")
    k6.metric("Profit Factor",  f"{stats['profit_factor']:.2f}")

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    left, right = st.columns([1.6, 1])

    with left:
        # Market regime
        reg = _load_json("logs/market_regime.json")
        if reg:
            regime = reg.get("regime","unknown")
            css_cls = {"bull":"regime-bull","bear":"regime-bear"}.get(regime,"regime-side")
            icons   = {"bull":"▲","bear":"▼","sideways":"◆"}
            allow   = regime != "bear"
            st.markdown(f"""
            <div class="{css_cls}">
                {icons.get(regime,"◆")} NIFTY {regime.upper()} &nbsp;|&nbsp;
                RSI {reg.get("rsi",0):.1f} &nbsp;|&nbsp;
                1M Return {reg.get("ret_1m",0):+.1f}% &nbsp;|&nbsp;
                {"✓ Trades allowed" if allow else "✗ Trades blocked"}
            </div>
            """, unsafe_allow_html=True)
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        # Equity curve
        snaps = memory.get_snapshots()
        if snaps:
            st.markdown("#### Equity Curve")
            df_s = pd.DataFrame(snaps)
            color_line = "#00d4aa" if pnl >= 0 else "#ef4444"
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df_s["timestamp"], y=df_s["portfolio_value"],
                mode="lines", line=dict(color=color_line, width=2),
                fill="tozeroy",
                fillcolor=f"rgba({'0,212,170' if pnl>=0 else '239,68,68'},0.06)",
            ))
            fig.add_hline(y=VIRTUAL_CAPITAL, line_dash="dash",
                          line_color="#1e2d4a", line_width=1,
                          annotation_text="Start", annotation_font_color="#64748b")
            fig.update_layout(
                height=240, margin=dict(l=0,r=0,t=10,b=0),
                plot_bgcolor="#0a0e1a", paper_bgcolor="#0a0e1a",
                xaxis=dict(gridcolor="#1e2d4a", color="#64748b", showgrid=False),
                yaxis=dict(gridcolor="#1e2d4a", color="#64748b"),
                font=dict(family="JetBrains Mono", color="#64748b"),
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.markdown("""
            <div class='card'>
                <div class='card-header'>Equity Curve</div>
                <div style='text-align:center;padding:40px;color:#64748b;
                            font-family:JetBrains Mono;font-size:12px;'>
                    No data yet — run the agent to start building history
                </div>
            </div>
            """, unsafe_allow_html=True)

        # Recent signals
        st.markdown("#### Recent Signals")
        sigs = memory.get_recent_signals(limit=5)
        if sigs:
            for s in sigs:
                ep  = s.get("entry_price") or 0
                act = s.get("action","")
                css = "sig-action-buy" if act=="BUY" else "sig-action-sell"
                st.markdown(f"""
                <div style='display:flex;align-items:center;gap:12px;padding:10px 14px;
                            background:#111827;border-radius:8px;margin-bottom:6px;
                            border:1px solid #1e2d4a;'>
                    <span style='font-family:JetBrains Mono;font-weight:700;
                                 color:#f1f5f9;font-size:14px;min-width:100px;'>{s['symbol']}</span>
                    <span class='{css}'>{act}</span>
                    <span style='font-family:JetBrains Mono;font-size:12px;color:#64748b;'>
                        {s['confidence']:.0%} conf</span>
                    <span style='font-family:JetBrains Mono;font-size:12px;color:#64748b;'>
                        Rs.{ep:,.0f}</span>
                    <span style='font-family:JetBrains Mono;font-size:11px;color:#475569;
                                 margin-left:auto;'>{s['timestamp'][:16]}</span>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No signals yet — run the agent from 🚀 Run Agent")

    with right:
        # Readiness gauge
        st.markdown("#### Phase 2 Readiness")
        r = _load_json("logs/readiness_report.json")
        if r:
            passed = r.get("passed",0)
            total  = r.get("total",8)
            pct    = passed/total
            gauge_color = "#00d4aa" if pct==1 else ("#f59e0b" if pct > 0.5 else "#ef4444")
            fig = go.Figure(go.Indicator(
                mode="gauge+number",
                value=pct*100,
                number={"suffix":"%","font":{"size":36,"family":"JetBrains Mono",
                                             "color":"#f1f5f9"}},
                gauge={
                    "axis":  {"range":[0,100],"tickcolor":"#64748b",
                              "tickfont":{"size":10,"color":"#64748b"}},
                    "bar":   {"color":gauge_color,"thickness":0.7},
                    "bgcolor":"#111827",
                    "borderwidth":0,
                    "steps":[
                        {"range":[0,50],  "color":"rgba(239,68,68,0.05)"},
                        {"range":[50,75], "color":"rgba(245,158,11,0.05)"},
                        {"range":[75,100],"color":"rgba(0,212,170,0.05)"},
                    ],
                },
                title={"text":f"Gates Passed {passed}/{total}",
                       "font":{"size":12,"color":"#64748b","family":"JetBrains Mono"}},
            ))
            fig.update_layout(height=220, margin=dict(l=20,r=20,t=30,b=0),
                              paper_bgcolor="#0a0e1a", plot_bgcolor="#0a0e1a",
                              font=dict(family="JetBrains Mono"))
            st.plotly_chart(fig, use_container_width=True)
            days = r.get("days_remaining")
            if r.get("is_ready"):
                st.markdown('<div class="regime-bull">✓ All gates passed — Ready for Phase 2</div>',
                            unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="regime-side">~ {days} more trading days</div>'
                            if days else '<div class="regime-side">Keep trading daily</div>',
                            unsafe_allow_html=True)
        else:
            st.info("Run 🚦 Readiness check to see status")

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        # Quick stats
        st.markdown("#### Performance")
        stat_items = [
            ("Win Rate",       f"{stats['win_rate_pct']:.1f}%",
             stats['win_rate_pct'] >= 52),
            ("Profit Factor",  f"{stats['profit_factor']:.2f}",
             stats['profit_factor'] >= 1.2),
            ("Avg Win",        f"Rs.{stats['avg_win']:,.0f}",   True),
            ("Avg Loss",       f"Rs.{stats['avg_loss']:,.0f}",  False),
            ("Max Drawdown",   f"{stats['max_drawdown_pct']:.1f}%",
             stats['max_drawdown_pct'] <= 15),
        ]
        for label, val, good in stat_items:
            vc = "#00d4aa" if good else "#ef4444"
            st.markdown(f"""
            <div style='display:flex;justify-content:space-between;align-items:center;
                        padding:7px 0;border-bottom:1px solid #111827;'>
                <span style='font-size:12px;color:#64748b;'>{label}</span>
                <span style='font-family:JetBrains Mono;font-size:13px;
                             color:{vc};font-weight:600;'>{val}</span>
            </div>
            """, unsafe_allow_html=True)

        # Quick action
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        if st.button("▶  Run Agent Now", type="primary", use_container_width=True):
            with st.spinner("Running..."):
                from main import run_agent
                sigs = run_agent(dry_run=True)
                st.success(f"Done — {len(sigs)} signals")
                time.sleep(1); st.rerun()

# ==================================================================
# PAGE: SIGNALS
# ==================================================================
elif page == "🎯  Signals":
    st.markdown("# 🎯 Trade Signals")

    c1,c2,c3 = st.columns([2,1,1])
    auto = c1.toggle("Auto-refresh every 60s", value=False)
    if c2.button("▶ Run Scan Now", type="primary"):
        with st.spinner("Scanning..."):
            from main import run_agent
            run_agent(dry_run=True)
            st.rerun()
    if c3.button("🔄 Refresh"):
        st.rerun()

    signals   = memory.get_recent_signals(limit=100)
    today     = datetime.now().strftime("%Y-%m-%d")
    today_sig = [s for s in signals if s["timestamp"].startswith(today) and s["action"]=="BUY"]
    if not today_sig:
        today_sig = [s for s in signals if s["action"]=="BUY"][:10]

    if today_sig:
        k1,k2,k3,k4 = st.columns(4)
        k1.metric("Buy Signals",   len(today_sig))
        k2.metric("Avg Confidence",f"{sum(s['confidence'] for s in today_sig)/len(today_sig):.0%}")
        k3.metric("Avg TA Score",  f"{sum(s['ta_score'] for s in today_sig)/len(today_sig):.1f}/10")
        k4.metric("Pos Sentiment", sum(1 for s in today_sig if s["sentiment"]=="positive"))
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        for sig in today_sig[:TOP_N_SIGNALS]:
            ep  = sig.get("entry_price") or 0
            sl  = sig.get("stop_loss")   or 0
            tp  = sig.get("take_profit") or 0
            qty = sig.get("position_size") or 0
            sl_d = f"{((sl-ep)/ep*100):.1f}%" if ep else "--"
            tp_d = f"{((tp-ep)/ep*100):.1f}%" if ep else "--"
            conf_w = int(sig['confidence']*100)
            ta_w   = int(sig['ta_score']/10*100)

            st.markdown(f"""
            <div class="sig-card">
                <div class="sig-header">
                    <span class="sig-sym">{sig['symbol']}</span>
                    <div style='display:flex;align-items:center;gap:8px;'>
                        <span style='font-family:JetBrains Mono;font-size:11px;color:#64748b;'>
                            {sig['timestamp'][:16]}</span>
                        <span class="sig-action-buy">{sig['action']}</span>
                    </div>
                </div>
                <div class="sig-metrics">
                    <div>
                        <div class="sig-metric-label">Entry</div>
                        <div class="sig-metric-val">Rs.{ep:,.2f}</div>
                    </div>
                    <div>
                        <div class="sig-metric-label">Stop Loss</div>
                        <div class="sig-metric-val" style="color:#ef4444;">
                            Rs.{sl:,.2f}
                            <span style='font-size:10px;color:#64748b;'> {sl_d}</span>
                        </div>
                    </div>
                    <div>
                        <div class="sig-metric-label">Take Profit</div>
                        <div class="sig-metric-val" style="color:#00d4aa;">
                            Rs.{tp:,.2f}
                            <span style='font-size:10px;color:#64748b;'> +{tp_d}</span>
                        </div>
                    </div>
                    <div>
                        <div class="sig-metric-label">Position</div>
                        <div class="sig-metric-val">{qty} shares</div>
                    </div>
                </div>
                <div style='display:flex;gap:16px;margin-top:10px;'>
                    <div style='flex:1;'>
                        <div style='font-size:10px;color:#64748b;margin-bottom:3px;'>
                            Confidence {sig['confidence']:.0%}</div>
                        <div class='prog-wrap'>
                            <div class='prog-fill-{"good" if sig["confidence"]>=0.6 else "bad"}'
                                 style='width:{conf_w}%'></div>
                        </div>
                    </div>
                    <div style='flex:1;'>
                        <div style='font-size:10px;color:#64748b;margin-bottom:3px;'>
                            TA Score {sig['ta_score']:.1f}/10</div>
                        <div class='prog-wrap'>
                            <div class='prog-fill-{"good" if sig["ta_score"]>=6 else "mid"}'
                                 style='width:{ta_w}%'></div>
                        </div>
                    </div>
                    <div style='display:flex;align-items:center;'>
                        <span style='font-size:10px;background:rgba(100,116,139,0.15);
                               color:#94a3b8;padding:2px 8px;border-radius:99px;'>
                            {sig["sentiment"]}</span>
                    </div>
                </div>
                <div class="sig-reason">{sig.get("reasoning","")}</div>
            </div>
            """, unsafe_allow_html=True)

            with st.expander(f"📈 Chart — {sig['symbol']}", expanded=False):
                _chart(sig["symbol"])

    else:
        st.markdown("""
        <div style='text-align:center;padding:60px;color:#64748b;
                    font-family:JetBrains Mono;font-size:13px;'>
            No BUY signals today. Market may be in bear mode.<br>
            <span style='font-size:11px;color:#475569;'>Run the agent to generate fresh signals.</span>
        </div>
        """, unsafe_allow_html=True)

    if auto:
        time.sleep(60); st.rerun()

# ==================================================================
# PAGE: PORTFOLIO
# ==================================================================
elif page == "💼  Portfolio":
    st.markdown("# 💼 Portfolio")

    pf    = _load_pf()
    stats = memory.get_stats()
    cash  = pf.get("cash", VIRTUAL_CAPITAL)
    pnl   = cash - VIRTUAL_CAPITAL
    snaps = memory.get_snapshots()

    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("Portfolio Value", f"Rs.{cash:,.0f}")
    c2.metric("P&L",             f"Rs.{pnl:+,.0f}", delta=f"{pnl/VIRTUAL_CAPITAL*100:+.2f}%")
    c3.metric("Win Rate",        f"{stats['win_rate_pct']:.1f}%")
    c4.metric("Profit Factor",   f"{stats['profit_factor']:.2f}")
    c5.metric("Max Drawdown",    f"{stats['max_drawdown_pct']:.1f}%")

    left_p, right_p = st.columns([3,1])
    with left_p:
        if snaps:
            df_s = pd.DataFrame(snaps)
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df_s["timestamp"], y=df_s["portfolio_value"],
                mode="lines+markers",
                line=dict(color="#00d4aa" if pnl>=0 else "#ef4444", width=2),
                marker=dict(size=4, color="#00d4aa"),
                fill="tozeroy",
                fillcolor="rgba(0,212,170,0.05)",
                name="Portfolio",
            ))
            fig.add_hline(y=VIRTUAL_CAPITAL, line_dash="dash",
                          line_color="#1e2d4a",
                          annotation_text="Start Rs.10L",
                          annotation_font_color="#64748b")
            fig.update_layout(
                height=260, margin=dict(l=0,r=0,t=10,b=0),
                plot_bgcolor="#0a0e1a", paper_bgcolor="#0a0e1a",
                xaxis=dict(gridcolor="#1e2d4a", color="#64748b"),
                yaxis=dict(gridcolor="#1e2d4a", color="#64748b"),
                font=dict(family="JetBrains Mono", color="#64748b"),
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

    with right_p:
        # Win/Loss pie
        wins   = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        if wins + losses > 0:
            fig_pie = go.Figure(go.Pie(
                values=[wins, losses],
                labels=["Wins","Losses"],
                hole=0.65,
                marker_colors=["#00d4aa","#ef4444"],
                textinfo="none",
            ))
            fig_pie.update_layout(
                height=200, margin=dict(l=0,r=0,t=0,b=0),
                paper_bgcolor="#0a0e1a",
                showlegend=True,
                legend=dict(orientation="h", y=-0.1, font=dict(color="#64748b", size=10)),
                annotations=[dict(text=f"{stats['win_rate_pct']:.0f}%",
                                  font_size=22, showarrow=False,
                                  font_color="#f1f5f9",
                                  font_family="JetBrains Mono")],
            )
            st.plotly_chart(fig_pie, use_container_width=True)

    # Performance breakdown
    st.markdown("#### Performance Breakdown")
    p1,p2,p3,p4 = st.columns(4)
    p1.metric("Total Trades",  stats["total_trades"])
    p2.metric("Total P&L",     f"Rs.{stats['total_pnl']:+,.0f}")
    p3.metric("Avg Win",       f"Rs.{stats['avg_win']:,.0f}")
    p4.metric("Avg Loss",      f"Rs.{stats['avg_loss']:,.0f}")

# ==================================================================
# PAGE: POSITIONS
# ==================================================================
elif page == "📈  Positions":
    st.markdown("# 📈 Open Positions")

    pf        = _load_pf()
    positions = pf.get("positions", {})

    c1, c2 = st.columns([3,1])
    if c2.button("🔄 Update Prices + Trailing Stops", type="primary"):
        with st.spinner("Fetching live prices..."):
            from risk.trailing_stop import TrailingStopMonitor
            TrailingStopMonitor().run()
            st.rerun()

    if not positions:
        st.markdown("""
        <div style='text-align:center;padding:80px 40px;'>
            <div style='font-size:48px;margin-bottom:16px;'>📭</div>
            <div style='font-family:JetBrains Mono;font-size:14px;color:#64748b;'>
                No open positions</div>
            <div style='font-size:12px;color:#475569;margin-top:8px;'>
                Agent is fully in cash. Waiting for next signal.</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        # Summary cards
        total_invested = sum(p["entry"]*p["qty"] for p in positions.values())
        total_unrealised = 0
        live_prices = {}

        for sym, p in positions.items():
            curr = _fetch_live_price(sym) or p["entry"]
            live_prices[sym] = curr
            total_unrealised += (curr - p["entry"]) * p["qty"]

        s1,s2,s3,s4 = st.columns(4)
        s1.metric("Open Positions",    len(positions))
        s2.metric("Total Invested",    f"Rs.{total_invested:,.0f}")
        s3.metric("Unrealised P&L",    f"Rs.{total_unrealised:+,.0f}",
                  delta=f"{total_unrealised/total_invested*100:+.1f}%" if total_invested else "0%")
        s4.metric("Avg per Position",  f"Rs.{total_unrealised/len(positions):+,.0f}")

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        # Position cards — sorted by P&L
        sorted_pos = sorted(positions.items(),
                            key=lambda x: (live_prices.get(x[0],x[1]["entry"]) - x[1]["entry"])*x[1]["qty"],
                            reverse=True)

        for sym, p in sorted_pos:
            curr     = live_prices.get(sym, p["entry"])
            pnl_pos  = (curr - p["entry"]) * p["qty"]
            pnl_pct  = (curr - p["entry"]) / p["entry"] * 100
            pnl_cls  = "profit" if pnl_pos > 0 else ("loss" if pnl_pos < 0 else "neutral")
            pnl_col  = "#00d4aa" if pnl_pos > 0 else "#ef4444"
            pnl_icon = "▲" if pnl_pos > 0 else "▼"
            ttype    = p.get("trade_type","swing")
            badge    = f'<span class="pos-badge-{"intra" if ttype=="intraday" else "swing"}">{ttype.upper()}</span>'

            # Progress bar for SL→TP range
            sl_range  = p["take_profit"] - p["stop_loss"]
            curr_prog = (curr - p["stop_loss"]) / sl_range * 100 if sl_range > 0 else 50
            curr_prog = max(0, min(100, curr_prog))
            prog_cls  = "prog-fill-good" if curr_prog > 50 else "prog-fill-bad"

            st.markdown(f"""
            <div class="pos-card {pnl_cls}">
                <div>
                    <div class="pos-sym">{sym} {badge}</div>
                    <div class="pos-type">{p.get("timestamp","")[:10]}</div>
                </div>
                <div>
                    <div class="pos-label">Entry → Current</div>
                    <div class="pos-val">Rs.{p["entry"]:,.2f}</div>
                    <div style='font-family:JetBrains Mono;font-size:13px;
                                color:{pnl_col};font-weight:600;'>
                        Rs.{curr:,.2f} {pnl_icon}</div>
                </div>
                <div>
                    <div class="pos-label">Stop Loss / Take Profit</div>
                    <div style='font-family:JetBrains Mono;font-size:12px;color:#ef4444;'>
                        SL Rs.{p["stop_loss"]:,.2f}</div>
                    <div style='font-family:JetBrains Mono;font-size:12px;color:#00d4aa;'>
                        TP Rs.{p["take_profit"]:,.2f}</div>
                </div>
                <div>
                    <div class="pos-label">Quantity / At Risk</div>
                    <div class="pos-val">{p["qty"]} shares</div>
                    <div style='font-family:JetBrains Mono;font-size:12px;color:#64748b;'>
                        Rs.{p["entry"]*p["qty"]:,.0f} invested</div>
                </div>
                <div>
                    <div class="pos-label">Unrealised P&L</div>
                    <div style='font-family:JetBrains Mono;font-size:16px;
                                color:{pnl_col};font-weight:700;'>
                        Rs.{pnl_pos:+,.0f}</div>
                    <div style='font-family:JetBrains Mono;font-size:12px;color:{pnl_col};'>
                        {pnl_pct:+.2f}%</div>
                </div>
            </div>
            <div style='padding:0 4px 8px;'>
                <div style='display:flex;justify-content:space-between;
                            font-size:10px;color:#475569;font-family:JetBrains Mono;
                            margin-bottom:3px;'>
                    <span>SL Rs.{p["stop_loss"]:,.0f}</span>
                    <span>Current Rs.{curr:,.0f} ({curr_prog:.0f}%)</span>
                    <span>TP Rs.{p["take_profit"]:,.0f}</span>
                </div>
                <div class='prog-wrap'>
                    <div class='{prog_cls}' style='width:{curr_prog:.0f}%'></div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            with st.expander(f"📈 {sym} Chart", expanded=False):
                _chart(sym, period="3mo")

# ==================================================================
# PAGE: TRADES
# ==================================================================
elif page == "📋  Trades":
    st.markdown("# 📋 Trade History")
    trades = memory.get_recent_trades(limit=200)

    if not trades:
        st.info("No closed trades yet. Run the agent without dry-run mode.")
    else:
        df = pd.DataFrame(trades)
        closed = df[df["status"]=="closed"]

        if not closed.empty:
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Total P&L",  f"Rs.{closed['pnl'].sum():+,.0f}")
            c2.metric("Best Trade", f"Rs.{closed['pnl'].max():+,.0f}")
            c3.metric("Worst Trade",f"Rs.{closed['pnl'].min():+,.0f}")
            c4.metric("Avg P&L",    f"Rs.{closed['pnl'].mean():+,.0f}")

            # P&L chart
            fig = go.Figure()
            colors = closed["pnl"].apply(lambda x: "#00d4aa" if x>0 else "#ef4444")
            fig.add_trace(go.Bar(
                x=list(range(len(closed))), y=closed["pnl"],
                marker_color=colors.tolist(), name="P&L"))
            cum = closed["pnl"].cumsum()
            fig.add_trace(go.Scatter(
                x=list(range(len(closed))), y=cum,
                mode="lines", line=dict(color="#f59e0b", width=1.5),
                name="Cumulative", yaxis="y2"))
            fig.update_layout(
                height=260, margin=dict(l=0,r=0,t=10,b=0),
                plot_bgcolor="#0a0e1a", paper_bgcolor="#0a0e1a",
                xaxis=dict(gridcolor="#1e2d4a", color="#64748b"),
                yaxis=dict(gridcolor="#1e2d4a", color="#64748b"),
                yaxis2=dict(overlaying="y", side="right",
                            color="#f59e0b", gridcolor="rgba(0,0,0,0)"),
                legend=dict(orientation="h", font=dict(color="#64748b", size=10)),
                barmode="relative",
                font=dict(family="JetBrains Mono"),
            )
            st.plotly_chart(fig, use_container_width=True)

        # Filter row
        f1,f2 = st.columns(2)
        sf    = f1.selectbox("Status", ["All","open","closed"])
        sym_f = f2.text_input("Symbol", "")

        filtered = df.copy()
        if sf != "All": filtered = filtered[filtered["status"]==sf]
        if sym_f: filtered = filtered[filtered["symbol"].str.contains(sym_f.upper())]

        st.dataframe(
            filtered.style.applymap(
                lambda v: "color: #00d4aa" if isinstance(v, float) and v > 0
                else ("color: #ef4444" if isinstance(v, float) and v < 0 else ""),
                subset=["pnl"] if "pnl" in filtered.columns else []
            ),
            use_container_width=True, height=400
        )

# ==================================================================
# PAGE: BACKTEST
# ==================================================================
elif page == "🔬  Backtest":
    st.markdown("# 🔬 Backtest")
    b1,b2,b3 = st.columns(3)
    bt_sym  = b1.text_input("Symbol","BRITANNIA")
    bt_yr   = b2.selectbox("Years",[1,2,3,5], index=2)
    bt_all  = b3.checkbox("Run top 10 stocks")

    if st.button("▶ Run Backtest", type="primary"):
        syms = (["BRITANNIA","TITAN","BAJFINANCE","HDFCBANK","RELIANCE",
                 "ICICIBANK","SBIN","AXISBANK","INFY","TCS"]
                if bt_all else [bt_sym.upper()])
        with st.spinner(f"Backtesting {len(syms)} stock(s)..."):
            try:
                from backtest.engine import BacktestEngine
                engine = BacktestEngine()
                end    = datetime.today().strftime("%Y-%m-%d")
                start  = (datetime.today()-timedelta(days=365*bt_yr)).strftime("%Y-%m-%d")
                if bt_all:
                    engine.run_all(syms, start, end)
                else:
                    engine.run(bt_sym.upper(), start, end)
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
                df_r = pd.DataFrame(all_r)[
                    ["symbol","total_return_pct","total_trades",
                     "win_rate_pct","max_drawdown_pct","sharpe_ratio","profit_factor"]
                ].sort_values("total_return_pct", ascending=False)
                df_r.columns = ["Symbol","Return %","Trades","Win %","Max DD %","Sharpe","PF"]
                fig = go.Figure(go.Bar(
                    x=df_r["Symbol"], y=df_r["Return %"],
                    marker_color=df_r["Return %"].apply(
                        lambda v: "#00d4aa" if v>0 else "#ef4444").tolist()))
                fig.update_layout(height=260,margin=dict(l=0,r=0,t=10,b=0),
                    plot_bgcolor="#0a0e1a",paper_bgcolor="#0a0e1a",
                    xaxis=dict(color="#64748b"),yaxis=dict(color="#64748b",gridcolor="#1e2d4a"),
                    font=dict(family="JetBrains Mono",color="#64748b"))
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(df_r, use_container_width=True)

# ==================================================================
# PAGE: RUN AGENT
# ==================================================================
elif page == "🚀  Run Agent":
    st.markdown("# 🚀 Run Agent")

    col_r1, col_r2, col_r3 = st.columns(3)
    dry = col_r1.toggle("Dry run", value=True)
    if col_r2.button("▶ Run Full Agent", type="primary", use_container_width=True):
        with st.spinner("Running all 16 pipeline steps..."):
            from main import run_agent
            sigs = run_agent(dry_run=dry)
            st.success(f"Done — {len(sigs)} signals"); st.rerun()
    if col_r3.button("🔄 Refresh", use_container_width=True): st.rerun()

    st.divider()
    st.subheader("🤖 Autonomous Scheduler")
    st.info("Start the autonomous agent to trade automatically every market day.")
    st.code("python -m scheduler.autonomous", language="bash")

    st.divider()
    st.subheader("Individual Modules")

    t1,t2,t3,t4 = st.tabs(["🌍 Market","📊 Analysis","🛡️ Risk","📡 Data"])

    with t1:
        a,b = st.columns(2)
        with a:
            if st.button("▶ Market Regime",use_container_width=True):
                from analysis.market_regime import MarketRegimeFilter
                r = MarketRegimeFilter().get_regime()
                (st.success if r.allow_buys else st.error)(f"{r.regime.upper()} — {r.message}")

            if st.button("▶ PCR Signal",use_container_width=True):
                from analysis.pcr_signal import PCRAnalyser
                r = PCRAnalyser().get_signal()
                st.info(f"PCR {r.pcr:.2f} — {r.message}")
        with b:
            if st.button("▶ FII/DII Flow",use_container_width=True):
                from analysis.fii_dii import FIIDIIAnalyser
                r = FIIDIIAnalyser().get_signal()
                st.info(f"{r.signal.upper()} — {r.message}")

            if st.button("▶ Sector Rotation",use_container_width=True):
                from analysis.sector_rotation import SectorRotationAnalyser
                r = SectorRotationAnalyser().analyse()
                st.info(f"{r.rotation_signal.upper()} — {r.message}")

    with t2:
        sym = st.text_input("Symbol","BRITANNIA",key="analysis_sym")
        a,b,c = st.columns(3)
        with a:
            if st.button("▶ TA",use_container_width=True):
                import yfinance as yf
                df = yf.Ticker(f"{sym}.NS").history(period="400d",interval="1d",auto_adjust=True)
                df.columns=[c.lower() for c in df.columns]
                from analysis.technical_agent import TechnicalAgent
                r = TechnicalAgent().analyse(sym,df)
                if r:
                    st.metric("Score",f"{r.score}/10")
                    st.metric("Signal",r.signal.upper())
        with b:
            if st.button("▶ S/R Levels",use_container_width=True):
                import yfinance as yf
                df = yf.Ticker(f"{sym}.NS").history(period="400d",interval="1d",auto_adjust=True)
                df.columns=[c.lower() for c in df.columns]
                from analysis.support_resistance import SupportResistanceAnalyser
                r = SupportResistanceAnalyser().analyse(sym,df)
                st.metric("SR Score",f"{r.sr_score}/10")
                st.metric("Nearest Sup",f"Rs.{r.nearest_support:,.0f}")
        with c:
            if st.button("▶ Patterns",use_container_width=True):
                import yfinance as yf
                df = yf.Ticker(f"{sym}.NS").history(period="400d",interval="1d",auto_adjust=True)
                df.columns=[c.lower() for c in df.columns]
                from analysis.pattern_recognition import PatternRecogniser
                r = PatternRecogniser().analyse(sym,df)
                st.metric("Pattern Score",f"{r.pattern_score}/10")
                for p in (r.patterns_found or ["None found"]):
                    st.caption(f"• {p}")

    with t3:
        a,b = st.columns(2)
        with a:
            if st.button("▶ Trailing Stops",type="primary",use_container_width=True):
                from risk.trailing_stop import TrailingStopMonitor
                res = TrailingStopMonitor().run()
                st.success(f"Checked {len(res)} positions")

            if st.button("▶ Circuit Breaker",use_container_width=True):
                pf = _load_pf()
                from risk.circuit_breaker import CircuitBreaker
                ok, reason = CircuitBreaker().check(pf["cash"])
                (st.success if ok else st.error)(reason)
        with b:
            if st.button("▶ Daily Report",use_container_width=True):
                from execution.daily_report import DailyReporter
                DailyReporter().send_report()
                st.success("Report sent to Telegram!")

            if st.button("▶ Readiness Check",use_container_width=True):
                from readiness.checker import ReadinessChecker
                r = ReadinessChecker().check()
                st.success(f"{r.passed_count}/{r.total_gates} gates passed")

    with t4:
        if st.button("▶ Run Backtest (top 5)",use_container_width=True):
            from backtest.engine import BacktestEngine
            engine=BacktestEngine()
            end=(datetime.today()).strftime("%Y-%m-%d")
            start=(datetime.today()-timedelta(days=365*3)).strftime("%Y-%m-%d")
            engine.run_all(["BRITANNIA","TITAN","BAJFINANCE","HDFCBANK","RELIANCE"],start,end)
            st.success("Done!"); st.rerun()

        if st.button("▶ Test Telegram",use_container_width=True):
            from utils.telegram import test_connection
            ok=test_connection()
            (st.success if ok else st.error)("Telegram OK!" if ok else "Failed — check .env")

# ==================================================================
# PAGE: MARKET INTEL
# ==================================================================
elif page == "📡  Market Intel":
    st.markdown("# 📡 Market Intelligence")
    c1,c2 = st.columns(2)
    with c1:
        st.markdown("#### Nifty 50")
        _chart("^NSEI", period="6mo", height=260)
    with c2:
        st.markdown("#### Bank Nifty")
        _chart("^NSEBANK", period="6mo", height=260)

    st.divider()
    a,b,c = st.columns(3)
    with a:
        if st.button("▶ Sector Rotation",use_container_width=True,type="primary"):
            from analysis.sector_rotation import SectorRotationAnalyser
            r = SectorRotationAnalyser().analyse()
            if r.sector_returns:
                df_s=pd.DataFrame(list(r.sector_returns.items()),columns=["Sector","Return %"])
                df_s=df_s.sort_values("Return %",ascending=False)
                fig=go.Figure(go.Bar(x=df_s["Sector"],y=df_s["Return %"],
                    marker_color=df_s["Return %"].apply(lambda v:"#00d4aa" if v>0 else "#ef4444").tolist()))
                fig.update_layout(height=280,margin=dict(l=0,r=0,t=10,b=0),
                    plot_bgcolor="#0a0e1a",paper_bgcolor="#0a0e1a",
                    xaxis=dict(color="#64748b"),yaxis=dict(color="#64748b",gridcolor="#1e2d4a"),
                    font=dict(family="JetBrains Mono",color="#64748b"))
                st.plotly_chart(fig,use_container_width=True)
    with b:
        if st.button("▶ PCR + FII",use_container_width=True,type="primary"):
            from analysis.pcr_signal import PCRAnalyser
            from analysis.fii_dii import FIIDIIAnalyser
            pcr=PCRAnalyser().get_signal()
            fii=FIIDIIAnalyser().get_signal()
            st.metric("PCR",f"{pcr.pcr:.2f}",delta=pcr.signal.upper())
            st.caption(pcr.message)
            st.metric("FII",f"Rs.{fii.fii_net:+,.0f}Cr",delta=fii.signal.upper())
            st.caption(fii.message)
    with c:
        if st.button("▶ IPO Watch",use_container_width=True,type="primary"):
            from analysis.ipo_alert import IPOAlertSystem
            ipos=IPOAlertSystem().check()
            if ipos:
                for ipo in ipos[:5]:
                    icon="✅" if ipo.watchable else "⏳"
                    st.markdown(f"{icon} **{ipo.symbol}** {ipo.return_from_issue:+.1f}%")
            else:
                st.info("No IPO data")

# ==================================================================
# PAGE: READINESS
# ==================================================================
elif page == "🚦  Readiness":
    st.markdown("# 🚦 Phase 2 Readiness")
    if st.button("🔄 Run Check Now", type="primary"):
        with st.spinner("Checking all 8 gates..."):
            from readiness.checker import ReadinessChecker
            ReadinessChecker().check()
            st.rerun()

    r = _load_json("logs/readiness_report.json")
    if r:
        passed=r.get("passed",0); total=r.get("total",8); pct=passed/total
        if r.get("is_ready"):
            st.markdown('<div class="regime-bull" style="font-size:16px;padding:16px;">✓ GREEN LIGHT — All gates passed. Ready for Phase 2!</div>',
                        unsafe_allow_html=True)
        else:
            days=r.get("days_remaining")
            st.markdown(f'<div class="regime-side">⚡ {passed}/{total} gates passed — {"~"+str(days)+" more days" if days else "keep paper trading daily"}</div>',
                        unsafe_allow_html=True)

        st.markdown("<div style='height:12px'></div>",unsafe_allow_html=True)
        st.caption(f"Last checked: {r.get('timestamp','')}")

        for gate in r.get("gates",[]):
            passed_g = gate["passed"]
            col = "#00d4aa" if passed_g else "#ef4444"
            icon = "✓" if passed_g else "✗"
            pct_g = min(gate["actual"]/gate["required"],1.0) if gate["required"]>0 else 1.0
            if gate["name"] in ("max_drawdown","max_consec_losses"):
                pct_g = 1.0 if passed_g else 0.3
            prog_cls = "prog-fill-good" if passed_g else "prog-fill-bad"

            st.markdown(f"""
            <div style='margin-bottom:14px;'>
                <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;'>
                    <span style='font-size:13px;color:#e2e8f0;'>
                        <span style='color:{col};font-weight:700;margin-right:8px;'>{icon}</span>
                        {gate["label"]}</span>
                    <span style='font-family:JetBrains Mono;font-size:12px;color:{col};font-weight:600;'>
                        {gate["actual"]:.1f} / {gate["required"]:.1f}</span>
                </div>
                <div class='prog-wrap'>
                    <div class='{prog_cls}' style='width:{pct_g*100:.0f}%'></div>
                </div>
                {"<div style='font-size:11px;color:#475569;margin-top:3px;'>↳ "+gate["message"]+"</div>" if not passed_g else ""}
            </div>
            """, unsafe_allow_html=True)

        st.divider()
        st.info(r.get("recommendation",""))

# ==================================================================
# PAGE: SETTINGS
# ==================================================================
elif page == "⚙️  Settings":
    st.markdown("# ⚙️ Settings")
    t1,t2 = st.tabs(["📋 Configuration","🔑 Credentials"])

    with t1:
        try:
            import config as cfg
            c1,c2=st.columns(2)
            with c1:
                st.markdown("**Strategy Parameters**")
                st.code(f"""MIN_TA_SCORE       = {cfg.MIN_TA_SCORE}
MIN_CONFIDENCE     = {cfg.MIN_CONFIDENCE}
TOP_N_SIGNALS      = {cfg.TOP_N_SIGNALS}
TA_WEIGHT          = {cfg.TA_WEIGHT}
SENTIMENT_WEIGHT   = {cfg.SENTIMENT_WEIGHT}
BACKTEST_START     = {cfg.BACKTEST_START_DATE}""", language="python")
            with c2:
                st.markdown("**Risk Parameters**")
                st.code(f"""RISK_PER_TRADE_PCT = {cfg.RISK_PER_TRADE_PCT}
MAX_OPEN_POSITIONS = {cfg.MAX_OPEN_POSITIONS}
REWARD_RISK_RATIO  = {cfg.REWARD_RISK_RATIO}
TRADING_MODE       = {cfg.TRADING_MODE}
VIRTUAL_CAPITAL    = {cfg.VIRTUAL_CAPITAL:,}""", language="python")
        except Exception as e:
            st.error(str(e))
        st.info("Edit config.py in Notepad and restart dashboard to apply changes.")

    with t2:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, KITE_API_KEY
        st.markdown("**Telegram**")
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            st.success(f"✅ Configured — Chat ID: {TELEGRAM_CHAT_ID}")
        else:
            st.error("❌ Not configured")
            st.code("TELEGRAM_BOT_TOKEN=...\nTELEGRAM_CHAT_ID=...")
        if st.button("📱 Test Telegram"):
            from utils.telegram import test_connection
            ok=test_connection()
            (st.success if ok else st.error)("✅ Connected!" if ok else "❌ Failed")

        st.divider()
        st.markdown("**Zerodha Kite**")
        if KITE_API_KEY:
            st.success("✅ API key configured")
        else:
            st.warning("⚠️ Not configured — only needed for live trading")

