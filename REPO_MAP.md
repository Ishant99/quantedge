# QuantEdge — Repository Map

> **Purpose:** Single reference file for any agent or developer. Read this first — you don't need to scan the whole repo.
> **Last verified:** April 2026 | **Branch:** `codex-v2-phase-rollout` (deploy target: `main`)

---

## Architecture Overview

QuantEdge is a multi-market algorithmic trading system running in paper mode on an Oracle Cloud server. A Python `APScheduler` daemon (`scheduler/scheduler.py`) triggers market scans on a cron schedule. Each scan runs a 16-stage pipeline (`main.py`) that fetches OHLCV data, scores stocks through technical analysis, sentiment analysis, and strategy quality scoring, then executes signals via a `PaperExecutor`. All state is persisted to SQLite + JSON files under `logs/`. A Streamlit dashboard, REST API server, Telegram bot, and Discord bot all read from the same state files.

---

## Core Data Flow

```
scheduler/scheduler.py  (APScheduler, cron jobs)
        │
        ▼
main.py run_agent()  — 16-stage NSE pipeline
        │
        ├── data/market_scanner.py        → fetch 491 NSE stocks via yfinance
        ├── analysis/momentum_filter.py   → trend health pre-filter
        ├── analysis/technical_agent.py   → 8 indicators, 10pt score
        ├── analysis/earnings_guard.py    → filter near-earnings stocks
        ├── analysis/support_resistance.py→ S/R levels
        ├── analysis/multi_timeframe.py   → weekly confirmation
        ├── analysis/volume_profile.py    → POC / VWAP analysis
        ├── analysis/pattern_recognition.py → 7 chart patterns
        ├── analysis/sentiment_agent.py   → LLM + keyword + freshness decay
        ├── strategy/engine.py            → weighted signal (TA 50% + Sent 30% + Trend 20%)
        ├── analysis/strategy_quality.py  → historical edge scoring, blocks weak symbols
        ├── risk/dynamic_sizing.py        → ATR-based position sizing
        │
        ▼
execution/executor.py  PaperExecutor.execute()
        │
        ├── logs/virtual_portfolio.json   (cash + open positions)
        ├── logs/trades.db (SQLite)       (signals, trades, snapshots tables)
        └── logs/unified_state.json       (aggregated all-market state)
                │
                ├── utils/telegram.py     → Telegram + Discord alerts
                ├── dashboard/app.py      → Streamlit on port 8501
                └── api/server.py         → REST API on port 8000
```

**Crypto scan:** `data/crypto_scanner.py` → `analysis/technical_agent.py` → `execution/brokers/crypto_paper_broker.py`
**US scan:** `data/us_scanner.py` → `analysis/technical_agent.py` → `execution/brokers/us_paper_broker.py`
**F&O:** `analysis/options_selling.py` + `analysis/futures_signals.py` → `execution/brokers/fno_paper_broker.py`

---

## Markets & Universe

| Market | Scanner | Symbols | Scan Schedule | TP / SL |
|--------|---------|---------|---------------|---------|
| NSE Equities | `data/market_scanner.py` | **491** (486 nse500 + 5 extras) | 09:15 + 15:00 IST Mon-Fri | ATR-based (1.5×ATR SL, 2:1 RR) |
| F&O | `analysis/options_selling.py`, `futures_signals.py` | NIFTY + BANKNIFTY | 09:15 IST + every 15 min | 2× premium TP / 50% SL |
| Crypto | `data/crypto_scanner.py` | **30** USDT pairs | Every 4h, 24/7 (00,04,08,12,16,20 IST) | +8% TP / -4% SL |
| US Stocks | `data/us_scanner.py` | **80** (S&P500 + NASDAQ) | 19:00 IST Mon-Fri | +6% TP / -3% SL |

**NSE data files:**
- `data/nse500_symbols.csv` — 486 stocks, 30 sectors, market cap rank, index membership (primary)
- `data/nse_top200_symbols.csv` — 194 stocks (fallback if nse500 missing)
- `data/nse_watchlist_additions.csv` — 5 extra stocks (BDL, GRSE, ZENTEC, PARAS, DATAPATTERN)

---

## Scheduler Jobs (All 12)

| Job ID | Time | Function | What it does |
|--------|------|----------|-------------|
| `scan_1` | 09:15 IST Mon-Fri | `run_daily_scan()` | Full NSE 16-stage pipeline |
| `scan_2` | 15:00 IST Mon-Fri | `run_daily_scan()` | Afternoon NSE scan |
| `price_monitor` | Every 15 min 09:15–15:00 | `run_price_monitor()` | Check SL/TP on open positions |
| `fno_monitor` | Every 15 min 09:15–15:00 | `run_fno_monitor()` | F&O position auto-exit |
| `intraday_scan` | Hourly 09:30–14:30 | `run_intraday_scan()` | 15-min EMA/VWAP/MACD scalping |
| `eod_close` | 15:25 IST Mon-Fri | `run_eod_close()` | Force-close all intraday positions |
| `outcome_tracker` | 15:30 IST Mon-Fri | `run_outcome_tracker()` | Mark TP_HIT / SL_HIT / EXPIRED |
| `eod_digest` | 18:00 IST Mon-Fri | `run_eod_digest()` | Daily summary Telegram alert |
| `us_scan` | 19:00 IST Mon-Fri | `run_us_scan()` | US stocks full TA scan |
| `crypto_scan` | 00,04,08,12,16,20 IST | `run_crypto_scan()` | Crypto full TA scan |
| `weekly_summary` | Sunday 20:00 | `run_weekly_summary()` | Weekly performance digest |
| `housekeeping` | 06:05 IST daily | `run_housekeeping()` | Trim old logs + cache files |

---

## NSE Signal Pipeline (16 Stages)

| # | Stage | File | Key function |
|---|-------|------|-------------|
| 1 | Circuit breaker | `risk/circuit_breaker.py` | `CircuitBreaker().check()` |
| 2 | Market regime | `analysis/market_regime.py` | `MarketRegimeFilter().get_regime()` |
| 3 | PCR + FII/DII + Sector | `analysis/pcr_signal.py`, `fii_dii.py`, `sector_rotation.py` | `.get_signal()`, `.analyse()` |
| 4 | Market scanner | `data/market_scanner.py` | `MarketScanner().run()` |
| 5 | Momentum filter | `analysis/momentum_filter.py` | `MomentumFilter().filter_all()` |
| 6 | Technical analysis | `analysis/technical_agent.py` | `TechnicalAgent().analyse_all()` |
| 7 | Earnings guard | `analysis/earnings_guard.py` | `EarningsGuard().filter_signals()` |
| 8 | Support/Resistance | `analysis/support_resistance.py` | `SupportResistanceAnalyser().analyse_all()` |
| 9 | Multi-timeframe | `analysis/multi_timeframe.py` | `MultiTimeframeAnalyser().analyse_all()` |
| 10 | Volume profile | `analysis/volume_profile.py` | `VolumeProfileAnalyser().analyse_all()` |
| 11 | Pattern recognition | `analysis/pattern_recognition.py` | `PatternRecogniser().analyse_all()` |
| 12 | Sentiment | `analysis/sentiment_agent.py` | `SentimentAgent().analyse_all()` |
| 13 | Strategy engine | `strategy/engine.py` | `StrategyEngine().generate_all()` |
| 14 | Quality scoring | `analysis/strategy_quality.py` | `StrategyQualityEngine().assess()` |
| 15 | Dynamic sizing | `risk/dynamic_sizing.py` | `DynamicPositionSizer().calculate()` |
| 16 | Execute + persist | `execution/executor.py`, `memory/portfolio_memory.py` | `executor.execute()`, `memory.save_signal()` |

---

## Technical Analysis Indicators

All in `analysis/technical_agent.py`. Score: 0–10 pts (must reach `MIN_TA_SCORE` = 5.0 to be tradeable).

| Indicator | Points | Key logic |
|-----------|--------|-----------|
| RSI(14) | 1.5 | Oversold < 35 = full pts, overbought > 70 = 0 pts |
| MACD(12,26,9) | 1.5 | Bullish crossover + histogram trend |
| EMA 20/50/200 | 2.0 | Price above all 3 = full pts (bull alignment) |
| Bollinger Bands | 1.0 | Near lower band + squeeze |
| Volume breakout | 1.5 | Current vol vs 20-bar avg |
| ADX(14) | 1.0 | ADX ≥ 25 with +DI > -DI = full pts |
| Stochastic(14,3) | 1.0 | %K < 20 oversold turning up = full pts |
| OBV trend | 0.5 | OBV > 10-bar average |

**Tradeability gates** (applied after scoring):
- Bullish signal blocked if `ADX < TA_MIN_TREND_ADX` (default 18.0) — choppy market filter
- Bullish signal blocked if `stoch_k >= TA_MAX_BUY_STOCH` (default 88.0) without crossover — overbought filter

---

## Analysis Modules

| File | Class | Purpose |
|------|-------|---------|
| `analysis/technical_agent.py` | `TechnicalAgent` | 8-indicator TA scoring |
| `analysis/sentiment_agent.py` | `SentimentAgent` | RSS news + LLM (Ollama llama3) + freshness decay |
| `analysis/strategy_quality.py` | `StrategyQualityEngine` | Historical edge scoring per symbol/setup/regime |
| `analysis/pattern_recognition.py` | `PatternRecogniser` | 7 chart patterns (Golden Cross, Double Bottom, Bull Flag, etc.) |
| `analysis/market_regime.py` | `MarketRegimeFilter` | Bull/bear/sideways classification |
| `analysis/momentum_filter.py` | `MomentumFilter` | Trend health pre-filter |
| `analysis/support_resistance.py` | `SupportResistanceAnalyser` | Pivot-based S/R levels |
| `analysis/multi_timeframe.py` | `MultiTimeframeAnalyser` | Weekly bar confirmation |
| `analysis/volume_profile.py` | `VolumeProfileAnalyser` | POC / VWAP analysis |
| `analysis/pcr_signal.py` | `PCRAnalyser` | Put-call ratio signal |
| `analysis/fii_dii.py` | `FIIDIIAnalyser` | FII/DII flow signal |
| `analysis/sector_rotation.py` | `SectorRotationAnalyser` | Sector momentum, position multiplier |
| `analysis/earnings_guard.py` | `EarningsGuard` | Block trades near earnings dates |
| `analysis/outcome_tracker.py` | `OutcomeTracker` | Mark TP/SL/expired on pending signals |
| `analysis/short_signals.py` | `ShortSignalGenerator` | SHORT watchlist in bear regime |
| `analysis/options_selling.py` | `OptionsSeller` | Straddle/strangle signals (NIFTY/BANKNIFTY) |
| `analysis/futures_signals.py` | `FuturesSignalGenerator` | Index futures directional signals |
| `analysis/signal_narrator.py` | `SignalNarrator` | LLM narrative for signal reasoning (optional) |

---

## Execution Layer

| File | Class | Mode | Notes |
|------|-------|------|-------|
| `execution/executor.py` | `PaperExecutor` | Paper | Default. Writes to `virtual_portfolio.json` + SQLite |
| `execution/executor.py` | `LiveExecutor` | Live stub | Zerodha Kite API skeleton. Needs `KITE_API_KEY` + `KITE_API_SECRET` |
| `execution/brokers/fno_paper_broker.py` | `FNOPaperBroker` | Paper | F&O positions in SQLite `fno_trades` table |
| `execution/brokers/crypto_paper_broker.py` | `CryptoPaperBroker` | Paper | Crypto positions in SQLite `crypto_trades` table |
| `execution/brokers/us_paper_broker.py` | `USPaperBroker` | Paper | US positions in SQLite `us_trades` table |
| `execution/intraday_agent.py` | `IntradayAgent` | Paper | 15-min scalping. Loads candidates from `unified_state.json` |
| `execution/price_monitor.py` | — | Paper | SL/TP monitoring for open NSE positions |

`get_executor()` in `executor.py` auto-selects Paper vs Live based on `config.TRADING_MODE`.

---

## Services Layer (`services/`)

| File | Key function | Purpose |
|------|-------------|---------|
| `state_sync.py` | `sync_unified_state()` | Aggregates NSE + F&O + Crypto + US into `logs/unified_state.json` |
| `paper_treasury.py` | `can_allocate()`, `write_treasury_snapshot()` | Enforces NSE 40% / F&O 30% / US 20% / Crypto 10% capital limits |
| `paper_reset.py` | `archive_and_reset_paper_state()` | Archives logs + resets portfolio to fresh state |
| `review_report.py` | `write_review_report()` | Generates `logs/agent_review_report.json` + `.md` |
| `runtime_state.py` | `acquire_pid_file()`, `write_scheduler_status()` | PID locking, job status tracking |
| `dashboard_data.py` | `unified_trade_frame()`, `signal_analytics()` | Data query layer for dashboard |
| `api_data.py` | `overview_payload()`, `portfolio_payload()` | Data formatters for REST API |

---

## Memory & Persistence

| File/Path | Format | Contents |
|-----------|--------|---------|
| `logs/virtual_portfolio.json` | JSON | Cash, open NSE positions, total trades, wins |
| `logs/trades.db` | SQLite | Tables: `signals`, `trades`, `snapshots` |
| `logs/unified_state.json` | JSON | All-market aggregated state (synced after each job) |
| `logs/paper_treasury.json` | JSON | Capital allocation snapshot per market |
| `logs/scheduler_status.json` | JSON | Per-job status, timestamps, heartbeat |
| `logs/scheduler.pid` | Text | PID of running scheduler (prevents duplicate instances) |
| `logs/market_regime.json` | JSON | Latest regime classification |
| `logs/pcr_signal.json` | JSON | Latest PCR signal |
| `logs/fii_signal.json` | JSON | Latest FII/DII signal |
| `logs/market_data/{symbol}.csv` | CSV | Cached OHLCV, refreshed every 24h |
| `logs/chromadb/` | ChromaDB | Semantic search index of trade reasoning |
| `logs/agent_review_report.json` | JSON | Periodic trade review |
| `logs/user_settings.json` | JSON | Dashboard-saved settings (overrides config.py) |

**SQLite schema** (`logs/trades.db`):
- `signals` — every generated signal with TA score, sentiment, quality metadata, setup type
- `trades` — executed trades with entry/exit price, P&L, status (open/closed/intraday)
- `snapshots` — daily portfolio value snapshots for equity curve

---

## Bot Commands

**Telegram** (`telegram/bot.py`) — prefix: `/`
**Discord** (`discord_bot/bot.py`) — prefix: `!`

| Command | Returns |
|---------|---------|
| `/status` / `!status` | Portfolio value, combined P&L all markets, open positions count |
| `/pnl` / `!pnl` | Today's P&L breakdown (NSE / F&O / Crypto / US) |
| `/signals` / `!signals` | Last 5 BUY signals with entry/SL/TP/confidence |
| `/positions` / `!positions` | Open NSE equity positions |
| `/crypto` / `!crypto` | Open crypto positions |
| `/us` / `!us` | Open US stock positions |
| `/fno` / `!fno` | Open F&O positions |
| `/regime` / `!regime` | Market regime + RSI + PCR + FII score |
| `/run` / `!run` | Manually trigger NSE scan |
| `/help` / `!help` | Command list |

Both bots start as daemon threads from `scheduler/scheduler.py` on launch.
Alert fan-out: `utils/telegram.py` sends to Telegram AND Discord on every scheduler alert.

---

## Dashboard Pages (`dashboard/app.py` — Streamlit, port 8501)

| Tab | Key content |
|-----|------------|
| Overview | Portfolio value, all-market P&L strip, open positions, market regime card |
| Signals | Recent signals table, outcome analytics (TP/SL/Expired rates), quality scores |
| Positions | NSE / F&O / Crypto / US open positions with live P&L |
| History | Closed trades log, equity curve chart |
| Analytics | Win rate, Sharpe, profit factor, drawdown, signal quality by setup type |
| Watchlist | Live NSE quotes with TA score badges |
| Settings | All config keys editable (saves to `logs/user_settings.json`) |
| Health | Scheduler PID status, DB health, file freshness, storage usage |

---

## REST API Endpoints (`api/server.py`, port 8000)

| Method | Endpoint | Returns |
|--------|----------|---------|
| GET | `/` | API metadata + available endpoints |
| GET | `/health` | System health (scheduler, DB, token status) |
| GET | `/api/overview` | Portfolio summary (value, P&L, positions) |
| GET | `/api/portfolio` | Positions + cash per market |
| GET | `/api/signals?limit=25` | Recent signals (max 200) |
| GET | `/api/watchlist?limit=8` | Live NSE watchlist (max 50) |
| GET | `/api/activity?limit=12` | Recent trade activity (max 100) |
| GET | `/api/analytics/summary` | Win rate, Sharpe, expectancy |
| GET | `/api/review` | Full trade review (JSON) |
| GET | `/api/review.md` | Full trade review (Markdown) |

**Note:** No systemd service file for API server — must be started manually or added as `trading-api.service`.

---

## Configuration (`config.py` + `settings/manager.py`)

All settings follow this precedence: `logs/user_settings.json` → environment variable → `config.py` default.
Change at runtime via the dashboard Settings tab (persists across restarts).

**Key config groups:**

| Group | Key examples | Defaults |
|-------|-------------|---------|
| Trading mode | `TRADING_MODE`, `AGENT_MODE` | `paper`, `copilot` |
| Capital | `VIRTUAL_CAPITAL`, `PAPER_MAX_ALLOC_NSE_PCT` | ₹10L, 40% |
| TA thresholds | `MIN_TA_SCORE`, `TA_SIGNAL_BULLISH`, `TA_MIN_TREND_ADX` | 5.0, 6.5, 18.0 |
| Risk | `RISK_PER_TRADE_PCT`, `MAX_OPEN_POSITIONS`, `MAX_DAILY_LOSS_PCT` | 2%, 5, 3% |
| Strategy quality | `STRATEGY_QUALITY_MIN_RESOLVED`, `STRATEGY_QUALITY_BLOCK_WEAK_SYMBOLS` | 3, True |
| Intraday | `INTRADAY_MAX_POSITIONS`, `INTRADAY_MIN_CRITERIA`, `INTRADAY_RR` | 4, 3, 1.5 |
| F&O | `FNO_TP_MULT`, `FNO_SL_MULT`, `FNO_MAX_POSITIONS` | 2.0, 0.5, 6 |
| Crypto | `CRYPTO_USDT_PER_TRADE`, `CRYPTO_TP_PCT`, `CRYPTO_SL_PCT` | 100 USDT, 8%, 4% |
| US | `US_USD_PER_TRADE`, `US_TP_PCT`, `US_SL_PCT` | $500, 6%, 3% |
| Sentiment | `SENTIMENT_FRESHNESS_HOURS`, `SENTIMENT_DECAY_FACTOR` | 6h, 0.5 |
| Scheduler | `SCAN_TIME_1`, `SCAN_TIME_2` | 09:15, 15:00 |

---

## Deployment

```
Server:      Oracle Cloud — 144.24.143.86
Project dir: /home/ubuntu/quantedge/
Python:      /usr/bin/python3 (system, no venv)
Branch:      main (auto-deployed via GitHub Actions on push or workflow_dispatch)
```

**Systemd services:**
```
trading-agent.service     — runs scheduler/scheduler.py (ExecStart=/usr/bin/python3 scheduler/scheduler.py)
trading-dashboard.service — runs streamlit dashboard on port 8501
```

**Deploy manually:**
GitHub → Actions → `deploy.yml` → Run workflow → pick branch → confirm

**After deploy, services restart automatically** (Restart=always in systemd).

---

## Known Gaps / Incomplete Modules

| Module | Status | Impact |
|--------|--------|--------|
| `api/server.py` | No systemd service | REST API won't start on Oracle reboot |
| `backtest/optimiser.py` | Skeleton | Backtest engine works; optimizer not wired |
| `analysis/signal_narrator.py` | Optional | LLM narrative in signal reasoning (non-blocking) |
| `analysis/ipo_alert.py` | Sample data only | IPO alerts not implemented |
| Live trading (`LiveExecutor`) | Stub | Zerodha Kite API skeleton; needs auth flow |
