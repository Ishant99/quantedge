# QuantEdge — System Gap Analysis & Upgrade Plan

_Generated: 2026-06-10 — based on a full audit of strategy, risk/execution, and infrastructure layers._

**Context driving this audit:** 30-day live results showed F&O losses of Rs.-30,657
(avg Rs.-4,380/trade), all-time win rate 27.8% (below the 33.3% breakeven for 2:1 R:R),
and drift score 0.581 (HALT). The findings below explain *why* and define the fix plan.

---

## Root Causes of Current Losses

These five gaps directly explain the live results. Everything else is secondary.

| # | Root Cause | Location | Why it loses money |
|---|-----------|----------|--------------------|
| 1 | **F&O position sizing underestimates risk 3–5×** | `config.py` (`FNO_SL_MULT=0.70`), `execution/brokers/fno_paper_broker.py` lot sizing, `services/paper_treasury.py` reserve calc | A -30% premium SL with multi-lot positions means actual capital at risk per trade is far above the intended 2% rule. This is the direct cause of Rs.-4,380 average losses. |
| 2 | **No IV rank check before buying options** | `analysis/options_signals.py:177-196` | HV is used as a comment-level proxy; options are bought regardless of whether IV is at its 80th percentile. Buying expensive premium then losing to IV crush + theta is the classic retail F&O failure mode. |
| 3 | **Calibration corrections mostly inactive** | `analysis/calibration.py` (`n_trades >= 10` per band), `strategy/engine.py:150-166` | Only 2/4 confidence bands have enough trades for correction. Uncorrected bands run ~35-40% overconfident, so the risk gate (min confidence 0.55/0.60) passes signals whose true win probability is ~0.35. |
| 4 | **`MAX_DRAWDOWN_PCT` defined but never enforced** | `config.py:150` — zero references anywhere else | There is no portfolio-level drawdown brake. Losses can compound indefinitely; only daily (3%) and weekly (7%) limits exist, and they reset. |
| 5 | **System is long-only in practice** | `analysis/short_signals.py` (generated, never routed), `pipeline/runner.py` (no call site) | In bear/sideways regimes the system now correctly blocks BUYs — but then sits idle. Short signals exist in code but have no execution path, so half the market cycle produces nothing. |

---

## Full Gap Inventory

### A. Strategy & Signal Generation

| Severity | Gap | Location |
|----------|-----|----------|
| CRITICAL | No IV rank gate before option buys; fixed TP/SL (`2.0×`/`0.70×`) regardless of IV, DTE, or theta | `analysis/options_signals.py:177-196`, `config.py` |
| CRITICAL | Calibration correction requires ≥10 resolved trades per band — most bands uncorrected | `analysis/calibration.py:476-500` |
| CRITICAL | Short signals generated but never executed — no route to F&O or any short instrument | `analysis/short_signals.py`, `pipeline/runner.py` |
| HIGH | Always buys nearest weekly expiry — no DTE-aware selection (buying 1-DTE options = theta trap) | `analysis/options_signals.py:122-127` |
| HIGH | No spread strategies — only naked CE/PE buys and straddle/strangle sells | `analysis/options_signals.py` |
| HIGH | Pipeline Stage 1 failure silently falls back to `regime="bull"` — worst possible default | `pipeline/runner.py:33-44, 914` |
| HIGH | Deployed-capital heat calc hardcodes 2% per position | `pipeline/runner.py:1090` |
| MEDIUM | Module attribution measured (`module_attribution()`) but never used to re-weight votes | `analysis/calibration.py:138-236` |
| MEDIUM | Outcome detection on daily bars can record wrong leg when TP and SL hit same day | `analysis/outcome_tracker.py:150-187` |
| MEDIUM | Permission reduction factors (0.70×, 0.75×, 0.80×…) hardcoded with no feedback loop | `strategy/market_permission.py:89-124` |
| MEDIUM | Regime detection lags up to 12h (2-scan stability on twice-daily scans) | `analysis/market_regime.py` |
| MEDIUM | Backtest commission 0.03%/slippage 0.05% hardcoded — optimistic vs reality | `backtest/engine.py:99-100` |
| MEDIUM | Drift analysis not regime-segmented; 75% degradation tolerance too lenient | `backtest/drift_analysis.py:101-144` |
| LOW | Optimizer tunes on only 7 hand-picked symbols — overfitting risk | `backtest/optimiser.py:71-98` |

### B. Risk Management & Execution

| Severity | Gap | Location |
|----------|-----|----------|
| CRITICAL | `MAX_DRAWDOWN_PCT` never enforced — no equity high-water-mark circuit breaker | `config.py:150`, `risk/circuit_breaker.py` |
| CRITICAL | F&O lot sizing not derived from capital-at-risk; treasury reserves notional, not risk | `execution/brokers/fno_paper_broker.py:385-394`, `services/paper_treasury.py:52-57` |
| HIGH | No correlation-aware sizing across asset classes (NIFTY + BANKNIFTY positions double the same bet) | `risk/correlation_filter.py` (NSE-spot only) |
| HIGH | No monthly loss limit, no per-strategy auto-disable, no consecutive-loss cooldown | `risk/circuit_breaker.py` |
| HIGH | Kelly sizing inert below 10 trades per setup; VIX multiplier is the only vol adjustment | `risk/dynamic_sizing.py:60-83` |
| HIGH | No gap-open handling — 0.2% staleness threshold misses overnight 2% gaps | `execution/executor.py:77-91` |
| MEDIUM | `price_monitor.py` writes portfolio JSON without the executor lock — race condition | `execution/price_monitor.py:98-105` |
| MEDIUM | JSON↔SQLite reconciliation detects deltas but never heals them | `services/state_sync.py:458-476` |
| MEDIUM | No bid-ask spread modeling for options — paper fills at chain mid-price | `execution/brokers/fno_paper_broker.py:385-394` |
| MEDIUM | Paper→live switch has no position bootstrap from Kite — duplicate-position risk | `execution/executor.py:367-378` |
| MEDIUM | Paper executor simulates market orders only; live uses GTT limit SLs — behavior mismatch | `execution/executor.py:402-410` |

### C. Infrastructure, Data & Operations

| Severity | Gap | Location |
|----------|-----|----------|
| CRITICAL | SQLite concurrent writes from 24 jobs + bot threads with no WAL mode (except dedup DB) and minimal locking | brokers, `memory/portfolio_memory.py`, `services/state_sync.py` |
| CRITICAL | Schema defined ad-hoc in 7+ files; no migrations framework or version tracking | `executor.py`, broker classes, `ohlcv_store.py`, etc. |
| CRITICAL | Single data source per market (yfinance/NSE scrape/Binance) — no fallback, no failover | `data/market_scanner.py`, `data/nse_options_chain.py` |
| CRITICAL | Secrets (Telegram, Kite keys) stored plaintext in `logs/user_settings.json` | `settings/manager.py` |
| HIGH | `run_db_backup()` exists but is **never scheduled** — no backups are running | `scheduler/scheduler.py:131-164` |
| HIGH | No indices on hot queries (`trades(symbol, exit_time)`, `signals(symbol, timestamp)`, `fno_trades(instrument, entry_time)`) | DB layer |
| HIGH | No job retry/backoff (one crude 60s blocking sleep retry in daily scan); transient failures kill jobs | `scheduler/scheduler.py:287-301` |
| HIGH | No scheduler health monitoring — silent death possible; systemd restarts but nothing alerts | `deploy/trading-agent.service` |
| HIGH | 103+ broad `except Exception` blocks, several `except: pass` — silent failures everywhere | system-wide |
| HIGH | Brokers/pipeline/scheduler effectively untested (12 test files, mostly pure-logic units) | `tests/` |
| MEDIUM | Settings cached at import; dashboard changes require process restart (`reload()` never called) | `settings/manager.py:138-150` |
| MEDIUM | 3 near-identical paper brokers (~1,350 lines) with no shared base class | `execution/brokers/` |
| MEDIUM | Config sprawl: config.py + settings manager + .env with unclear precedence | `config.py`, `settings/manager.py` |
| MEDIUM | Unpinned requirements (`pandas>=2.0.0`...) — non-reproducible builds | `requirements.txt` |
| MEDIUM | No health endpoint, no readiness checks scheduled, no deployment runbook | `readiness/checker.py`, `deploy/` |
| MEDIUM | No data-quality validation (splits, bad ticks, delisted symbols) beyond a 15% jump filter | `data/market_scanner.py:287-337` |
| LOW | Dead code: Windows .bat files, unscheduled functions, commented-out logging | various |

---

## Upgrade Plan

### Phase 1 — Stop the bleeding (this week, ~1-2 days of work)

Directly targets the Rs.-30,657 F&O loss and the 27.8% win rate.

1. **Risk-based F&O lot sizing.** Derive lots from capital-at-risk:
   `lots = floor((capital × FNO_RISK_PCT) / (entry_premium × lot_size × (1 − FNO_SL_MULT)))`, minimum 1, with a new `FNO_RISK_PCT` setting (default 0.5%). Caps the worst case per trade at ~Rs.5,000 instead of Rs.9,000–11,000.
2. **IV rank gate for option buys.** Compute HV/IV percentile over 1 year; block long CE/PE entries when above the 60th percentile. Block entries with DTE < 3 (theta trap) — partially exists via `FNO_MIN_LONG_DTE=2`, tighten and verify.
3. **Portfolio max-drawdown circuit breaker.** Track equity high-water mark in `circuit_breaker.py`; block all new entries when drawdown > `MAX_DRAWDOWN_PCT` (finally enforce it). Add a monthly loss limit (15%).
4. **Lower calibration minimum from 10 → 5 trades per band** and alert when any correction factor < 0.70. Gets corrections active on more bands now, sharpening the overconfidence fix.
5. **Operational safety trio** (quick wins):
   - Enable `PRAGMA journal_mode=WAL` on all SQLite connections.
   - Add the three missing hot-query indices.
   - Schedule the existing `run_db_backup()` job (daily 01:00 IST).

### Phase 2 — Harden the system (next 2–3 weeks)

6. **Per-strategy auto-disable + consecutive-loss cooldown.** Track P&L per setup type; if a setup is down >3% in a month, block new entries for 7 days. After 3 consecutive losing days overall, halve sizes the next day.
7. **Cross-asset correlation sizing.** If a BANKNIFTY direction position is open, reduce a new same-direction NIFTY position by ~25% (and vice versa). Treat NSE beta + index F&O as one exposure bucket.
8. **Pipeline fail-safe defaults.** Stage 1 failure should default to `regime="sideways"` (blocks buys) rather than `"bull"`, and send an alert. Validate enrichment output; reduce confidence when enrichment is empty.
9. **Job retries with exponential backoff** (replace the blocking `sleep(60)`), plus a scheduler heartbeat file checked by a tiny watchdog job that alerts if stale.
10. **Reconciliation healing.** Hourly job: if JSON↔SQLite delta > Rs.1,000, realign JSON to SQLite (source of truth) and alert.
11. **Options spread/slippage modeling.** Apply moneyness/OI-based spread to paper fills (mid ± 1–5%), and raise backtest costs to realistic levels (0.04–0.10% + spread).
12. **Regime-segmented drift analysis** with a tighter degradation tolerance, so a bull-market backtest isn't compared against sideways-market live results.
13. **Lock unification.** Route `price_monitor.py` and `trailing_stop.py` portfolio writes through the executor lock.

### Phase 3 — Capability upgrades (1–2 months)

14. **Short-side execution path.** Route `ShortSignalGenerator` output to the F&O broker (bear PE buys / futures shorts) so bear and sideways regimes generate P&L instead of idling. This is the single biggest *upside* opportunity — the system currently only monetizes ~half the market cycle.
15. **Defined-risk spreads.** Replace naked option buys with debit spreads (bull call / bear put) — caps theta and IV-crush losses structurally, complements gap #2.
16. **Module re-weighting feedback loop.** Use `module_attribution()` edge data to down-weight modules with negative edge in the strategy engine — closes the measure-but-never-act loop.
17. **Refactor: base `PaperBroker` class** consolidating the 3 brokers; fixes apply once instead of three times.
18. **DB migrations framework** (versioned SQL files + `_schema_version` table); consolidate all schema creation.
19. **Secrets encryption at rest** for `user_settings.json` (Fernet, key from env/systemd credential).
20. **Settings hot-reload** — call `settings.manager.reload()` at the top of each scheduled job, or switch hot paths to runtime `S.get()` reads.
21. **Data source fallback** — secondary OHLCV source for NSE (e.g., NSE bhavcopy) with quality validation (split detection, schema checks).
22. **Test expansion** — integration tests for the full pipeline (signal→permission→sizing→execution against a fixture DB), broker exit-trigger tests, concurrent-write stress test.
23. **Ops polish** — pinned `requirements.txt`, health endpoint, deployment runbook, readiness check scheduled pre-market.

---

## Success Criteria

| Metric | Current | Target after Phase 1-2 |
|--------|---------|------------------------|
| F&O avg loss per losing trade | Rs.-4,380 | < Rs.-1,500 (risk-based sizing) |
| Win rate (all-time, resolved) | 27.8% | > 35% (calibration + IV gate + tighter entries) |
| Drift score | 0.581 (HALT) | < 0.40, trending to < 0.20 |
| Max theoretical loss/trade | unbounded vs intent | hard-capped at FNO_RISK_PCT |
| DB backup | never runs | daily, verified |
| Calibration bands active | 2/4 | 4/4 |
