# =============================================================================
# dashboard/app.py — QuantEdge Pro  |  Bloomberg-style terminal
# =============================================================================
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import json, time
from datetime import datetime, timedelta
from types import SimpleNamespace
import yfinance as yf

import settings.manager as S
from memory.portfolio_memory import PortfolioMemory

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
</style>
""", unsafe_allow_html=True)

# =============================================================================
# HELPERS
# =============================================================================
def _load_pf():
    f = "logs/virtual_portfolio.json"
    if os.path.exists(f):
        with open(f) as fp: return json.load(fp)
    vc = _cfg("VIRTUAL_CAPITAL", 1_000_000)
    return {"cash": vc, "positions": {}, "total_trades": 0, "wins": 0}

def _load_json(path, default=None):
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

def _chart(symbol, period="3mo", height=200):
    try:
        df = yf.Ticker(f"{symbol}.NS").history(period=period, interval="1d")
        if df.empty: return
        df["EMA20"] = df["Close"].ewm(span=20).mean()
        df["EMA50"] = df["Close"].ewm(span=50).mean()
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

def _live_price(symbol):
    try:
        h = yf.Ticker(f"{symbol}.NS").history(period="1d", interval="15m")
        return float(h["Close"].iloc[-1]) if not h.empty else None
    except Exception: return None

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
    st.markdown(f"""
    <div style="background:#111111;border:1px solid #1e1e1e;padding:10px;
                border-left:2px solid #FF6B00;">
        <div style="font-size:9px;color:#444;text-transform:uppercase;
                    letter-spacing:1px;margin-bottom:6px;">Portfolio</div>
        <div style="font-size:17px;font-weight:700;color:#eeeeee;">
            Rs.{cash:,.0f}</div>
        <div style="font-size:11px;color:{pc};margin-top:3px;">
            {'+' if pnl>=0 else ''}Rs.{pnl:,.0f} ({pnl/vc*100:+.2f}%)</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div style="font-size:9px;color:#333;margin-top:8px;text-align:right;">
        {datetime.now().strftime('%d %b %Y  %H:%M')}</div>
    """, unsafe_allow_html=True)


# =============================================================================
# PAGE: TODAY
# =============================================================================
if page == "TODAY":
    stats = memory.get_stats()
    pf    = _load_pf()
    cash  = pf.get("cash", _cfg("VIRTUAL_CAPITAL", 1_000_000))
    vc    = _cfg("VIRTUAL_CAPITAL", 1_000_000)
    pnl   = cash - vc
    pos   = pf.get("positions", {})

    # ── Ticker strip ──────────────────────────────────────────────────────────
    reg = _load_json("logs/market_regime.json")
    rsi_val = reg.get("rsi", 0) if reg else 0
    ret_1m  = reg.get("ret_1m", 0) if reg else 0
    pcr_d   = _load_json("logs/pcr_signal.json")
    pcr_val = pcr_d.get("pcr", 0) if pcr_d else 0
    st.markdown(f"""
    <div class="ticker-strip">
        <span>NIFTY50 &nbsp;<span style="color:{'#00C805' if ret_1m>=0 else '#FF3B3B'}">
            {ret_1m:+.2f}%</span></span>
        <span>RSI <span style="color:#FF6B00">{rsi_val:.1f}</span></span>
        <span>PCR <span style="color:#FF6B00">{pcr_val:.2f}</span></span>
        <span>POSITIONS <span style="color:#FF6B00">{len(pos)}</span></span>
        <span>WIN RATE <span style="color:#FF6B00">{stats['win_rate_pct']:.1f}%</span></span>
        <span>P&L <span style="color:{'#00C805' if pnl>=0 else '#FF3B3B'}">{'+' if pnl>=0 else ''}Rs.{pnl:,.0f}</span></span>
    </div>
    """, unsafe_allow_html=True)

    # ── KPI row ───────────────────────────────────────────────────────────────
    k1,k2,k3,k4,k5,k6 = st.columns(6)
    k1.metric("PORTFOLIO",      f"Rs.{cash:,.0f}")
    k2.metric("TOTAL P&L",      f"Rs.{pnl:+,.0f}",     delta=f"{pnl/vc*100:+.2f}%")
    k3.metric("OPEN POS",       len(pos))
    k4.metric("TOTAL TRADES",   stats["total_trades"])
    k5.metric("WIN RATE",       f"{stats['win_rate_pct']:.1f}%")
    k6.metric("PROFIT FACTOR",  f"{stats['profit_factor']:.2f}")

    st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)

    tab_ov, tab_sig = st.tabs(["OVERVIEW", "SIGNALS"])

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
            snaps = memory.get_snapshots()
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
            sigs = memory.get_recent_signals(limit=6)
            if sigs:
                rows = ""
                for s in sigs:
                    ep  = s.get("entry_price") or 0
                    act = s.get("action","")
                    sc  = "#00C805" if act=="BUY" else "#FF3B3B"
                    rows += f"""<div class="bb-row">
                        <span style="color:#eeeeee;font-weight:600;min-width:90px;">{s['symbol']}</span>
                        <span style="color:{sc};font-size:10px;min-width:40px;">{act}</span>
                        <span style="color:#888;font-size:10px;">{s['confidence']:.0%}</span>
                        <span style="color:#cccccc;">Rs.{ep:,.0f}</span>
                        <span style="color:#444;font-size:10px;">{s['timestamp'][:16]}</span>
                    </div>"""
                st.markdown(rows, unsafe_allow_html=True)
            else:
                st.info("No signals yet")

        with right:
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
                st.rerun()
            st.markdown(f"""
            <div style="font-size:10px;color:#444;margin-top:6px;">
                Mode: <span style="color:#FF6B00">{agent_mode.upper()}</span>
                &nbsp;|&nbsp; {'Dry run (no orders)' if dry else 'LIVE orders'}
            </div>
            """, unsafe_allow_html=True)

    # ── Signals ───────────────────────────────────────────────────────────────
    with tab_sig:
        sc1, sc2, sc3 = st.columns([2,1,1])
        auto = sc1.toggle("AUTO-REFRESH 60s", value=False)
        if sc2.button("RUN SCAN", type="primary"):
            with st.spinner("Scanning NSE 500..."):
                from main import run_agent
                run_agent(dry_run=True)
                st.rerun()
        if sc3.button("REFRESH"):
            st.rerun()

        all_sigs  = memory.get_recent_signals(limit=100)
        today_str = datetime.now().strftime("%Y-%m-%d")
        buy_sigs  = [s for s in all_sigs if s["action"]=="BUY" and s["timestamp"].startswith(today_str)]
        if not buy_sigs:
            buy_sigs = [s for s in all_sigs if s["action"]=="BUY"][:15]

        if buy_sigs:
            top_n = int(_cfg("TOP_N_SIGNALS", 10))
            k1,k2,k3,k4 = st.columns(4)
            k1.metric("BUY SIGNALS", len(buy_sigs))
            k2.metric("AVG CONF",    f"{sum(s['confidence'] for s in buy_sigs)/len(buy_sigs):.0%}")
            k3.metric("AVG TA",      f"{sum(s['ta_score'] for s in buy_sigs)/len(buy_sigs):.1f}/10")
            k4.metric("POS SENT",    sum(1 for s in buy_sigs if s.get("sentiment")=="positive"))
            st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)

            for sig in buy_sigs[:top_n]:
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

                with st.expander(f"CHART  {sig['symbol']}", expanded=False):
                    _chart(sig["symbol"])
        else:
            st.markdown("""
            <div style="text-align:center;padding:40px;color:#444;font-size:12px;">
                NO BUY SIGNALS TODAY<br>
                <span style="font-size:10px;color:#333;">Run the agent to generate signals.</span>
            </div>
            """, unsafe_allow_html=True)

        if auto:
            time.sleep(60); st.rerun()


# =============================================================================
# PAGE: PORTFOLIO
# =============================================================================
elif page == "PORTFOLIO":
    stats = memory.get_stats()
    pf    = _load_pf()
    cash  = pf.get("cash", _cfg("VIRTUAL_CAPITAL", 1_000_000))
    vc    = _cfg("VIRTUAL_CAPITAL", 1_000_000)
    pnl   = cash - vc
    snaps = memory.get_snapshots()
    positions = pf.get("positions", {})

    st.markdown('<div class="bb-header" style="font-size:12px;">PORTFOLIO</div>',
                unsafe_allow_html=True)

    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("VALUE",        f"Rs.{cash:,.0f}")
    c2.metric("P&L",          f"Rs.{pnl:+,.0f}", delta=f"{pnl/vc*100:+.2f}%")
    c3.metric("WIN RATE",     f"{stats['win_rate_pct']:.1f}%")
    c4.metric("PROFIT FACTOR",f"{stats['profit_factor']:.2f}")
    c5.metric("MAX DRAWDOWN", f"{stats['max_drawdown_pct']:.1f}%")

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
        if st.button("UPDATE PRICES + TRAILING STOPS", type="primary"):
            with st.spinner("Fetching live prices..."):
                from risk.trailing_stop import TrailingStopMonitor
                TrailingStopMonitor().run()
                st.rerun()

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

            # Header row
            st.markdown("""
            <div class="pos-row" style="color:#444;font-size:10px;text-transform:uppercase;
                                        border-bottom:1px solid #2a2a2a;padding:4px 12px;">
                <span>Symbol</span><span>Entry / Current</span><span>SL / TP</span>
                <span>Qty</span><span>Unrealised P&L</span><span>Progress</span>
            </div>
            """, unsafe_allow_html=True)

            sorted_pos = sorted(positions.items(),
                key=lambda x: (live.get(x[0],x[1]["entry"]) - x[1]["entry"])*x[1]["qty"],
                reverse=True)

            for sym, p in sorted_pos:
                curr    = live.get(sym, p["entry"])
                pnl_pos = (curr - p["entry"]) * p["qty"]
                pnl_pct = (curr - p["entry"]) / p["entry"] * 100
                pc      = "#00C805" if pnl_pos > 0 else "#FF3B3B"
                sl_r    = p["take_profit"] - p["stop_loss"]
                prog    = max(0, min(100, (curr - p["stop_loss"]) / sl_r * 100)) if sl_r > 0 else 50
                pc2     = "#00C805" if prog > 50 else "#FF3B3B"
                st.markdown(f"""
                <div class="pos-row">
                    <span style="color:#eeeeee;font-weight:600;">{sym}</span>
                    <span>
                        <span style="color:#888;font-size:10px;">Rs.{p['entry']:,.2f}</span><br>
                        <span style="color:{pc};">Rs.{curr:,.2f}</span>
                    </span>
                    <span>
                        <span style="color:#FF3B3B;font-size:10px;">SL {p['stop_loss']:,.0f}</span><br>
                        <span style="color:#00C805;font-size:10px;">TP {p['take_profit']:,.0f}</span>
                    </span>
                    <span style="color:#cccccc;">{p['qty']}</span>
                    <span style="color:{pc};font-weight:600;">
                        Rs.{pnl_pos:+,.0f}<br>
                        <span style="font-size:10px;">{pnl_pct:+.2f}%</span>
                    </span>
                    <span>
                        <div style="background:#1e1e1e;height:4px;width:60px;">
                            <div style="background:{pc2};height:4px;width:{prog:.0f}%;"></div>
                        </div>
                        <span style="font-size:9px;color:#555;">{prog:.0f}%</span>
                    </span>
                </div>
                """, unsafe_allow_html=True)

                with st.expander(f"  {sym}", expanded=False):
                    _chart(sym)


# =============================================================================
# PAGE: RESEARCH
# =============================================================================
elif page == "RESEARCH":
    st.markdown('<div class="bb-header" style="font-size:12px;">RESEARCH</div>',
                unsafe_allow_html=True)

    tab_intel, tab_heat, tab_bt, tab_screen = st.tabs([
        "MARKET INTEL", "SECTOR MAP", "BACKTEST", "SCREENER"
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


# =============================================================================
# PAGE: HISTORY
# =============================================================================
elif page == "HISTORY":
    st.markdown('<div class="bb-header" style="font-size:12px;">HISTORY</div>',
                unsafe_allow_html=True)
    tab_tr, tab_rd = st.tabs(["TRADE LOG", "READINESS"])

    with tab_tr:
        trades = memory.get_recent_trades(limit=500)
        if not trades:
            st.info("No closed trades yet")
        else:
            df = pd.DataFrame(trades)
            closed = df[df["status"]=="closed"]

            if not closed.empty:
                c1,c2,c3,c4 = st.columns(4)
                c1.metric("TOTAL P&L",   f"Rs.{closed['pnl'].sum():+,.0f}")
                c2.metric("BEST TRADE",  f"Rs.{closed['pnl'].max():+,.0f}")
                c3.metric("WORST TRADE", f"Rs.{closed['pnl'].min():+,.0f}")
                c4.metric("AVG P&L",     f"Rs.{closed['pnl'].mean():+,.0f}")

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

            f1, f2 = st.columns(2)
            sf    = f1.selectbox("FILTER STATUS", ["All","open","closed"])
            sym_f = f2.text_input("FILTER SYMBOL", "")
            filtered = df.copy()
            if sf != "All": filtered = filtered[filtered["status"]==sf]
            if sym_f: filtered = filtered[filtered["symbol"].str.contains(sym_f.upper())]
            st.dataframe(filtered, use_container_width=True, height=360)

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

    st.info("All settings are saved to logs/user_settings.json on the server. "
            "Changes take effect immediately in the dashboard. "
            "Click RESTART SCHEDULER below to apply changes to the scheduler process.")

    tab_api, tab_mode, tab_strat, tab_risk, tab_sched, tab_tools = st.tabs([
        "API KEYS", "MODE", "STRATEGY", "RISK", "SCHEDULER", "TOOLS"
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

    # ── Risk ──────────────────────────────────────────────────────────────────
    with tab_risk:
        st.markdown('<div class="bb-header">POSITION SIZING & STOPS</div>', unsafe_allow_html=True)
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

    # ── Scheduler ────────────────────────────────────────────────────────────
    with tab_sched:
        st.markdown('<div class="bb-header">SCAN TIMES (IST, MON-FRI)</div>',
                    unsafe_allow_html=True)
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
                "container for changes to take effect.")
        if st.button("RESTART SCHEDULER CONTAINER", use_container_width=True):
            try:
                import subprocess
                result = subprocess.run(
                    ["docker", "restart", "quantedge_scheduler"],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    st.success("Scheduler restarted successfully.")
                else:
                    st.error(f"Restart failed: {result.stderr[:200]}")
            except Exception as e:
                st.error(f"Could not restart: {e}")

        st.markdown('<div class="bb-header" style="margin-top:14px;">CURRENT SETTINGS SUMMARY</div>',
                    unsafe_allow_html=True)
        display = {k: v for k, v in S.all_settings().items()
                   if k not in ("TELEGRAM_BOT_TOKEN","KITE_API_KEY","KITE_API_SECRET")}
        st.json(display)

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
