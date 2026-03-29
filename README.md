# 🤖 NSE Trading Agent

A production-grade AI trading agent for Indian stock markets (NSE/BSE).
Scans 200 stocks daily, generates signals using TA + sentiment + LLM,
and executes trades via Zerodha Kite API.

<!-- CI/CD test: 2026-03-29 -->

---

## 🚀 Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set up environment
```bash
cp .env.example .env
# Edit .env — set TRADING_MODE=paper to start safely
```

### 3. (Optional) Install Ollama for local LLM sentiment
```bash
# Download from https://ollama.ai
ollama pull llama3
```

### 4. Run the agent
```bash
# Dry run — generates signals, no trades placed
python main.py --dry-run

# Paper trading — simulates trades with virtual ₹10L
python main.py

# Launch dashboard
streamlit run dashboard/app.py
```

---

## 📁 Project Structure

```
trading_agent/
├── config.py                  # Master config — change TRADING_MODE here
├── main.py                    # Orchestrator — runs the full pipeline
├── requirements.txt
├── .env.example
│
├── data/
│   ├── market_scanner.py      # M1: Fetches NSE top-200 OHLCV data
│   └── nse_top200_symbols.csv # Watchlist
│
├── analysis/
│   ├── technical_agent.py     # M2: RSI, MACD, EMA, BB, Volume
│   └── sentiment_agent.py     # M3: News RSS + LLM sentiment
│
├── strategy/
│   └── engine.py              # M4+M5: Signal + risk management
│
├── execution/
│   └── executor.py            # M7: Paper or live Zerodha execution
│
├── memory/                    # M6: ChromaDB + SQLite (coming next)
├── backtest/                  # M8: Historical backtesting (coming next)
├── dashboard/                 # M9: Streamlit UI (coming next)
├── scheduler/                 # APScheduler daily jobs (coming next)
└── logs/                      # All output files
```

---

## 🔄 Trading Modes

| Mode | Orders | Data | Use when |
|------|--------|------|----------|
| `paper` | Virtual ₹10L portfolio | yfinance (free) | Learning + validation |
| `live` | Real Zerodha orders | Kite WebSocket | After 4-week paper gate |

Switch by changing `.env`:
```
TRADING_MODE=paper   # safe default
TRADING_MODE=live    # only after paper trading passes gate
```

---

## 📊 Daily Output Example

```
#1  RELIANCE
    Action      : BUY
    Confidence  : 78%
    Entry       : ₹2,847.50
    Stop Loss   : ₹2,790.00
    Take Profit : ₹2,962.00
    Position    : 7 shares
    Capital Risk: ₹402
    TA Score    : 8.2/10
    Sentiment   : positive
    Reason      : 78% confidence. MACD bullish crossover. Positive news sentiment
```

---

## ⚠️ Risk Rules (hardcoded — never bypassed)

- Max **2% of portfolio** risked per trade
- Max **5 open positions** at any time
- Every BUY automatically places a **GTT stop-loss** (live mode)
- Agent **pauses** if portfolio drawdown exceeds 10%

---

## 📅 Roadmap

- [x] M1 — Market Scanner
- [x] M2 — Technical Analysis Agent
- [x] M3 — News Sentiment Agent
- [x] M4+M5 — Strategy Engine + Risk Manager
- [x] M7 — Execution Layer (paper + live)
- [ ] M6 — ChromaDB Portfolio Memory
- [ ] M8 — Backtesting Engine
- [ ] M9 — Streamlit Dashboard + Telegram
- [ ] M10 — Intraday module (Phase 3)
