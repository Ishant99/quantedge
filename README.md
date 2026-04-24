# QuantEdge Trading Agent

QuantEdge is a Python trading system centered on Indian equities, with paper-trading support for F&O, crypto, and US stocks. The repository combines a runnable legacy orchestration flow in `main.py` and `scheduler/scheduler.py` with a newer layered architecture under `pipeline/`, `strategy/`, `risk/`, and `research/`.

## What Is In This Repo

- A daily NSE scan and execution flow driven by `main.py`
- APScheduler-based automation in `scheduler/scheduler.py`
- Streamlit operations dashboard in `dashboard/app.py`
- Read-only HTTP API in `api/server.py`
- Paper brokers for NSE F&O, crypto, and US markets
- Backtesting, ablation, drift analysis, and research sandbox modules
- Readiness, review-report, and unified-state services for monitoring

## Current State Of The Codebase

This repo is in the middle of a refactor.

- The runnable production-style path still starts from `main.py`
- The newer decision-engine pieces are already present in:
  - `pipeline/contracts.py`
  - `pipeline/runner.py`
  - `strategy/decision_journal.py`
  - `strategy/market_permission.py`
  - `strategy/execution_planner.py`
  - `risk/risk_gate.py`
  - `strategy/abstention.py`
- Planning documents for that migration live in `PHASES.md`, `UPGRADE_PLAN.md`, and `REPO_MAP.md`

If you are trying to operate the system today, follow the legacy entrypoints first. If you are extending the architecture, read the phase docs and newer pipeline modules next.

## Repository Layout

```text
.
|-- main.py                     # Legacy end-to-end trading pipeline
|-- config.py                   # Environment/default configuration
|-- run.py                      # Small CLI wrapper around run_agent()
|-- dashboard/app.py            # Streamlit dashboard and operator controls
|-- api/server.py               # Lightweight JSON API
|-- scheduler/scheduler.py      # Main APScheduler job runner
|-- scheduler/autonomous.py     # Alternate automation entrypoint
|-- analysis/                   # Market, sentiment, TA, and enrichment modules
|-- strategy/                   # Signal objects, journaling, abstention, ranking
|-- risk/                       # Circuit breaker, sizing, risk gate, trailing stops
|-- execution/                  # Paper/live execution and position monitoring
|-- backtest/                   # Historical backtests, ablations, drift analysis
|-- pipeline/                   # Newer pipeline contracts and runner
|-- research/                   # Sandbox and promotion workflow modules
|-- services/                   # Unified state, dashboard feeds, review reports
|-- automation/                 # Weekly summary and report generation
|-- settings/                   # Persistent runtime settings manager
|-- readiness/                  # Go/no-go checks
|-- memory/                     # SQLite/Chroma-backed portfolio memory
|-- telegram/                   # Telegram bot commands
|-- discord_bot/                # Discord bot commands
|-- data/                       # Scanners plus NSE symbol universe CSVs
|-- tests/                      # Pytest coverage for newer risk/pipeline pieces
|-- logs/                       # Runtime artifacts, caches, DBs, reports, market data
```

## Main Runtime Flow

The current `main.py` path does the following:

1. Loads config and portfolio state
2. Runs the circuit breaker
3. Detects market regime
4. Pulls market-wide context such as PCR, FII/DII, and sector rotation
5. Scans the NSE universe
6. Applies momentum and F&O-ban filters
7. Runs TA, support/resistance, multi-timeframe, volume-profile, and pattern analysis
8. Runs sentiment analysis
9. Generates signals through `strategy.engine`
10. Enriches signals with quality, sizing, block-deal, and relative-strength logic
11. Filters with earnings, deduplication, correlation, and position-count guards
12. Executes paper/live trades, updates memory, sends alerts, and snapshots state

## Dashboard And Operator Features

`dashboard/app.py` is the operator console. It includes:

- Portfolio and P&L views
- Unified history and reconciliation tools
- Scheduler health and runtime status
- Review-report generation and download
- Period report generation
- Runtime settings management through `logs/user_settings.json`
- Manual test actions for regime, PCR, FII/DII, TA, sentiment, circuit breaker, and trailing stops

## Scheduler Jobs

`scheduler/scheduler.py` is the long-running process that coordinates:

- GIFT Nifty pre-market check
- NSE morning and afternoon scans
- Price monitor
- F&O monitor
- Intraday scan
- Thesis re-evaluation
- EOD close
- Outcome tracker
- US scan
- Crypto scan
- EOD digest
- Weekly summary
- Housekeeping
- Telegram and Discord bot startup

## Research And Backtesting

The repo includes a second lane for evaluation and controlled experimentation:

- `backtest/engine.py` for walk-forward backtests and replay mode
- `backtest/ablation.py` for module-ablation comparisons
- `backtest/drift_analysis.py` for paper-vs-backtest drift checks
- `research/sandbox_pipeline.py` for isolated sandbox runs
- `research/promotion_checklist.py` for promotion criteria

The phase documents make it clear that research and production are intended to stay separate over time.

## Configuration

Primary configuration sources:

1. `logs/user_settings.json` via `settings/manager.py`
2. Environment variables from `.env`
3. Hard-coded defaults in `config.py`

Useful files:

- `.env.example`
- `config.py`
- `docker-compose.yml`
- `Dockerfile`

Key integrations supported by the codebase:

- Telegram
- Discord
- Ollama for sentiment fallback/LLM usage
- Zerodha Kite for live execution
- Streamlit and Plotly for the dashboard

## Setup

```bash
pip install -r requirements.txt
copy .env.example .env
```

On PowerShell, a common first run is:

```powershell
python .\main.py --dry-run
```

## How To Run

Run a one-off dry run:

```powershell
python .\main.py --dry-run
```

Run the main agent:

```powershell
python .\main.py
```

Use the wrapper:

```powershell
python .\run.py --dry-run
```

Start the dashboard:

```powershell
streamlit run .\dashboard\app.py
```

Start the scheduler:

```powershell
python .\scheduler\scheduler.py
```

Start the API:

```powershell
python .\api\server.py
```

Run with Docker Compose:

```powershell
docker compose up --build
```

## Tests

The current automated tests are focused on the newer architectural pieces:

- `tests/test_abstention.py`
- `tests/test_risk_gate.py`
- `tests/test_pipeline_integration.py`

Run them with:

```powershell
pytest -q
```

## Runtime Data And Generated Artifacts

This repository already contains runtime output under `logs/`, including:

- log files
- cached market data CSVs
- SQLite databases
- review reports
- backtest results
- ChromaDB files

Those files are useful for operations, but they are generated artifacts rather than core source code. The repo also contains planning and mapping documents that describe the ongoing migration.

## Important Notes

- Start with `TRADING_MODE=paper`
- The codebase currently mixes stable runtime flows with in-progress refactor work
- `README` descriptions from older snapshots can drift from the actual code, so `main.py`, `scheduler/scheduler.py`, and the phase documents are the most reliable orientation points
- The worktree is currently dirty in multiple source files, so review local changes before treating this checkout as a clean baseline
