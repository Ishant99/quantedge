# =============================================================================
# dashboard/app.py — QuantEdge Pro Dashboard v2
# 5-tab navigation | Copilot/Autopilot toggle | AI signal stories
# Dark terminal aesthetic — professional, data-dense
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import json, time
from datetime import datetime, timedelta
from types import SimpleNamespace
import yfinance as yf

from config import (
    TRADING_MODE, VIRTUAL_CAPITAL, TOP_N_SIGNALS,
    MIN_TA_SCORE, RISK_PER_TRADE_PCT, MAX_OPEN_POSITIONS,
    AGENT_MODE, DASHBOARD_REFRESH_SEC,
)
from memory.portfolio_memory import PortfolioMemory

st.set_page_config(
    page_title="QuantEdge Pro",
    page_icon="Q",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ------------------------------------------------------------------
# Premium CSS — Dark terminal aesthetic
# ------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&family=Space+Grotesk:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Space Grotesk', sans-serif; }
.stApp { background: #0a0e1a; }

section[data-testid="stSidebar"] {
    background: #0d1224 !important;
    border-right: 1px solid #1e2d4a;
}
section[data-testid="stSidebar"] .stRadio label {
    color: #7a8ba6 !important; font-size: 13px !important;
    padding: 6px 0 !important; transition: color 0.2s;
}
section[data-testid="stSidebar"] .stRadio label:hover { color: #00d4aa !important; }

.block-container { padding: 1.5rem 2rem !important; max-width: 1400px; }

h1 { font-family: 'Space Grotesk', sans-serif !important; font-weight: 700 !important;
     color: #ffffff !important; letter-spacing: -0.5px; }
h2 { font-family: 'Space Grotesk', sans-serif !important; font-weight: 600 !important;
     color: #e2e8f0 !important; font-size: 1.1rem !important; }
h3 { color: #94a3b8 !important; font-size: 0.9rem !important; font-weight: 500 !important;
     text-transform: uppercase; letter-spacing: 1px; }

div[data-testid="metric-container"] {
    background: #0d1224 !important; border: 1px solid #1e2d4a !important;
    border-radius: 12px !important; padding: 16px 20px !important;
    position: relative; overflow: hidden;
}
div[data-testid="metric-container"]::before {
    content: ''; position: absolute; top: 0; left: 0;
    width: 3px; height: 100%;
    background: linear-gradient(180deg, #00d4aa, #0066ff);
    border-radius: 12px 0 0 12px;
}
div[data-testid="stMetricLabel"] {
    color: #64748b !important; font-size: 11px !important;
    text-transform: uppercase; letter-spacing: 1px; font-weight: 500 !important;
}
div[data-testid="stMetricValue"] {
    color: #f1f5f9 !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 1.4rem !important; font-weight: 700 !important;
}
div[data-testid="stMetricDelta"] {
    font-family: 'JetBrains Mono', monospace !important; font-size: 12px !important;
}

.stButton > button {
    background: #0d1224 !important; color: #00d4aa !important;
    border: 1px solid #00d4aa !important; border-radius: 8px !important;
    font-family: 'JetBrains Mono', monospace !important; font-size: 12px !important;
    font-weight: 500 !important; letter-spacing: 0.5px; transition: all 0.2s !important;
}
.stButton > button:hover {
    background: #00d4aa !important; color: #0a0e1a !important;
    transform: translateY(-1px); box-shadow: 0 4px 20px rgba(0,212,170,0.3) !important;
}
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #00d4aa, #0066ff) !important;
    color: #fff !important; border: none !important; font-weight: 700 !important;
}
.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #00efc0, #0077ff) !important;
    color: #fff !important; box-shadow: 0 4px 24px rgba(0,212,170,0.4) !important;
}

.card {
    background: #0d1224; border: 1px solid #1e2d4a; border-radius: 16px;
    padding: 20px 24px; margin-bottom: 16px;
}
.card-header {
    font-size: 11px; font-weight: 600; color: #64748b; text-transform: uppercase;
    letter-spacing: 1.5px; margin-bottom: 16px; display: flex; align-items: center; gap: 8px;
}
.card-header::before {
    content: ''; display: inline-block; width: 6px; height: 6px;
    border-radius: 50%; background: #00d4aa; flex-shrink: 0;
}

.pos-card {
    background: #111827; border: 1px solid #1e2d4a; border-radius: 12px;
    padding: 16px 20px; margin-bottom: 10px;
    display: grid; grid-template-columns: 1fr 1fr 1fr 1fr 1fr; gap: 12px;
    align-items: center; transition: border-color 0.2s;
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
.pos-badge-swing { background: rgba(59,130,246,0.15); color: #60a5fa;
                   border: 1px solid rgba(59,130,246,0.3); font-size: 10px;
                   padding: 2px 8px; border-radius: 99px; font-weight: 600; }
.pos-badge-intra { background: rgba(245,158,11,0.15); color: #fbbf24;
                   border: 1px solid rgba(245,158,11,0.3); font-size: 10px;
                   padding: 2px 8px; border-radius: 99px; font-weight: 600; }

.sig-card {
    background: #111827; border: 1px solid #1e2d4a; border-radius: 12px;
    padding: 16px 20px; margin-bottom: 10px; transition: all 0.2s;
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
.sig-story  { margin-top: 8px; padding: 10px 12px; background: rgba(0,212,170,0.04);
              border-left: 2px solid rgba(0,212,170,0.3); border-radius: 0 6px 6px 0;
              font-size: 12px; color: #94a3b8; line-height: 1.7; font-style: italic; }

.prog-wrap { background: #1e2d4a; border-radius: 99px; height: 6px; margin: 6px 0 12px; }
.prog-fill-good { background: linear-gradient(90deg,#00d4aa,#00efc0);
                  border-radius: 99px; height: 6px; transition: width 0.5s; }
.prog-fill-bad  { background: linear-gradient(90deg,#ef4444,#f87171);
                  border-radius: 99px; height: 6px; }
.prog-fill-mid  { background: linear-gradient(90deg,#f59e0b,#fbbf24);
                  border-radius: 99px; height: 6px; }

.regime-bull { background: rgba(0,212,170,0.08); border: 1px solid rgba(0,212,170,0.25);
               border-radius: 10px; padding: 12px 16px; color: #00d4aa;
               font-family: 'JetBrains Mono'; font-size: 13px; font-weight: 500; }
.regime-bear { background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.25);
               border-radius: 10px; padding: 12px 16px; color: #ef4444;
               font-family: 'JetBrains Mono'; font-size: 13px; font-weight: 500; }
.regime-side { background: rgba(245,158,11,0.08); border: 1px solid rgba(245,158,11,0.25);
               border-radius: 10px; padding: 12px 16px; color: #fbbf24;
               font-family: 'JetBrains Mono'; font-size: 13px; font-weight: 500; }

.stDataFrame { background: #0d1224 !important; }

.stTabs [data-baseweb="tab-list"] {
    background: #0d1224 !important; border-bottom: 1px solid #1e2d4a !important; gap: 4px;
}
.stTabs [data-baseweb="tab"] {
    background: transparent !important; color: #64748b !important;
    font-family: 'JetBrains Mono' !important; font-size: 12px !important;
    border-radius: 8px 8px 0 0 !important; padding: 8px 16px !important;
}
.stTabs [aria-selected="true"] {
    background: #111827 !important; color: #00d4aa !important;
    border-bottom: 2px solid #00d4aa !important;
}

.stTextInput input, .stSelectbox select {
    background: #111827 !important; border: 1px solid #1e2d4a !important;
    color: #e2e8f0 !important; border-radius: 8px !important;
    font-family: 'JetBrains Mono', monospace !important;
}

hr { border-color: #1e2d4a !important; }

.stSuccess { background: rgba(0,212,170,0.08) !important;
             border: 1px solid rgba(0,212,170,0.2) !important; border-radius: 8px !important; }
.stError   { background: rgba(239,68,68,0.08) !important;
             border: 1px solid rgba(239,68,68,0.2) !important; border-radius: 8px !important; }
.stInfo    { background: rgba(59,130,246,0.08) !important;
             border: 1px solid rgba(59,130,246,0.2) !important; border-radius: 8px !important; }
.stWarning { background: rgba(245,158,11,0.08) !important;
             border: 1px solid rgba(245,158,11,0.2) !important; border-radius: 8px !important; }

::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: #0a0e1a; }
::-webkit-scrollbar-thumb { background: #1e2d4a; border-radius: 3px; }

.log-box {
    background: #060a12; border: 1px solid #1e2d4a; border-radius: 10px; padding: 14px;
    font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #64748b;
    max-height: 250px; overflow-y: auto; line-height: 1.7;
}

.mode-badge-copilot  { background: rgba(59,130,246,0.15); color: #60a5fa;
                       border: 1px solid rgba(59,130,246,0.35); padding: 3px 10px;
                       border-radius: 99px; font-size: 10px; font-weight: 700; }
.mode-badge-autopilot{ background: rgba(0,212,170,0.15); color: #00d4aa;
                       border: 1px solid rgba(0,212,170,0.35); padding: 3px 10px;
                       border-radius: 99px; font-size: 10px; font-weight: 700; }
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
        if not hist.empty: return float(hist["Close"].iloc[-1])
    except Exception: pass
    return None

def _plotly_base():
    return dict(
        plot_bgcolor="#0a0e1a", paper_bgcolor="#0a0e1a",
        xaxis=dict(gridcolor="#1e2d4a", color="#64748b"),
        yaxis=dict(gridcolor="#1e2d4a", color="#64748b"),
        font=dict(family="JetBrains Mono", color="#64748b"),
        margin=dict(l=0, r=0, t=10, b=0),
    )

def _sig_to_ns(d: dict) -> SimpleNamespace:
    """Convert a stored signal dict into a SimpleNamespace for SignalNarrator."""
    return SimpleNamespace(
        symbol=d.get("symbol", ""),
        action=d.get("action", "HOLD"),
        confidence=d.get("confidence", 0.5),
        entry_price=d.get("entry_price", 0) or 0,
        stop_loss=d.get("stop_loss", 0) or 0,
        take_profit=d.get("take_profit", 0) or 0,
        position_size=d.get("position_size", 0) or 0,
        ta_score=d.get("ta_score", 5),
        sentiment=d.get("sentiment", "neutral"),
        sentiment_score=d.get("sentiment_score", 0),
        raw_ta=d.get("raw_ta", {}),
        reasoning=d.get("reasoning", ""),
    )

@st.cache_resource
def _get_narrator():
    try:
        from analysis.signal_narrator import SignalNarrator
        return SignalNarrator(use_llm=False)  # fast template-only in dashboard
    except Exception:
        return None

def _get_story(sig_dict: dict) -> str:
    narrator = _get_narrator()
    if narrator is None: return ""
    try: return narrator.narrate(_sig_to_ns(sig_dict))
    except Exception: return ""

memory = PortfolioMemory()

# ------------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------------
with st.sidebar:
    st.markdown("""
    <div style='padding:8px 0 20px;border-bottom:1px solid #1e2d4a;margin-bottom:16px;'>
        <div style='font-family:JetBrains Mono;font-size:18px;font-weight:700;
                    color:#f1f5f9;letter-spacing:-0.5px;'>QuantEdge</div>
        <div style='font-size:10px;color:#64748b;text-transform:uppercase;
                    letter-spacing:2px;margin-top:2px;'>Pro Dashboard</div>
    </div>
    """, unsafe_allow_html=True)

    # Trading mode dot
    mode_color = "#00d4aa" if TRADING_MODE == "paper" else "#ef4444"
    st.markdown(f"""
    <div style='display:flex;align-items:center;gap:8px;margin-bottom:10px;'>
        <div style='width:8px;height:8px;border-radius:50%;background:{mode_color};
                    box-shadow:0 0 8px {mode_color};'></div>
        <span style='font-family:JetBrains Mono;font-size:11px;color:{mode_color};
                     font-weight:600;text-transform:uppercase;letter-spacing:1px;'>
            {TRADING_MODE} mode</span>
    </div>
    """, unsafe_allow_html=True)

    # Market regime
    reg = _load_json("logs/market_regime.json")
    if reg:
        regime = reg.get("regime", "unknown")
        rc = {"bull": "#00d4aa", "bear": "#ef4444", "sideways": "#f59e0b"}.get(regime, "#64748b")
        st.markdown(f"""
        <div style='display:flex;align-items:center;gap:8px;margin-bottom:12px;'>
            <div style='width:6px;height:6px;border-radius:50%;background:{rc};'></div>
            <span style='font-family:JetBrains Mono;font-size:11px;color:{rc};font-weight:500;'>
                NIFTY {regime.upper()}</span>
            <span style='font-family:JetBrains Mono;font-size:10px;color:#64748b;margin-left:auto;'>
                RSI {reg.get("rsi",0):.0f}</span>
        </div>
        """, unsafe_allow_html=True)

    # Copilot / Autopilot toggle
    _agent_default = (AGENT_MODE == "autopilot")
    _autopilot_on = st.toggle("Autopilot", value=_agent_default,
                               help="ON = agent executes autonomously | OFF = you approve each trade")
    _effective_mode = "autopilot" if _autopilot_on else "copilot"
    badge_cls = "mode-badge-autopilot" if _autopilot_on else "mode-badge-copilot"
    st.markdown(f"""
    <div style='margin-bottom:14px;'>
        <span class='{badge_cls}'>{_effective_mode.upper()}</span>
        <span style='font-size:10px;color:#475569;margin-left:8px;'>
            {"auto-executes" if _autopilot_on else "awaits approval"}</span>
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    # 5-page navigation
    page = st.radio("", [
        "Today",
        "Portfolio",
        "Research",
        "History",
        "Settings",
    ], label_visibility="collapsed")

    st.divider()

    # Portfolio mini-card
    pf   = _load_pf()
    cash = pf.get("cash", VIRTUAL_CAPITAL)
    pnl  = cash - VIRTUAL_CAPITAL
    pnl_c = "#00d4aa" if pnl >= 0 else "#ef4444"
    pnl_s = "+" if pnl >= 0 else ""
    st.markdown(f"""
    <div style='padding:12px;background:#111827;border-radius:10px;border:1px solid #1e2d4a;'>
        <div style='font-size:10px;color:#64748b;text-transform:uppercase;
                    letter-spacing:1px;margin-bottom:6px;'>Portfolio Value</div>
        <div style='font-family:JetBrains Mono;font-size:20px;font-weight:700;
                    color:#f1f5f9;'>Rs.{cash:,.0f}</div>
        <div style='font-family:JetBrains Mono;font-size:12px;color:{pnl_c};margin-top:4px;'>
            {pnl_s}Rs.{pnl:,.0f} ({pnl/VIRTUAL_CAPITAL*100:+.2f}%)</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    st.caption(f"  {datetime.now().strftime('%d %b %Y  %H:%M')}")


# ==================================================================
# PAGE: TODAY
# ==================================================================
if page == "Today":
    stats = memory.get_stats()
    pf    = _load_pf()
    cash  = pf.get("cash", VIRTUAL_CAPITAL)
    pnl   = cash - VIRTUAL_CAPITAL
    positions = pf.get("positions", {})

    # Header row
    hc1, hc2 = st.columns([3, 1])
    with hc1:
        st.markdown("# Today")
    with hc2:
        if st.button("Run Agent Now", type="primary", use_container_width=True):
            with st.spinner("Running pipeline..."):
                from main import run_agent
                dry = (_effective_mode == "copilot")
                sigs = run_agent(dry_run=dry)
                st.success(f"Done — {len(sigs)} signals")
                time.sleep(1); st.rerun()

    # KPI row
    k1,k2,k3,k4,k5,k6 = st.columns(6)
    k1.metric("Portfolio",      f"Rs.{cash:,.0f}")
    k2.metric("Total P&L",      f"Rs.{pnl:+,.0f}", delta=f"{pnl/VIRTUAL_CAPITAL*100:+.2f}%")
    k3.metric("Open Positions", len(positions))
    k4.metric("Total Trades",   stats["total_trades"])
    k5.metric("Win Rate",       f"{stats['win_rate_pct']:.1f}%")
    k6.metric("Profit Factor",  f"{stats['profit_factor']:.2f}")

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    tab_ov, tab_sig = st.tabs(["  Overview  ", "  Signals  "])

    # ---- Overview sub-tab ----
    with tab_ov:
        left, right = st.columns([1.6, 1])
        with left:
            reg = _load_json("logs/market_regime.json")
            if reg:
                regime = reg.get("regime","unknown")
                css_cls = {"bull":"regime-bull","bear":"regime-bear"}.get(regime,"regime-side")
                icons = {"bull":"▲","bear":"▼","sideways":"◆"}
                allow = regime != "bear"
                st.markdown(f"""
                <div class="{css_cls}">
                    {icons.get(regime,"◆")} NIFTY {regime.upper()} &nbsp;|&nbsp;
                    RSI {reg.get("rsi",0):.1f} &nbsp;|&nbsp;
                    1M Return {reg.get("ret_1m",0):+.1f}% &nbsp;|&nbsp;
                    {"Trades allowed" if allow else "Trades BLOCKED"}
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
                fig.update_layout(height=240, showlegend=False, **_plotly_base())
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No equity data yet — run the agent to build history")

            # Recent signals
            st.markdown("#### Recent Signals")
            sigs = memory.get_recent_signals(limit=5)
            for s in sigs:
                ep  = s.get("entry_price") or 0
                act = s.get("action","")
                css = "sig-action-buy" if act=="BUY" else "sig-action-sell"
                st.markdown(f"""
                <div style='display:flex;align-items:center;gap:12px;padding:10px 14px;
                            background:#111827;border-radius:8px;margin-bottom:6px;
                            border:1px solid #1e2d4a;'>
                    <span style='font-family:JetBrains Mono;font-weight:700;color:#f1f5f9;
                                 font-size:14px;min-width:100px;'>{s['symbol']}</span>
                    <span class='{css}'>{act}</span>
                    <span style='font-family:JetBrains Mono;font-size:12px;color:#64748b;'>
                        {s['confidence']:.0%} conf</span>
                    <span style='font-family:JetBrains Mono;font-size:12px;color:#64748b;'>
                        Rs.{ep:,.0f}</span>
                    <span style='font-family:JetBrains Mono;font-size:11px;color:#475569;
                                 margin-left:auto;'>{s['timestamp'][:16]}</span>
                </div>
                """, unsafe_allow_html=True)
            if not sigs:
                st.info("No signals yet — use Run Agent Now")

        with right:
            # Readiness gauge
            st.markdown("#### System Readiness")
            r = _load_json("logs/readiness_report.json")
            if r:
                passed = r.get("passed", 0); total = r.get("total", 8)
                pct    = passed / total
                gc = "#00d4aa" if pct==1 else ("#f59e0b" if pct > 0.5 else "#ef4444")
                fig = go.Figure(go.Indicator(
                    mode="gauge+number",
                    value=pct*100,
                    number={"suffix":"%","font":{"size":32,"family":"JetBrains Mono","color":"#f1f5f9"}},
                    gauge={
                        "axis": {"range":[0,100],"tickcolor":"#64748b","tickfont":{"size":10,"color":"#64748b"}},
                        "bar": {"color":gc,"thickness":0.7},
                        "bgcolor":"#111827","borderwidth":0,
                        "steps":[
                            {"range":[0,50],"color":"rgba(239,68,68,0.05)"},
                            {"range":[50,75],"color":"rgba(245,158,11,0.05)"},
                            {"range":[75,100],"color":"rgba(0,212,170,0.05)"},
                        ],
                    },
                    title={"text":f"Gates {passed}/{total}","font":{"size":12,"color":"#64748b","family":"JetBrains Mono"}},
                ))
                fig.update_layout(height=200, paper_bgcolor="#0a0e1a",
                                  margin=dict(l=20,r=20,t=30,b=0),
                                  font=dict(family="JetBrains Mono"))
                st.plotly_chart(fig, use_container_width=True)
                if r.get("is_ready"):
                    st.markdown('<div class="regime-bull">All gates passed</div>', unsafe_allow_html=True)
                else:
                    days = r.get("days_remaining")
                    st.markdown(f'<div class="regime-side">~{days} more days</div>'
                                if days else '<div class="regime-side">Keep paper trading</div>',
                                unsafe_allow_html=True)
            else:
                st.info("Run History > Readiness Check to see status")

            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
            st.markdown("#### Performance")
            for label, val, good in [
                ("Win Rate",      f"{stats['win_rate_pct']:.1f}%",     stats['win_rate_pct'] >= 52),
                ("Profit Factor", f"{stats['profit_factor']:.2f}",     stats['profit_factor'] >= 1.2),
                ("Avg Win",       f"Rs.{stats['avg_win']:,.0f}",       True),
                ("Avg Loss",      f"Rs.{stats['avg_loss']:,.0f}",      False),
                ("Max Drawdown",  f"{stats['max_drawdown_pct']:.1f}%", stats['max_drawdown_pct'] <= 15),
            ]:
                vc = "#00d4aa" if good else "#ef4444"
                st.markdown(f"""
                <div style='display:flex;justify-content:space-between;align-items:center;
                            padding:7px 0;border-bottom:1px solid #111827;'>
                    <span style='font-size:12px;color:#64748b;'>{label}</span>
                    <span style='font-family:JetBrains Mono;font-size:13px;color:{vc};font-weight:600;'>{val}</span>
                </div>
                """, unsafe_allow_html=True)

    # ---- Signals sub-tab ----
    with tab_sig:
        c1, c2, c3 = st.columns([2,1,1])
        auto = c1.toggle("Auto-refresh every 60s", value=False)
        if c2.button("Run Scan", type="primary"):
            with st.spinner("Scanning..."):
                from main import run_agent
                run_agent(dry_run=True)
                st.rerun()
        if c3.button("Refresh"):
            st.rerun()

        all_sigs  = memory.get_recent_signals(limit=100)
        today_str = datetime.now().strftime("%Y-%m-%d")
        buy_sigs  = [s for s in all_sigs if s["action"]=="BUY" and s["timestamp"].startswith(today_str)]
        if not buy_sigs:
            buy_sigs = [s for s in all_sigs if s["action"]=="BUY"][:10]

        if buy_sigs:
            k1,k2,k3,k4 = st.columns(4)
            k1.metric("Buy Signals",    len(buy_sigs))
            k2.metric("Avg Confidence", f"{sum(s['confidence'] for s in buy_sigs)/len(buy_sigs):.0%}")
            k3.metric("Avg TA Score",   f"{sum(s['ta_score'] for s in buy_sigs)/len(buy_sigs):.1f}/10")
            k4.metric("Pos Sentiment",  sum(1 for s in buy_sigs if s.get("sentiment")=="positive"))
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            for sig in buy_sigs[:TOP_N_SIGNALS]:
                ep  = sig.get("entry_price") or 0
                sl  = sig.get("stop_loss")   or 0
                tp  = sig.get("take_profit") or 0
                qty = sig.get("position_size") or 0
                sl_d = f"{((sl-ep)/ep*100):.1f}%" if ep else "--"
                tp_d = f"{((tp-ep)/ep*100):.1f}%" if ep else "--"
                conf_w = int(sig["confidence"]*100)
                ta_w   = int(sig["ta_score"]/10*100)
                story  = _get_story(sig)

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
                                {sig.get("sentiment","neutral")}</span>
                        </div>
                    </div>
                    {"<div class='sig-story'>" + story + "</div>" if story else ""}
                    <div class="sig-reason">{sig.get("reasoning","")}</div>
                </div>
                """, unsafe_allow_html=True)

                # Copilot approve/reject buttons
                if _effective_mode == "copilot":
                    ca, cr = st.columns(2)
                    if ca.button(f"Approve — {sig['symbol']}", key=f"app_{sig['symbol']}"):
                        st.success(f"Order queued: BUY {sig['symbol']} x{qty} @ Rs.{ep:,.2f}")
                    if cr.button(f"Skip", key=f"skip_{sig['symbol']}"):
                        st.info(f"{sig['symbol']} skipped")

                with st.expander(f"Chart — {sig['symbol']}", expanded=False):
                    _chart(sig["symbol"])
        else:
            st.markdown("""
            <div style='text-align:center;padding:60px;color:#64748b;
                        font-family:JetBrains Mono;font-size:13px;'>
                No BUY signals today.<br>
                <span style='font-size:11px;color:#475569;'>
                    Run the agent to generate fresh signals.</span>
            </div>
            """, unsafe_allow_html=True)

        if auto:
            time.sleep(60); st.rerun()


# ==================================================================
# PAGE: PORTFOLIO
# ==================================================================
elif page == "Portfolio":
    st.markdown("# Portfolio")
    stats = memory.get_stats()
    pf    = _load_pf()
    cash  = pf.get("cash", VIRTUAL_CAPITAL)
    pnl   = cash - VIRTUAL_CAPITAL
    snaps = memory.get_snapshots()
    positions = pf.get("positions", {})

    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("Portfolio Value", f"Rs.{cash:,.0f}")
    c2.metric("P&L",             f"Rs.{pnl:+,.0f}", delta=f"{pnl/VIRTUAL_CAPITAL*100:+.2f}%")
    c3.metric("Win Rate",        f"{stats['win_rate_pct']:.1f}%")
    c4.metric("Profit Factor",   f"{stats['profit_factor']:.2f}")
    c5.metric("Max Drawdown",    f"{stats['max_drawdown_pct']:.1f}%")

    tab_eq, tab_pos = st.tabs(["  Equity & Stats  ", "  Open Positions  "])

    with tab_eq:
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
                    fill="tozeroy", fillcolor="rgba(0,212,170,0.05)", name="Portfolio",
                ))
                fig.add_hline(y=VIRTUAL_CAPITAL, line_dash="dash",
                              line_color="#1e2d4a",
                              annotation_text=f"Start Rs.{VIRTUAL_CAPITAL/1e5:.0f}L",
                              annotation_font_color="#64748b")
                fig.update_layout(height=260, showlegend=False, **_plotly_base())
                st.plotly_chart(fig, use_container_width=True)

                # Drawdown chart
                if len(df_s) > 1:
                    rolling_max = df_s["portfolio_value"].cummax()
                    drawdown    = (df_s["portfolio_value"] - rolling_max) / rolling_max * 100
                    fig_dd = go.Figure()
                    fig_dd.add_trace(go.Scatter(
                        x=df_s["timestamp"], y=drawdown,
                        mode="lines", line=dict(color="#ef4444", width=1.5),
                        fill="tozeroy", fillcolor="rgba(239,68,68,0.06)", name="Drawdown %",
                    ))
                    fig_dd.update_layout(height=120, showlegend=False, **_plotly_base())
                    st.caption("Drawdown %")
                    st.plotly_chart(fig_dd, use_container_width=True)
            else:
                st.info("No equity history yet")

        with right_p:
            wins   = stats.get("wins", 0)
            losses = stats.get("losses", 0)
            if wins + losses > 0:
                fig_pie = go.Figure(go.Pie(
                    values=[wins, losses], labels=["Wins","Losses"],
                    hole=0.65, marker_colors=["#00d4aa","#ef4444"], textinfo="none",
                ))
                fig_pie.update_layout(
                    height=200, paper_bgcolor="#0a0e1a", showlegend=True,
                    legend=dict(orientation="h", y=-0.1, font=dict(color="#64748b", size=10)),
                    annotations=[dict(text=f"{stats['win_rate_pct']:.0f}%",
                                      font_size=22, showarrow=False,
                                      font_color="#f1f5f9", font_family="JetBrains Mono")],
                    margin=dict(l=0,r=0,t=0,b=0),
                )
                st.plotly_chart(fig_pie, use_container_width=True)

        st.markdown("#### Performance Breakdown")
        p1,p2,p3,p4 = st.columns(4)
        p1.metric("Total Trades", stats["total_trades"])
        p2.metric("Total P&L",    f"Rs.{stats['total_pnl']:+,.0f}")
        p3.metric("Avg Win",      f"Rs.{stats['avg_win']:,.0f}")
        p4.metric("Avg Loss",     f"Rs.{stats['avg_loss']:,.0f}")

    with tab_pos:
        c1r, c2r = st.columns([3,1])
        if c2r.button("Update Prices + Trailing Stops", type="primary"):
            with st.spinner("Fetching live prices..."):
                from risk.trailing_stop import TrailingStopMonitor
                TrailingStopMonitor().run()
                st.rerun()

        if not positions:
            st.markdown("""
            <div style='text-align:center;padding:80px 40px;'>
                <div style='font-size:48px;margin-bottom:16px;'>no open positions</div>
                <div style='font-family:JetBrains Mono;font-size:12px;color:#475569;'>
                    Agent is fully in cash. Waiting for next signal.</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            total_invested = sum(p["entry"]*p["qty"] for p in positions.values())
            total_unrealised = 0
            live_prices = {}
            for sym, p in positions.items():
                curr = _fetch_live_price(sym) or p["entry"]
                live_prices[sym] = curr
                total_unrealised += (curr - p["entry"]) * p["qty"]

            s1,s2,s3,s4 = st.columns(4)
            s1.metric("Open Positions",  len(positions))
            s2.metric("Total Invested",  f"Rs.{total_invested:,.0f}")
            s3.metric("Unrealised P&L",  f"Rs.{total_unrealised:+,.0f}",
                      delta=f"{total_unrealised/total_invested*100:+.1f}%" if total_invested else "0%")
            s4.metric("Avg per Position",f"Rs.{total_unrealised/len(positions):+,.0f}")

            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
            sorted_pos = sorted(positions.items(),
                                key=lambda x: (live_prices.get(x[0],x[1]["entry"]) - x[1]["entry"])*x[1]["qty"],
                                reverse=True)

            for sym, p in sorted_pos:
                curr    = live_prices.get(sym, p["entry"])
                pnl_pos = (curr - p["entry"]) * p["qty"]
                pnl_pct = (curr - p["entry"]) / p["entry"] * 100
                pnl_cls = "profit" if pnl_pos > 0 else ("loss" if pnl_pos < 0 else "neutral")
                pnl_col = "#00d4aa" if pnl_pos > 0 else "#ef4444"
                pnl_icon = "▲" if pnl_pos > 0 else "▼"
                ttype   = p.get("trade_type","swing")
                badge   = f'<span class="pos-badge-{"intra" if ttype=="intraday" else "swing"}">{ttype.upper()}</span>'
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
                        <div class="pos-label">Entry / Current</div>
                        <div class="pos-val">Rs.{p["entry"]:,.2f}</div>
                        <div style='font-family:JetBrains Mono;font-size:13px;
                                    color:{pnl_col};font-weight:600;'>
                            Rs.{curr:,.2f} {pnl_icon}</div>
                    </div>
                    <div>
                        <div class="pos-label">SL / TP</div>
                        <div style='font-family:JetBrains Mono;font-size:12px;color:#ef4444;'>
                            SL Rs.{p["stop_loss"]:,.2f}</div>
                        <div style='font-family:JetBrains Mono;font-size:12px;color:#00d4aa;'>
                            TP Rs.{p["take_profit"]:,.2f}</div>
                    </div>
                    <div>
                        <div class="pos-label">Qty / Invested</div>
                        <div class="pos-val">{p["qty"]} shares</div>
                        <div style='font-family:JetBrains Mono;font-size:12px;color:#64748b;'>
                            Rs.{p["entry"]*p["qty"]:,.0f}</div>
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
                        <span>Current ({curr_prog:.0f}%)</span>
                        <span>TP Rs.{p["take_profit"]:,.0f}</span>
                    </div>
                    <div class='prog-wrap'>
                        <div class='{prog_cls}' style='width:{curr_prog:.0f}%'></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                with st.expander(f"Chart — {sym}", expanded=False):
                    _chart(sym, period="3mo")


# ==================================================================
# PAGE: RESEARCH
# ==================================================================
elif page == "Research":
    st.markdown("# Research")

    tab_intel, tab_sector, tab_bt = st.tabs([
        "  Market Intel  ", "  Sector Heatmap  ", "  Backtest  "
    ])

    # ---- Market Intel ----
    with tab_intel:
        c1,c2 = st.columns(2)
        with c1:
            st.markdown("#### Nifty 50")
            _chart("^NSEI", period="6mo", height=240)
        with c2:
            st.markdown("#### Bank Nifty")
            _chart("^NSEBANK", period="6mo", height=240)

        st.divider()

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            if st.button("Sector Rotation", use_container_width=True, type="primary"):
                from analysis.sector_rotation import SectorRotationAnalyser
                r = SectorRotationAnalyser().analyse()
                if r.sector_returns:
                    df_s = pd.DataFrame(list(r.sector_returns.items()),
                                        columns=["Sector","Return %"])
                    df_s = df_s.sort_values("Return %", ascending=False)

                    # Radar chart
                    fig_rad = go.Figure(go.Scatterpolar(
                        r=df_s["Return %"].tolist() + [df_s["Return %"].iloc[0]],
                        theta=df_s["Sector"].tolist() + [df_s["Sector"].iloc[0]],
                        fill="toself",
                        fillcolor="rgba(0,212,170,0.1)",
                        line=dict(color="#00d4aa"),
                    ))
                    fig_rad.update_layout(
                        polar=dict(
                            bgcolor="#0d1224",
                            radialaxis=dict(color="#64748b", gridcolor="#1e2d4a"),
                            angularaxis=dict(color="#94a3b8", gridcolor="#1e2d4a"),
                        ),
                        paper_bgcolor="#0a0e1a",
                        showlegend=False,
                        height=320,
                        margin=dict(l=40,r=40,t=20,b=20),
                    )
                    st.plotly_chart(fig_rad, use_container_width=True)

        with col_b:
            if st.button("PCR + FII/DII", use_container_width=True, type="primary"):
                from analysis.pcr_signal import PCRAnalyser
                from analysis.fii_dii import FIIDIIAnalyser
                pcr = PCRAnalyser().get_signal()
                fii = FIIDIIAnalyser().get_signal()
                st.metric("PCR", f"{pcr.pcr:.2f}", delta=pcr.signal.upper())
                st.caption(pcr.message)
                st.metric("FII Net Flow", f"Rs.{fii.fii_net:+,.0f}Cr", delta=fii.signal.upper())
                st.caption(fii.message)

        with col_c:
            if st.button("IPO Watch", use_container_width=True, type="primary"):
                try:
                    from analysis.ipo_alert import IPOAlertSystem
                    ipos = IPOAlertSystem().check()
                    if ipos:
                        for ipo in ipos[:5]:
                            icon = "[W]" if ipo.watchable else "[P]"
                            st.markdown(f"**{icon} {ipo.symbol}** {ipo.return_from_issue:+.1f}%")
                    else:
                        st.info("No IPO data")
                except Exception as e:
                    st.error(str(e))

    # ---- Sector Heatmap ----
    with tab_sector:
        st.markdown("#### Sector Performance Heatmap")
        if st.button("Refresh Sector Data", type="primary"):
            st.rerun()

        # Try loading from cached sector rotation result
        try:
            from analysis.sector_rotation import SectorRotationAnalyser
            with st.spinner("Loading sector data..."):
                r = SectorRotationAnalyser().analyse()
            if r.sector_returns:
                df_heat = pd.DataFrame([
                    {"Sector": sector, "Return %": ret, "Size": max(abs(ret)*10+5, 5)}
                    for sector, ret in r.sector_returns.items()
                ])
                df_heat["Color"] = df_heat["Return %"].apply(
                    lambda v: "#00d4aa" if v > 1 else ("#ef4444" if v < -1 else "#f59e0b")
                )
                df_heat["Label"] = df_heat.apply(
                    lambda row: f"{row['Sector']}<br>{row['Return %']:+.1f}%", axis=1
                )

                fig_tree = go.Figure(go.Treemap(
                    labels=df_heat["Label"].tolist(),
                    parents=[""] * len(df_heat),
                    values=df_heat["Size"].tolist(),
                    marker=dict(
                        colors=df_heat["Return %"].tolist(),
                        colorscale=[
                            [0.0, "rgba(239,68,68,0.8)"],
                            [0.5, "rgba(30,45,74,0.8)"],
                            [1.0, "rgba(0,212,170,0.8)"],
                        ],
                        showscale=True,
                        colorbar=dict(
                            tickfont=dict(color="#64748b", size=10, family="JetBrains Mono"),
                            title=dict(text="Return %", font=dict(color="#64748b", size=10)),
                        ),
                    ),
                    textfont=dict(color="#f1f5f9", size=12, family="JetBrains Mono"),
                    hovertemplate="<b>%{label}</b><extra></extra>",
                ))
                fig_tree.update_layout(
                    height=420, paper_bgcolor="#0a0e1a",
                    margin=dict(l=0,r=0,t=0,b=0),
                    font=dict(family="JetBrains Mono"),
                )
                st.plotly_chart(fig_tree, use_container_width=True)

                # Table
                df_disp = df_heat[["Sector","Return %"]].sort_values("Return %", ascending=False)
                st.dataframe(df_disp, use_container_width=True, height=300)
            else:
                st.info("No sector data available — run a scan first")
        except Exception as e:
            st.info(f"Sector heatmap requires a scan run first. ({e})")

    # ---- Backtest ----
    with tab_bt:
        b1,b2,b3 = st.columns(3)
        bt_sym  = b1.text_input("Symbol", "BRITANNIA")
        bt_yr   = b2.selectbox("Years", [1,2,3,5], index=2)
        bt_all  = b3.checkbox("Run top 10 stocks")

        c1b, c2b = st.columns(2)
        comm_pct = c1b.slider("Commission %", 0.0, 0.5, 0.03, 0.01)
        slip_pct = c2b.slider("Slippage %",   0.0, 0.5, 0.05, 0.01)

        if st.button("Run Backtest", type="primary"):
            syms = (["BRITANNIA","TITAN","BAJFINANCE","HDFCBANK","RELIANCE",
                     "ICICIBANK","SBIN","AXISBANK","INFY","TCS"]
                    if bt_all else [bt_sym.upper()])
            with st.spinner(f"Backtesting {len(syms)} stock(s)..."):
                try:
                    from backtest.engine import BacktestEngine
                    engine = BacktestEngine()
                    end   = datetime.today().strftime("%Y-%m-%d")
                    start = (datetime.today()-timedelta(days=365*bt_yr)).strftime("%Y-%m-%d")
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
                    btr1, btr2, btr3 = st.tabs(["  Returns  ", "  Win Rate  ", "  Risk  "])
                    df_r = pd.DataFrame(all_r).sort_values("total_return_pct", ascending=False)

                    with btr1:
                        fig_ret = go.Figure(go.Bar(
                            x=df_r["symbol"], y=df_r["total_return_pct"],
                            marker_color=df_r["total_return_pct"].apply(
                                lambda v: "#00d4aa" if v>0 else "#ef4444").tolist(),
                            text=df_r["total_return_pct"].apply(lambda v: f"{v:.1f}%"),
                            textposition="outside",
                            textfont=dict(color="#94a3b8", size=10),
                        ))
                        fig_ret.update_layout(height=280, **_plotly_base())
                        st.plotly_chart(fig_ret, use_container_width=True)

                    with btr2:
                        fig_wr = go.Figure(go.Bar(
                            x=df_r["symbol"], y=df_r["win_rate_pct"],
                            marker_color=df_r["win_rate_pct"].apply(
                                lambda v: "#00d4aa" if v>=52 else "#f59e0b").tolist(),
                            text=df_r["win_rate_pct"].apply(lambda v: f"{v:.1f}%"),
                            textposition="outside",
                            textfont=dict(color="#94a3b8", size=10),
                        ))
                        fig_wr.add_hline(y=52, line_dash="dash", line_color="#64748b", line_width=1,
                                         annotation_text="52% target", annotation_font_color="#64748b")
                        fig_wr.update_layout(height=280, **_plotly_base())
                        st.plotly_chart(fig_wr, use_container_width=True)

                    with btr3:
                        cols_show = ["symbol","total_return_pct","win_rate_pct",
                                     "max_drawdown_pct","sharpe_ratio","profit_factor","total_trades"]
                        avail = [c for c in cols_show if c in df_r.columns]
                        df_disp = df_r[avail].copy()
                        df_disp.columns = [c.replace("_pct","_%").replace("_"," ").title()
                                           for c in avail]
                        st.dataframe(df_disp, use_container_width=True)


# ==================================================================
# PAGE: HISTORY
# ==================================================================
elif page == "History":
    st.markdown("# History")
    tab_trades, tab_ready = st.tabs(["  Trade History  ", "  Readiness  "])

    with tab_trades:
        trades = memory.get_recent_trades(limit=200)
        if not trades:
            st.info("No closed trades yet. Run the agent without dry-run mode.")
        else:
            df = pd.DataFrame(trades)
            closed = df[df["status"]=="closed"]

            if not closed.empty:
                c1,c2,c3,c4 = st.columns(4)
                c1.metric("Total P&L",   f"Rs.{closed['pnl'].sum():+,.0f}")
                c2.metric("Best Trade",  f"Rs.{closed['pnl'].max():+,.0f}")
                c3.metric("Worst Trade", f"Rs.{closed['pnl'].min():+,.0f}")
                c4.metric("Avg P&L",     f"Rs.{closed['pnl'].mean():+,.0f}")

                fig = go.Figure()
                colors = closed["pnl"].apply(lambda x: "#00d4aa" if x>0 else "#ef4444")
                fig.add_trace(go.Bar(
                    x=list(range(len(closed))), y=closed["pnl"],
                    marker_color=colors.tolist(), name="P&L"))
                cum = closed["pnl"].cumsum()
                fig.add_trace(go.Scatter(
                    x=list(range(len(closed))), y=cum, mode="lines",
                    line=dict(color="#f59e0b", width=1.5),
                    name="Cumulative", yaxis="y2"))
                fig.update_layout(
                    height=260, yaxis2=dict(overlaying="y", side="right",
                                            color="#f59e0b", gridcolor="rgba(0,0,0,0)"),
                    legend=dict(orientation="h", font=dict(color="#64748b", size=10)),
                    **_plotly_base()
                )
                st.plotly_chart(fig, use_container_width=True)

            f1, f2 = st.columns(2)
            sf    = f1.selectbox("Status", ["All","open","closed"])
            sym_f = f2.text_input("Symbol", "")
            filtered = df.copy()
            if sf != "All": filtered = filtered[filtered["status"]==sf]
            if sym_f: filtered = filtered[filtered["symbol"].str.contains(sym_f.upper())]

            st.dataframe(
                filtered.style.map(
                    lambda v: "color: #00d4aa" if isinstance(v, float) and v > 0
                    else ("color: #ef4444" if isinstance(v, float) and v < 0 else ""),
                    subset=["pnl"] if "pnl" in filtered.columns else []
                ),
                use_container_width=True, height=400
            )

    with tab_ready:
        if st.button("Run Readiness Check", type="primary"):
            with st.spinner("Checking all gates..."):
                from readiness.checker import ReadinessChecker
                ReadinessChecker().check()
                st.rerun()

        r = _load_json("logs/readiness_report.json")
        if r:
            passed = r.get("passed",0); total = r.get("total",8); pct = passed/total
            if r.get("is_ready"):
                st.markdown('<div class="regime-bull" style="font-size:16px;padding:16px;">All gates passed — Ready for Phase 2!</div>',
                            unsafe_allow_html=True)
            else:
                days = r.get("days_remaining")
                st.markdown(f'<div class="regime-side">{passed}/{total} gates passed — {"~"+str(days)+" more days" if days else "keep paper trading"}</div>',
                            unsafe_allow_html=True)

            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
            st.caption(f"Last checked: {r.get('timestamp','')}")

            for gate in r.get("gates", []):
                passed_g = gate["passed"]
                col = "#00d4aa" if passed_g else "#ef4444"
                icon = "+" if passed_g else "-"
                pct_g = min(gate["actual"]/gate["required"], 1.0) if gate["required"]>0 else 1.0
                prog_cls = "prog-fill-good" if passed_g else "prog-fill-bad"
                st.markdown(f"""
                <div style='margin-bottom:14px;'>
                    <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;'>
                        <span style='font-size:13px;color:#e2e8f0;'>
                            <span style='color:{col};font-weight:700;margin-right:8px;'>[{icon}]</span>
                            {gate["label"]}</span>
                        <span style='font-family:JetBrains Mono;font-size:12px;color:{col};font-weight:600;'>
                            {gate["actual"]:.1f} / {gate["required"]:.1f}</span>
                    </div>
                    <div class='prog-wrap'>
                        <div class='{prog_cls}' style='width:{pct_g*100:.0f}%'></div>
                    </div>
                    {"<div style='font-size:11px;color:#475569;margin-top:3px;'>-> "+gate.get("message","")+"</div>" if not passed_g else ""}
                </div>
                """, unsafe_allow_html=True)

            st.divider()
            st.info(r.get("recommendation",""))


# ==================================================================
# PAGE: SETTINGS
# ==================================================================
elif page == "Settings":
    st.markdown("# Settings")
    tab_params, tab_risk, tab_creds, tab_tools = st.tabs([
        "  Strategy  ", "  Risk  ", "  Credentials  ", "  Tools  "
    ])

    with tab_params:
        import config as cfg
        st.markdown("**Tune strategy parameters below. Changes apply until dashboard restart.**")
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        s1, s2 = st.columns(2)
        with s1:
            new_min_ta = st.slider("Min TA Score (to consider tradeable)",
                                   1.0, 9.0, float(cfg.MIN_TA_SCORE), 0.5)
            new_min_conf = st.slider("Min Confidence",
                                     0.3, 0.95, float(cfg.MIN_CONFIDENCE), 0.05)
            new_top_n = st.slider("Top N Signals to show",
                                  1, 20, int(cfg.TOP_N_SIGNALS), 1)
        with s2:
            new_ta_wt = st.slider("TA Weight (vs Sentiment)",
                                  0.0, 1.0, float(cfg.TA_WEIGHT), 0.05)
            new_sent_wt = st.slider("Sentiment Weight",
                                    0.0, 1.0, float(cfg.SENTIMENT_WEIGHT), 0.05)
            new_refresh = st.slider("Dashboard Refresh (sec)",
                                    10, 300, int(cfg.DASHBOARD_REFRESH_SEC), 10)

        st.markdown("#### Current effective values")
        st.code(f"""MIN_TA_SCORE      = {new_min_ta}
MIN_CONFIDENCE    = {new_min_conf}
TOP_N_SIGNALS     = {new_top_n}
TA_WEIGHT         = {new_ta_wt}
SENTIMENT_WEIGHT  = {new_sent_wt}
REFRESH_SEC       = {new_refresh}""", language="python")
        st.info("To persist these changes, edit config.py and restart. "
                "Values above only affect the current session.")

    with tab_risk:
        import config as cfg
        st.markdown("**Risk management parameters**")
        r1, r2 = st.columns(2)
        with r1:
            new_risk_pct = st.slider("Risk per Trade %", 0.5, 5.0,
                                     float(cfg.RISK_PER_TRADE_PCT*100), 0.25)
            new_max_pos  = st.slider("Max Open Positions", 1, 20,
                                     int(cfg.MAX_OPEN_POSITIONS), 1)
            new_rr       = st.slider("Reward/Risk Ratio", 1.0, 5.0,
                                     float(cfg.REWARD_RISK_RATIO), 0.25)
        with r2:
            new_trail    = st.slider("Trailing Stop %", 0.5, 5.0,
                                     float(cfg.TRAIL_PCT*100), 0.25)
            new_max_dd   = st.slider("Max Daily Loss %", 1.0, 10.0,
                                     float(cfg.MAX_DAILY_LOSS_PCT*100), 0.5)
            new_max_sector = st.slider("Max Same Sector Positions", 1, 5,
                                       int(cfg.MAX_SAME_SECTOR), 1)

        st.markdown("#### Current effective values")
        st.code(f"""RISK_PER_TRADE_PCT   = {new_risk_pct/100:.4f}
MAX_OPEN_POSITIONS  = {new_max_pos}
REWARD_RISK_RATIO   = {new_rr}
TRAIL_PCT           = {new_trail/100:.4f}
MAX_DAILY_LOSS_PCT  = {new_max_dd/100:.4f}
MAX_SAME_SECTOR     = {new_max_sector}""", language="python")

    with tab_creds:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, KITE_API_KEY
        st.markdown("**Telegram**")
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            st.success(f"Configured — Chat ID: {TELEGRAM_CHAT_ID}")
        else:
            st.error("Not configured — add to .env file")
            st.code("TELEGRAM_BOT_TOKEN=your_token\nTELEGRAM_CHAT_ID=your_chat_id")
        if st.button("Test Telegram"):
            from utils.telegram import test_connection
            ok = test_connection()
            (st.success if ok else st.error)("Connected!" if ok else "Failed — check .env")

        st.divider()
        st.markdown("**Zerodha Kite**")
        if KITE_API_KEY:
            st.success("API key configured")
        else:
            st.warning("Not configured — only needed for live trading")
            st.code("KITE_API_KEY=your_key\nKITE_API_SECRET=your_secret")

        st.divider()
        st.markdown("**Agent Mode** (current session)")
        st.info(f"Effective mode: **{_effective_mode.upper()}** "
                f"(toggle in sidebar to switch between Copilot and Autopilot)")

    with tab_tools:
        st.markdown("**Individual module testing**")
        t1, t2, t3, t4 = st.tabs(["Market", "Analysis", "Risk", "Data"])

        with t1:
            a, b = st.columns(2)
            with a:
                if st.button("Market Regime", use_container_width=True):
                    from analysis.market_regime import MarketRegimeFilter
                    r = MarketRegimeFilter().get_regime()
                    (st.success if r.allow_buys else st.error)(f"{r.regime.upper()} — {r.message}")
                if st.button("PCR Signal", use_container_width=True):
                    from analysis.pcr_signal import PCRAnalyser
                    r = PCRAnalyser().get_signal()
                    st.info(f"PCR {r.pcr:.2f} — {r.message}")
            with b:
                if st.button("FII/DII Flow", use_container_width=True):
                    from analysis.fii_dii import FIIDIIAnalyser
                    r = FIIDIIAnalyser().get_signal()
                    st.info(f"{r.signal.upper()} — {r.message}")
                if st.button("Sector Rotation", use_container_width=True):
                    from analysis.sector_rotation import SectorRotationAnalyser
                    r = SectorRotationAnalyser().analyse()
                    st.info(f"{r.rotation_signal.upper()} — {r.message}")

        with t2:
            sym_t = st.text_input("Symbol", "BRITANNIA", key="tools_sym")
            a, b, c = st.columns(3)
            with a:
                if st.button("TA", use_container_width=True):
                    df = yf.Ticker(f"{sym_t}.NS").history(period="400d",interval="1d",auto_adjust=True)
                    df.columns = [col.lower() for col in df.columns]
                    from analysis.technical_agent import TechnicalAgent
                    res = TechnicalAgent().analyse(sym_t, df)
                    if res:
                        st.metric("Score", f"{res.score}/10")
                        st.metric("Signal", res.signal.upper())
            with b:
                if st.button("S/R Levels", use_container_width=True):
                    df = yf.Ticker(f"{sym_t}.NS").history(period="400d",interval="1d",auto_adjust=True)
                    df.columns = [col.lower() for col in df.columns]
                    from analysis.support_resistance import SupportResistanceAnalyser
                    res = SupportResistanceAnalyser().analyse(sym_t, df)
                    st.metric("SR Score", f"{res.sr_score}/10")
            with c:
                if st.button("Patterns", use_container_width=True):
                    df = yf.Ticker(f"{sym_t}.NS").history(period="400d",interval="1d",auto_adjust=True)
                    df.columns = [col.lower() for col in df.columns]
                    from analysis.pattern_recognition import PatternRecogniser
                    res = PatternRecogniser().analyse(sym_t, df)
                    st.metric("Pattern Score", f"{res.pattern_score}/10")

        with t3:
            a, b = st.columns(2)
            with a:
                if st.button("Trailing Stops", type="primary", use_container_width=True):
                    from risk.trailing_stop import TrailingStopMonitor
                    res = TrailingStopMonitor().run()
                    st.success(f"Checked {len(res)} positions")
                if st.button("Circuit Breaker", use_container_width=True):
                    pf2 = _load_pf()
                    from risk.circuit_breaker import CircuitBreaker
                    ok, reason = CircuitBreaker().check(pf2["cash"])
                    (st.success if ok else st.error)(reason)
            with b:
                if st.button("Daily Report", use_container_width=True):
                    from execution.daily_report import DailyReporter
                    DailyReporter().send_report()
                    st.success("Report sent to Telegram!")

        with t4:
            if st.button("Run Backtest (top 5)", use_container_width=True):
                from backtest.engine import BacktestEngine
                engine = BacktestEngine()
                end   = datetime.today().strftime("%Y-%m-%d")
                start = (datetime.today()-timedelta(days=365*3)).strftime("%Y-%m-%d")
                engine.run_all(
                    ["BRITANNIA","TITAN","BAJFINANCE","HDFCBANK","RELIANCE"], start, end
                )
                st.success("Done!")
            if st.button("Test Telegram", use_container_width=True):
                from utils.telegram import test_connection
                ok = test_connection()
                (st.success if ok else st.error)("Telegram OK!" if ok else "Failed — check .env")
