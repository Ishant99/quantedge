# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and populate environment
cp .env.example .env

# One-off dry run (no trades executed)
python main.py --dry-run

# Run the full agent
python main.py

# Start the Streamlit dashboard (port 8501)
streamlit run dashboard/app.py

# Start the APScheduler daemon (long-running)
python scheduler/scheduler.py

# Start the REST API (port 8000)
python api/server.py

# Run all tests
pytest -q

# Run a single test file
pytest tests/test_risk_gate.py -v

# Docker (production)
docker compose up --build
```

## Architecture

### Dual-Path Codebase (In-Flight Refactor)

The repo is mid-refactor. Two paths coexist:

- **Legacy path** (`main.py` → `run_agent()` → 16 sequential function calls): still the production runtime entry point, started by `scheduler/scheduler.py`.
- **Newer typed pipeline** (`pipeline/runner.py` → `TradingPipeline.run()`): 9-stage class-based pipeline with typed I/O contracts in `pipeline/contracts.py`. The refactor plan is in `PHASES.md` (8 phases total).

When the scheduler calls `run_daily_scan()`, it can call either path. The newer pipeline is already wired and tested — new feature work should target `pipeline/runner.py`.

### Core Signal Flow (9 Stages in pipeline/runner.py)

```
Stage 1  market_context   → MarketContext (regime, PCR, FII, breadth, sector scores)
Stage 2  data_fetch       → {symbol: DataFrame} via yfinance (491 NSE + extras)
Stage 3  technical        → {symbol: TAResult}   — MomentumFilter pre-filters before TechnicalAgent
Stage 3b enrichment       → MTF, patterns, S/R, 52W breakouts, RSI2 (adjusts p_direction)
Stage 4  sentiment        → {symbol: SentimentResult} via RSS + Ollama llama3
Stage 5  signal_gen       → list[TradeSignal] from StrategyEngine (TA 50% + Sentiment 30% + Trend 20%)
Stage 6  layer2_permission → BLOCK/REDUCE/ALLOW per signal (MarketPermission)
Stage 7  risk_gate        → ABSTAIN signals that fail checks (RiskGate)
Stage 8  sizing           → Allocation with position size (DynamicPositionSizer)
Stage 9  execution        → PaperExecutor or LiveExecutor
```

### Three-Layer Signal Architecture (Phase 1 goal, partially implemented)

- **Layer 1 — Setup Quality**: TA-only inputs (RSI, MACD, EMA, BB, Volume, ADX, Stochastic, OBV) → `p_direction`, `setup_quality`
- **Layer 2 — Market Permission** (`strategy/market_permission.py`): macro inputs (regime, PCR, FII, earnings, F&O ban) → BLOCK / REDUCE / ALLOW
- **Layer 3 — Execution Sizing** (`risk/dynamic_sizing.py`): ATR-based size, VIX multiplier, regime multiplier, sector score, sentiment modifier (±10% only)

Sentiment belongs **only** in Layer 3 sizing — it must not influence Layer 1 `p_direction`.

### State & Persistence

All runtime state lives under `logs/` (not committed):

| File | Purpose |
|------|---------|
| `logs/virtual_portfolio.json` | Cash + open NSE positions |
| `logs/trades.db` | SQLite: signals, trades, snapshots, fno_trades, crypto_trades, us_trades |
| `logs/unified_state.json` | All-market aggregated state, read by dashboard and bots |
| `logs/user_settings.json` | Runtime overrides (written by dashboard Settings tab) |
| `logs/market_data/{symbol}.csv` | Cached OHLCV, 24h TTL |

### Configuration Precedence

`logs/user_settings.json` → environment variable (`.env`) → `config.py` default

All overridable settings use the `_S("KEY", default=value)` helper in `config.py`. Adding a new config value must use `_S()` so the dashboard can override it without code changes. Config is validated at import time via `_validate_config()`.

### Multi-Market Architecture

| Market | Scanner | Broker | Capital Allocation |
|--------|---------|--------|--------------------|
| NSE Equities | `data/market_scanner.py` | `execution/executor.py` (PaperExecutor) | 40% |
| F&O | `analysis/options_selling.py`, `analysis/futures_signals.py` | `execution/brokers/fno_paper_broker.py` | 30% |
| US Stocks | `data/us_scanner.py` | `execution/brokers/us_paper_broker.py` | 20% |
| Crypto | `data/crypto_scanner.py` | `execution/brokers/crypto_paper_broker.py` | 10% |

Capital allocation is enforced by `services/paper_treasury.py`. All four markets write to the same `logs/trades.db` but different tables, and aggregate into `logs/unified_state.json` via `services/state_sync.py`.

### Scheduler Jobs

`scheduler/scheduler.py` is the long-running daemon (systemd: `trading-agent.service`). Key job times (IST, Mon-Fri):
- 09:15 + 15:00 — full NSE scan
- Every 15 min 09:15–15:00 — price monitor (SL/TP check) and F&O monitor
- 15:25 — EOD close (force-close all intraday)
- 15:30 — outcome tracker (mark TP_HIT / SL_HIT / EXPIRED)
- 18:00 — EOD digest (Telegram + Discord summary)
- 19:00 — US scan
- Every 4h — crypto scan
- Sunday 20:00 — weekly summary

### Alert Fan-out

`utils/telegram.py` `send()` mirrors every alert to both Telegram and Discord in a single call. Both bots (`telegram/bot.py`, `discord_bot/bot.py`) are started as daemon threads from the scheduler on launch.

## Key Conventions

### TradeSignal Fields

`strategy/engine.py` produces `TradeSignal` objects. Critical fields used downstream:
- `action`: `"BUY"` / `"SELL"` / `"HOLD"` / `"BLOCKED"` / `"ABSTAIN"`
- `p_direction`: directional probability 0.0–1.0 (equivalent to `confidence`)
- `setup_quality`: cleanliness of the setup 0.0–1.0
- `position_size`: shares to buy (0 = blocked by sizing)
- `entry_price`, `stop_loss`, `take_profit`
- `setup_type`: string tag used for quality scoring (e.g. `"breakout_52w"`, `"rsi2_mean_reversion"`)

`signal.confidence` is a property alias for `signal.p_direction` — existing code using either form is fine.

### Risk Gate vs Abstention

These are two distinct checks with different philosophies:
- `risk/risk_gate.py` (`RiskGate.check()`): "Is this safe?" — hard blocks for position limits, zero size, negative EV, low confidence
- `strategy/abstention.py` (`Abstention.evaluate()`): "Is this worth it?" — soft abstentions for borderline conviction, regime mismatch, very weak breadth, Friday afternoon

Both are called from `pipeline/runner.py` Stages 7 and implicitly in Stage 6. Signals that fail either get `action = "ABSTAIN"` or `action = "BLOCKED"`.

### F&O P&L Sign Convention

- Long positions (BUY CE/PE, FUT-LONG): `pnl = (exit - entry) × qty`
- Short positions (SELL-*, FUT-SHORT): `pnl = (entry - exit) × qty`

This is critical — the original code had a bug where short positions used the long formula (hiding losses). `fno_paper_broker.py` runs `_backfill_short_pnl_sign()` on first init to correct historical rows.

### Lazy Imports in TradingPipeline

`pipeline/runner.py` defers all module imports to first use via `_get_*()` accessor methods. This is intentional to avoid circular imports at startup. When adding a new analysis module to the pipeline, follow this pattern: add a `_get_new_module()` method and use it within the appropriate stage method.

### TA Score Threshold

`MIN_TA_SCORE = 5.0` (out of 10) is the minimum for a symbol to be tradeable. `TA_SIGNAL_BULLISH = 6.5` is the threshold to emit a BUY signal. Signals below `TA_SIGNAL_BEARISH = 4.0` emit SELL. Configurable via `_S()`.

## Deployment

- **Server**: Oracle Cloud VM at `/home/ubuntu/quantedge/`
- **Auto-deploy**: push to `main` → GitHub Actions (self-hosted runner on Oracle VM) → `docker compose up --build` → smoke test → Telegram notify
- **Systemd services**: `trading-agent.service` (scheduler) and `trading-dashboard.service` (Streamlit)
- **No venv**: system Python3 (`/usr/bin/python3`)

The self-hosted runner is on the Oracle VM itself — the CI workflow does a local checkout and rebuild, no SSH required.

## Active Refactor: PHASES.md

The codebase is progressing through 8 phases (see `PHASES.md` for full detail):
- Phase 0: Critical bug fixes (same-bar backtest entry, executor race condition, sentiment bias)
- Phase 1: Three-layer signal architecture + DecisionJournal audit trail
- Phase 2: Unified RiskGate + Abstention layer (partially done — `risk/risk_gate.py`, `strategy/abstention.py` exist)
- Phase 3: Pipeline refactor to named stages (substantially done — `pipeline/runner.py` exists)
- Phases 4–7: Memory feedback loop, backtest upgrade, meta-decision engine, research/production separation

When working on new features: check which phase they belong to and respect the sequential dependency. Do not implement Phase N+1 concepts before Phase N is complete.

## Known Incomplete Modules

- `backtest/optimiser.py` — skeleton only, not wired
- `analysis/signal_narrator.py` — optional LLM narrative, non-blocking
- `analysis/ipo_alert.py` — sample data only
- `LiveExecutor` in `execution/executor.py` — Zerodha Kite stub, not auth-wired
- `api/server.py` — no systemd service; must be started manually
