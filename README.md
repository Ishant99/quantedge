# QuantEdge — Risk-First Portfolio Decision Engine

Algorithmic trading system for Indian equities (NSE) with paper execution, backtesting, signal auditing, and a Streamlit dashboard. Built around a three-layer signal architecture: setup quality → market permission → execution sizing.

**116 Python files · ~30,000 LOC · 191 tests passing**

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [Configuration Reference](#configuration-reference)
5. [Running the System](#running-the-system)
6. [Signal Flow — 9 Pipeline Stages](#signal-flow--9-pipeline-stages)
7. [Three-Layer Signal Architecture](#three-layer-signal-architecture)
8. [Multi-Market Support](#multi-market-support)
9. [Memory & Calibration Feedback Loop](#memory--calibration-feedback-loop)
10. [Backtesting & Ablation](#backtesting--ablation)
11. [Meta-Decision Engine](#meta-decision-engine)
12. [Research vs Production Separation](#research-vs-production-separation)
13. [Dashboard](#dashboard)
14. [Deployment](#deployment)
15. [Known Issues & Audit Findings](#known-issues--audit-findings)
16. [Phase Completion Status](#phase-completion-status)

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and populate environment
cp .env.example .env

# Paper trading dry run (no orders placed)
python main.py --dry-run

# Start full pipeline
python main.py

# Start Streamlit dashboard (port 8501)
streamlit run dashboard/app.py

# Start scheduler daemon
python scheduler/scheduler.py

# Start REST API (port 8000)
python api/server.py

# Run tests
pytest -q
```

---

## Architecture

### Dual-Path Codebase

Two execution paths coexist:

| Path | Entry | Description |
|------|-------|-------------|
| **Legacy** | `main.py → run_agent()` | 16 sequential function calls; production runtime; started by scheduler |
| **Pipeline** | `pipeline/runner.py → TradingPipeline.run()` | 9-stage class-based pipeline with typed I/O contracts; target for new development |

New feature work should target `pipeline/runner.py`.

### Core Signal Flow

```
Stage 1  market_context    → MarketContext (regime, PCR, FII, breadth, sector scores)
Stage 2  data_fetch        → {symbol: DataFrame} via yfinance (491 NSE + extras)
Stage 3  technical         → {symbol: TAResult} — MomentumFilter pre-filters
Stage 3b enrichment        → MTF, patterns, S/R, 52W breakouts, RSI2 (adjusts p_direction)
Stage 4  sentiment         → {symbol: SentimentResult} via RSS + Ollama llama3
Stage 5  signal_gen        → list[TradeSignal] from StrategyEngine
Stage 6  layer2_permission → BLOCK / REDUCE / ALLOW per signal (MarketPermission)
Stage 7  risk_gate         → ABSTAIN signals that fail checks (RiskGate)
Stage 8  sizing            → Allocation with position size (DynamicPositionSizer)
Stage 8b execution_planning→ Candidate competition (ExecutionPlanner ranks + prunes BUYs)
Stage 9  execution         → PaperExecutor or LiveExecutor
```

---

## Project Structure

```
quantedge/
├── main.py                    # Legacy entry point
├── config.py                  # All configuration with _S() override helper
├── run.py                     # Thin wrapper
├── requirements.txt
│
├── pipeline/
│   ├── runner.py              # 9-stage TradingPipeline class
│   └── contracts.py           # MarketContext, Allocation, PipelineResult dataclasses
│
├── analysis/
│   ├── technical_agent.py     # RSI, MACD, EMA, BB, ADX, Stochastic, OBV → TAResult
│   ├── sentiment_agent.py     # RSS feeds + Ollama LLM → SentimentResult
│   ├── market_regime.py       # Nifty-based regime detection (bull/bear/sideways/recovery)
│   ├── market_breadth.py      # Advance/decline breadth signal
│   ├── pcr_signal.py          # Put-call ratio signal
│   ├── fii_dii.py             # FII/DII flow tracker
│   ├── sector_rotation.py     # Sector momentum scores
│   ├── earnings_guard.py      # Block trades within earnings window
│   ├── fno_ban.py             # F&O ban filter
│   ├── multi_timeframe.py     # MTF confluence analysis
│   ├── pattern_recognition.py # Candlestick pattern detector
│   ├── support_resistance.py  # S/R level detector
│   ├── breakout_52w.py        # 52-week high/low breakout scanner
│   ├── rsi2_strategy.py       # RSI-2 mean reversion signals
│   ├── momentum_filter.py     # Pre-filters low-momentum symbols
│   ├── calibration.py         # ConfidenceCalibrator + CalibrationReport
│   ├── market_scanner.py      # (in data/) NSE symbol scanner
│   └── signal_narrator.py     # Optional LLM narrative for signal reasoning
│
├── strategy/
│   ├── engine.py              # StrategyEngine — 3-layer signal + TradeSignal
│   ├── market_permission.py   # Layer 2: BLOCK / REDUCE / ALLOW gate
│   ├── abstention.py          # Soft abstention evaluator
│   ├── execution_planner.py   # Candidate competition + slot allocation
│   ├── decision_journal.py    # Per-signal audit trail (DecisionJournal)
│   └── regime_weights.py      # Regime-conditional weights + RegimeWeightManager
│
├── risk/
│   ├── risk_gate.py           # RiskGate — hard blocks (EV, confidence, position limits)
│   └── dynamic_sizing.py      # DynamicPositionSizer — ATR + VIX + Kelly + regime
│
├── execution/
│   ├── executor.py            # PaperExecutor + LiveExecutor (Zerodha Kite stub)
│   ├── portfolio_lock.py      # File-based portfolio mutex
│   └── brokers/
│       ├── fno_paper_broker.py
│       ├── us_paper_broker.py
│       └── crypto_paper_broker.py
│
├── backtest/
│   ├── engine.py              # BacktestEngine — full pipeline replay, stress test
│   ├── ablation.py            # AblationRunner — module contribution analysis
│   └── drift_analysis.py      # DriftAnalyser — live vs backtest performance drift
│
├── memory/
│   └── portfolio_memory.py    # PortfolioMemory — SQLite persistence layer
│
├── research/
│   ├── sandbox_pipeline.py    # Isolated research pipeline (writes to research.db only)
│   ├── promotion_checklist.py # PromotionChecker.evaluate() + PromotionChecklist gates
│   ├── experiments/           # New signals/analyzers under test
│   ├── ablations/             # Ablation results JSON files
│   └── notebooks/             # Analysis notebooks
│
├── readiness/
│   └── checker.py             # ReadinessChecker — go/no-go report + asset class gates
│
├── data/
│   ├── market_scanner.py      # NSE 491-symbol OHLCV downloader
│   ├── us_scanner.py          # US equities scanner
│   ├── crypto_scanner.py      # Crypto scanner
│   └── nse500_historical_membership.csv  # Survivorship bias data
│
├── services/
│   ├── paper_treasury.py      # Capital allocation across markets
│   └── state_sync.py          # Aggregates all markets → unified_state.json
│
├── dashboard/
│   └── app.py                 # Streamlit dashboard (~4,200 lines)
│
├── scheduler/
│   └── scheduler.py           # APScheduler daemon — 24 scheduled jobs
│
├── api/
│   └── server.py              # FastAPI REST webhooks (TradingView alerts)
│
├── settings/
│   └── manager.py             # Runtime config overrides from user_settings.json
│
├── telegram/
│   └── bot.py                 # Telegram bot: /status /pnl /signals /positions /crypto /us /fno /regime /vix /run
│
├── discord_bot/
│   └── bot.py                 # Discord bot
│
├── tests/
│   ├── test_signal_layers.py
│   ├── test_risk_gate.py
│   ├── test_abstention.py
│   ├── test_pipeline_integration.py
│   ├── test_dynamic_sizing.py
│   ├── test_execution_planner.py
│   ├── test_portfolio_memory.py
│   ├── test_paper_brokers.py
│   ├── test_confidence_calibrator.py
│   ├── test_drift_analyser.py
│   └── test_market_permission.py
│
└── logs/                      # Runtime state (not committed)
    ├── trades.db              # SQLite: signals, trades, journals, calibration
    ├── virtual_portfolio.json # Cash + open NSE positions
    ├── unified_state.json     # All-market aggregated state
    ├── user_settings.json     # Runtime overrides (written by dashboard)
    └── market_data/           # Cached OHLCV, 24h TTL
```

---

## Configuration Reference

All settings use the `_S("KEY", default=value)` helper in `config.py`. Override priority: `logs/user_settings.json` → `.env` → `config.py` default.

### Core Trading

| Key | Default | Description |
|-----|---------|-------------|
| `TRADING_MODE` | `paper` | `paper` or `live` |
| `VIRTUAL_CAPITAL` | `1000000` | Starting capital (INR) |
| `MIN_CONFIDENCE` | `0.60` | Minimum p_direction to emit BUY |
| `SELL_CONFIDENCE` | `0.40` | Maximum p_direction to emit SELL |
| `MAX_OPEN_POSITIONS` | `5` | Maximum concurrent positions |
| `RISK_PER_TRADE_PCT` | `0.02` | 2% of capital at risk per trade |
| `REWARD_RISK_RATIO` | `2.0` | Take-profit multiplier vs stop-loss distance |
| `ATR_SL_MULTIPLIER` | `1.5` | ATR multiplier for stop-loss |

### Signal Generation

| Key | Default | Description |
|-----|---------|-------------|
| `TA_WEIGHT` | `0.50` | Technical analysis weight (Layer 1) |
| `TREND_WEIGHT` | `0.20` | Trend strength weight (Layer 1) |
| `MIN_TA_SCORE` | `5.0` | Minimum TA score (0–10) to be tradeable |
| `TA_SIGNAL_BULLISH` | `6.5` | TA score threshold for BUY signal |
| `TA_SIGNAL_BEARISH` | `4.0` | TA score threshold for SELL signal |
| `TOP_N_SIGNALS` | `10` | Max BUY signals to pass to execution |

### Risk Gate

| Key | Default | Description |
|-----|---------|-------------|
| `RISK_GATE_MIN_CONFIDENCE` | `0.55` | Hard floor confidence for RiskGate |
| `MAX_POSITION_RISK_PCT` | `0.05` | Max 5% of portfolio in a single position |
| `THESIS_DROP_SELL_PCT` | `0.20` | Exit held position if confidence drops 20% |

### Asset Class Gates

| Key | Default | Description |
|-----|---------|-------------|
| `ASSET_NSE_SPOT_ENABLED` | `true` | NSE spot equities enabled |
| `ASSET_FNO_ENABLED` | `false` | F&O gated until Phase 6 complete |
| `ASSET_CRYPTO_ENABLED` | `false` | Crypto gated until Phase 6 complete |
| `ASSET_US_ENABLED` | `false` | US equities gated until Phase 6 complete |

### Alerts

| Key | Default | Description |
|-----|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | `""` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | `""` | Telegram chat ID |
| `DISCORD_BOT_TOKEN` | `""` | Discord bot token |
| `DISCORD_CHANNEL_ID` | `""` | Discord channel ID |

---

## Running the System

### One-off scan

```bash
python main.py --dry-run          # scan, log, no orders
python main.py                    # scan + execute paper trades
```

### Dashboard

```bash
streamlit run dashboard/app.py    # http://localhost:8501
```

Dashboard pages: **Overview** · **Positions** · **Intelligence** (charts, signals, backtest, attribution) · **Trade Log** · **Config** · **System Status**

### Scheduler daemon

```bash
python scheduler/scheduler.py
```

Key scheduled jobs (IST, Mon–Fri):

| Time | Job |
|------|-----|
| 07:30 | Earnings calendar refresh (NSE board meetings) |
| 08:30 | GIFT Nifty pre-market gap check |
| 09:00 | Morning digest — regime + top candidates (T-1 OHLCV) |
| 09:15 | Morning NSE scan |
| Every 15 min 09:15–15:25 | Price monitor (SL/TP check) + F&O monitor |
| 13:00 | Thesis re-evaluation (exit degraded positions) |
| 15:00 | Afternoon NSE scan |
| 15:25 | EOD force-close intraday |
| 15:30 | Outcome tracker (mark TP_HIT / SL_HIT / EXPIRED) |
| 15:45 | OHLCV store update |
| 18:00 | EOD digest → Telegram + Discord |
| 19:00 | US equities scan |
| Every 4h | Crypto scan |
| Sun 02:00 | Walk-forward strategy optimizer |
| Sun 20:00 | Weekly summary |
| 1st Sun of month 21:00 | Drift analysis |
| Daily 02:30 | SQLite backup (7-day rolling) |
| Daily 06:05 | Housekeeping (log trim, cache cleanup) |

### REST API (TradingView webhooks)

```bash
python api/server.py              # http://localhost:8000
```

Endpoints: `POST /signal` (inbound TradingView alert), `GET /status`, `GET /portfolio`

---

## Signal Flow — 9 Pipeline Stages

### Stage 1: Market Context
Fetches regime (bull/bear/sideways/recovery), PCR signal, FII/DII flow, market breadth, sector scores. All downstream stages receive `MarketContext`.

### Stage 2: Data Fetch
Downloads OHLCV via yfinance for all symbols using regime-appropriate filtering. 24-hour cache in `logs/market_data/`. Passes `ctx.regime` to the scanner so bear-regime filtering applies correctly.

### Stage 3: Technical Analysis
`TechnicalAgent` computes RSI, MACD, EMA crossovers, Bollinger Bands, ADX, Stochastic, OBV, volume spike. Outputs `TAResult` with `score` (0–10) and `signal` (bullish/neutral/bearish). `MomentumFilter` pre-filters to reduce universe.

### Stage 3b: Enrichment
- **MTF**: Multi-timeframe confluence (daily + weekly)
- **Patterns**: Hammer, engulfing, doji, morning star, etc.
- **S/R**: Nearest support/resistance levels (adjusts stop-loss)
- **52W Breakout**: Tags breakout setup type
- **RSI-2**: Mean reversion signal for short-term trades

Each enrichment adjusts `p_direction` ±0.03–0.10.

### Stage 4: Sentiment
RSS feeds from MoneyControl, Economic Times, NSE announcements → Ollama llama3 classification. Falls back to keyword scoring when Ollama unavailable. Sentiment is a **Layer 3 sizing modifier only** (±10%) — it does NOT influence `p_direction`.

### Stage 5: Signal Generation
`StrategyEngine.generate()` computes:
- `p_direction` = weighted TA + trend (regime-conditional weights from `regime_weights.py`)
- Calibration correction applied if ≥10 resolved trades in the confidence band
- `setup_quality` = 0.6×TA + 0.4×trend
- Entry/SL (ATR + S/R pivot max)/TP prices
- `expected_value` = p_direction×avg_win − (1−p)×avg_loss

### Stage 6: Layer 2 Permission
`MarketPermission.evaluate()` checks regime, PCR, FII, sector signal, breadth, earnings window, F&O ban. Returns ALLOW / REDUCE / BLOCK. BLOCK → `signal.action = "BLOCKED"`.

**BLOCK conditions:** bear regime + BUY, sideways regime + BUY (BUYs fully blocked in ranging markets), earnings ≤ 3 days, F&O ban.
**REDUCE conditions:** recovery regime (×0.80), FII bearish (×0.85), FII+PCR double-bearish (×0.70), weak/very-weak breadth (×0.85/×0.75), bearish sector (×0.85), regime in transition (×0.80). Reductions are multiplicative.
**REGIME_STABILITY_GATE** (config, default 2): regime must be confirmed for this many consecutive scans before the transition reduction is lifted.

### Stage 7: Risk Gate
`RiskGate.check()` hard-blocks on: negative EV, confidence < 0.55, position size = 0, portfolio at max positions, PCR extreme. Blocked signals become `"ABSTAIN"`.

### Stage 8: Sizing
`DynamicPositionSizer.calculate()` applies Kelly fraction, VIX multiplier (India VIX), regime multiplier, sector score modifier, sentiment modifier (±10%). Outputs `SizingResult` with final `position_size`.

### Stage 8b: Execution Planning
`ExecutionPlanner.rank_and_allocate()` ranks all BUY candidates by:
```
rank_score = (EV × p_direction) / max(execution_risk + 0.01, 0.01)
```
Applies 30% same-sector penalty, up to 50% correlation penalty, 20% heat reduction when >60% capital deployed. Candidates beyond `MAX_OPEN_POSITIONS` remaining slots are marked `ABSTAIN` with `abstention_reason = "opportunity_cost: rank N"`.

### Stage 9: Execution
`PaperExecutor` simulates fills with slippage. `LiveExecutor` is a Zerodha Kite stub (not auth-wired). Results persisted to `logs/trades.db` via `PortfolioMemory`.

---

## Three-Layer Signal Architecture

```
Layer 1 — Setup Quality    (TA inputs only)
  Inputs:  RSI, MACD, EMA, BB, ADX, Stochastic, OBV, trend position
  Outputs: p_direction (0.0–1.0), setup_quality (0.0–1.0)
  No sentiment. No macro.

Layer 2 — Market Permission  (macro + events)
  Inputs:  regime, PCR, FII, breadth, sector, earnings, F&O ban
  Outputs: ALLOW | REDUCE | BLOCK  +  permission_reason

Layer 3 — Execution Sizing  (risk + portfolio)
  Inputs:  ATR, VIX, Kelly, regime multiplier, sector score, sentiment (±10%)
  Outputs: position_size, position_size_pct, execution_risk
```

**DecisionJournal** records every vote from every module at every layer with weight, raw score, and note. Persisted to `decision_journals` table in `logs/trades.db` for calibration and audit.

---

## Multi-Market Support

| Market | Scanner | Broker | Capital Allocation |
|--------|---------|--------|--------------------|
| NSE Equities | `data/market_scanner.py` | `PaperExecutor` | 40% |
| F&O | `analysis/options_selling.py`, `futures_signals.py` | `fno_paper_broker.py` | 30% |
| US Stocks | `data/us_scanner.py` | `us_paper_broker.py` | 20% |
| Crypto | `data/crypto_scanner.py` | `crypto_paper_broker.py` | 10% |

Capital allocation enforced by `services/paper_treasury.py`. All markets write to the same `logs/trades.db` (separate tables) and aggregate into `logs/unified_state.json` via `services/state_sync.py`.

**F&O, crypto, and US are gated** (`ASSET_CLASS_GATES` in `config.py`) — disabled until explicitly enabled after Phase 6 validation.

**F&O P&L sign convention:**
- Long (BUY CE/PE, FUT-LONG): `pnl = (exit − entry) × qty`
- Short (SELL-*, FUT-SHORT): `pnl = (entry − exit) × qty`

---

## Memory & Calibration Feedback Loop

### Outcome Tracking
`OutcomeTracker` (called by scheduler at 15:30) marks each signal `TP_HIT`, `SL_HIT`, or `EXPIRED`. Updates `signals` table with outcome fields **and** closes the matching `trades` row (`status='closed'`, `exit_price`, `pnl`) so the EOD digest P&L is accurate.

### Confidence Calibration
`ConfidenceCalibrator.compute_confidence_calibration()` buckets resolved BUY signals by `p_direction` band (0.50–0.59, 0.60–0.69, 0.70–0.79, 0.80+) and computes:
- `stated_p` — what the model predicted
- `actual_win_rate` — what actually happened
- `correction_factor` = actual_win_rate / stated_p

Applied in `StrategyEngine.generate()` via `get_correction_factor(p_direction)` when ≥10 trades exist in the band.

### Module Calibration
`compute_module_calibration()` queries Layer 1/2 journal votes on resolved signals, splits by regime, returns win rates per module. Used by `RegimeWeightManager` to replace REGIME_WEIGHTS priors when ≥50 trades per module per regime exist.

### Overconfidence Detection
`detect_overconfidence(threshold=0.10)` flags (regime, setup_type) pairs where `avg_confidence − actual_win_rate > 0.10`. Surfaced in dashboard.

---

## Backtesting & Ablation

### BacktestEngine (`backtest/engine.py`)

```python
result = BacktestEngine().run(
    symbol="RELIANCE",
    start_date="2022-01-01",
    end_date="2024-01-01",
    capital=1_000_000,
    slippage_multiplier=1.0,    # 1× standard, 2× stress
    full_pipeline_replay=True,  # activates Layer 2 gates in backtest loop
)
```

Features:
- **Survivorship bias control** — `nse500_historical_membership.csv` excludes symbols that were not in NSE 500 during the test window
- **Regime breakdown** — returns per-regime win rate, trade count, Sharpe
- **Module attribution** — queries decision_journals to show which modules had highest win rate on this symbol
- **Stress test** — 6 scenarios (1×/2×/3× slippage × with/without execution delay), flags strategy fragile when Sharpe < 0.5 under 2× slippage

### AblationRunner (`backtest/ablation.py`)
Disables individual modules and measures Sharpe delta. Determines which signals actually add edge.

### DriftAnalyser (`backtest/drift_analysis.py`)
Compares live paper outcomes against backtest predictions over a rolling 90-day window. Scheduled monthly (1st Sunday 21:00 IST). Alerts when drift exceeds threshold.

---

## Meta-Decision Engine

### Regime-Conditional Weights (`strategy/regime_weights.py`)

Layer 1/2/3 module weights vary by detected regime:

| Module | Bull | Bear | Sideways | Recovery |
|--------|------|------|----------|----------|
| technical | 0.50 | 0.40 | 0.45 | 0.50 |
| trend_strength | 0.20 | 0.30 | 0.15 | 0.25 |
| vix (L3) | 0.15 | 0.25 | 0.20 | 0.20 |
| sentiment (L3) | 0.05 | 0.00 | 0.05 | 0.05 |

`RegimeWeightManager.get_weights(regime)` returns calibration-derived weights when ≥50 trades per module exist, otherwise falls back to static priors above.

`RedundancyDetector.compute(days=90)` flags module pairs with >85% vote agreement and halves the weaker module's weight.

### Dashboard Panels (Signal Quality tab)
- **Regime Weight Viewer** — current prior vs active weights, highlights zero-weight modules
- **Redundancy Monitor** — pairwise agreement table with ⚠ flags
- **Candidate Competition Table** — today's ranked signals, allocated vs rejected with opportunity cost notes

---

## Research vs Production Separation

### Research Pipeline
`research/sandbox_pipeline.py` — identical logic to `TradingPipeline` but:
- Writes to `research/research.db` only (never touches `logs/trades.db`)
- Never calls executor (no orders placed)
- Accepts `ablation_config` dict to swap/disable modules

### Promotion Gate
Before any research module is promoted to production, `PromotionChecker.evaluate(module_name)` must pass all five requirements:

1. `ablation_test_shows_positive_edge` — ablation JSON in `research/ablations/` shows positive Sharpe delta
2. `paper_trades_count >= 30` — at least 30 trades in `research.db`
3. `paper_expectancy > 0` — positive average expected value
4. `readiness_checker_passes` — `readiness/checker.py` all gates green
5. `manual_approval_logged` — operator called `PromotionChecker().log_approval(module_name, operator="name")`

### Asset Class Gates
F&O, crypto, and US equities are hard-gated in `ASSET_CLASS_GATES` (config.py). The pipeline's `run()` method blocks execution if `nse_spot` is disabled. Use `ReadinessChecker().check_asset_class("fno")` in execution paths before placing F&O orders.

---

## Dashboard

Single Streamlit app (`dashboard/app.py`) with the following pages:

### Overview
Live portfolio value, P&L, open positions, win rate, recent trade history.

### Positions
Mark-to-market for all open positions with unrealised P&L.

### Intelligence
Tabs: **Market Intel** (regime, PCR, FII, breadth) · **Heatmap** (sector heat) · **Backtest** (run + results) · **Charts** (technical chart per symbol) · **Screener** (live signal table) · **Attribution** (module win rates)

### Trade Log
Tabs: **Trade Log** · **Signal Outcomes** · **Signal Quality** (calibration, confidence bands, regime weights, redundancy monitor, candidate competition)

Signal Quality panels:
- Confidence calibration curve (stated vs actual win rate)
- Overconfidence detector (regime × setup_type pairs)
- Module attribution heatmap
- Module calibration heatmap (per-regime win rates)
- Confidence calibration by band
- Regime weight viewer
- Redundancy monitor
- Candidate competition table

### Config
Runtime settings editor — all `_S()` config values can be overridden without code changes. Writes to `logs/user_settings.json`.

### Readiness / System Status
Readiness gate report, asset class gate table, production vs research activity panel.

---

## Deployment

### Server
Oracle Cloud VM, `/home/ubuntu/quantedge/`

### Auto-deploy
Push to `main` → GitHub Actions (self-hosted runner on Oracle VM) → `docker compose up --build` → smoke test → Telegram notify.

### Docker
```bash
docker compose up --build
```

### Systemd services
`deploy/trading-agent.service` (scheduler daemon) and `deploy/trading-dashboard.service` (Streamlit) are included in the repo. Install on the server:

```bash
sudo cp deploy/trading-agent.service /etc/systemd/system/
sudo cp deploy/trading-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trading-agent trading-dashboard
```

### Log Rotation
`utils/__init__.py` configures `RotatingFileHandler` — 50 MB max per file, 7 backup files kept.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TRADING_MODE` | No (default: paper) | `paper` or `live` |
| `VIRTUAL_CAPITAL` | No (default: 1000000) | Starting capital |
| `TELEGRAM_BOT_TOKEN` | For alerts | Telegram bot token |
| `TELEGRAM_CHAT_ID` | For alerts | Target chat ID |
| `DISCORD_BOT_TOKEN` | For alerts | Discord bot token |
| `DISCORD_CHANNEL_ID` | For alerts | Target channel ID |
| `KITE_API_KEY` | Live mode only | Zerodha API key |
| `KITE_API_SECRET` | Live mode only | Zerodha API secret |
| `API_SECRET_KEY` | For REST API auth | Webhook secret |
| `DASHBOARD_PASSWORD` | Optional | Dashboard login |
| `OLLAMA_HOST` | Optional | Ollama server URL |

---

## Known Issues & Audit Findings

Two full codebase audits completed May 2026. All actionable items resolved.

### Audit 1 — Architecture & Configuration

| # | File | Issue | Status |
|---|------|-------|--------|
| 1 | `execution/executor.py` | Portfolio dict not thread-safe during concurrent reads | ✅ Fixed — snapshot under `_EXECUTE_LOCK` |
| 2 | `pipeline/runner.py` | Silent `except: pass` blocks hiding network/data errors | ✅ Fixed — `logger.debug()` on all key paths |
| 3 | `analysis/calibration.py` | Zero-vote module returned `tp_rate=0.0` instead of `None` | ✅ Fixed — zero-vote entries skipped |
| 4 | `execution/executor.py` | `LiveExecutor` Zerodha Kite auth not wired | ⏭️ N/A — paper trading only |
| 5 | `analysis/signal_narrator.py` | LLM path always bypassed (`use_llm=False`) | ✅ Fixed — `use_llm=True`, falls back to template if Ollama offline |
| 6 | `analysis/ipo_alert.py` | Stale NSE API parse, no retry on 403/empty | ✅ Fixed — retry with fresh session, handles both response key formats |
| 7 | `api/server.py` | No rate limiting on webhook endpoints | ✅ Fixed — token-bucket limiter, 60 req/IP/min |
| 8 | `config.py` | `GIFT_NIFTY_GAP_STRONG/MILD` defined but never consumed | ✅ Fixed — `gift_nifty.py` imports and uses them |
| 9 | `pipeline/runner.py` | `CircuitBreaker` not passed to `RiskGate.check()` | ✅ Fixed — wired into Stage 7 |
| 10 | `execution/brokers/*` | `ASSET_CLASS_GATES` advisory-only at pipeline entry | ✅ Fixed — enforced inside each broker's `open_position()` |
| 11 | `deploy/` | No systemd service files in repo | ✅ Fixed — `deploy/trading-agent.service` + `deploy/trading-dashboard.service` |
| 12 | `logs/kite_access_token.txt` | Kite token stored as plain text | ⏭️ N/A — paper trading only |
| 13 | SQLite | No backup strategy | ✅ Fixed — daily backup job, 7-day rolling, online backup API |

### Audit 2 — Runtime Behaviour (Discord alert review, May 2026)

| # | File | Issue | Status |
|---|------|-------|--------|
| A | `analysis/outcome_tracker.py` | `_write_outcome()` only updated `signals` table — `trades` rows stayed `status='open'`, causing Daily Summary to always show 0 trades / Rs.0 P&L | ✅ Fixed — `_close_trade_row()` now closes matching `trades` row on TP/SL/EXPIRED |
| B | `scheduler/scheduler.py` | Options signals Telegram header sent unconditionally even if all positions skipped | ✅ Fixed — header only built and sent when `any_opened=True` |
| C | `scheduler/scheduler.py` | Morning digest showed live-price candidates but used T-1 OHLCV data — confusing to read | ✅ Fixed — footer note `_Data is based on previous session close (T-1)_` added |
| D | `strategy/market_permission.py` | SIDEWAYS regime only applied REDUCE ×0.70 — new BUY entries still placed in ranging markets | ✅ Fixed — SIDEWAYS BUY is now a hard BLOCK |
| F | `strategy/market_permission.py` | Regime stability check used hardcoded `== 1` instead of `REGIME_STABILITY_GATE` config | ✅ Fixed — uses `0 < stability < REGIME_STABILITY_GATE` |
| G | `scheduler/scheduler.py` | Options signals: Telegram sent even when all signals were skipped | ✅ Fixed — guarded by `any_opened` flag |
| H | `execution/price_monitor.py` | Synthetic trade `entry_time` written as date-only string — EOD digest queries `LIKE '2026-05-17%'` on full isoformat column | ✅ Fixed — `datetime.now().isoformat()` |
| I | `scheduler/scheduler.py` | `run_fno_monitor()` lacked scan lock check — ran concurrently with full scans | ✅ Fixed — skips when `_SCAN_ACTIVE` |
| J | `scheduler/scheduler.py` | `_run_options_signals()` had no F&O gate check | ✅ Fixed — returns early if `ASSET_CLASS_GATES["fno"]["enabled"]` is false |
| — | `analysis/earnings_guard.py` | `days_to_earnings()` called by pipeline runner but method was missing — silently fell back to 999 | ✅ Fixed — method added |
| — | `scheduler/scheduler.py` | Earnings calendar only refreshed on first `EarningsGuard()` init per process — stale after midnight | ✅ Fixed — daily refresh job at 07:30 IST |
| — | `backtest/optimiser.py` | `best_params` values were `np.float64` — displayed as `np.float64(14)` in Telegram | ✅ Fixed — `.item()` converts to Python native types |
| — | `scheduler/scheduler.py` | BTC dominance alert fired every 4-hour crypto scan regardless of state change | ✅ Fixed — state-transition tracking via `_btc_blue_chip_last` |

---

## Phase Completion Status

All 8 phases of the PHASES.md roadmap are complete:

| Phase | Name | Status |
|-------|------|--------|
| 0 | Critical Bug Fixes | ✅ Complete |
| 1 | Three-Layer Signal + DecisionJournal | ✅ Complete |
| 2 | Unified RiskGate + Abstention | ✅ Complete |
| 3 | Pipeline Refactor (9 stages) | ✅ Complete |
| 4 | Memory Feedback Loop | ✅ Complete |
| 5 | Backtest + Ablation Engine | ✅ Complete |
| 6 | Meta-Decision Engine | ✅ Complete |
| 7 | Research vs Production Separation | ✅ Complete |

### Before Going Live

1. Run `backtest/engine.py` against real NSE data to confirm positive edge
2. Run `PromotionChecker.evaluate("nse_spot_strategy")` — all 5 gates must pass
3. Install systemd services: `sudo cp deploy/*.service /etc/systemd/system/ && sudo systemctl enable --now trading-agent trading-dashboard`
4. Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` for trade alerts
5. Paper trade for minimum 30 days — `ReadinessChecker` must go green
6. Wire Zerodha Kite auth in `LiveExecutor` (`execution/kite_auth.py`)
7. Set `TRADING_MODE=live` only after readiness report shows all gates passed

---

*Last updated: 2026-05-17*
