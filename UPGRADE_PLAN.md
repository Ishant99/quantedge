# Quantedge Upgrade Plan — Final
**Branch**: `codex-v2-phase-rollout`  
**Date**: 2026-04-23  
**Scope**: Full strategic upgrade — bug fixes, architectural pivots, new subsystems

---

## Strategic Identity (What This System Is)

This is not a signal scanner. After this upgrade it should be:

> **A risk-first portfolio decision engine for Indian equities, with explainable policy and a learning feedback loop.**

Signal generation is an input to that engine, not the product itself.

The three pivots that define this upgrade:

1. From "many signals stacked" → "three clean decision layers with an explicit risk gate"
2. From "alpha-first signal bot" → "risk-first execution engine where no-trade is always valid"
3. From "AI for buy/sell authority" → "AI for narration, explanation, and operator copilot"

Everything below serves those three pivots.

---

## What We Are Not Changing

Strong foundations — plan does not touch their architecture:

- Paper/live execution split (`execution/executor.py`)
- Risk subsystem separation (`risk/`)
- Persistent memory foundation (`memory/portfolio_memory.py`)
- Dashboard/control plane infrastructure (`dashboard/app.py`)
- Scheduler/automation (`scheduler/`)
- Settings priority model (`config.py` + `settings/manager.py`)
- Telegram/Discord alerting
- Zerodha Kite integration

---

## Phase 0 — Critical Bug Fixes
**Goal**: Stop the system from silently producing wrong results in its current state.  
**Time estimate**: 2–3 days  
**Prerequisite**: Nothing — do this first

### 0.1 Backtest same-bar entry/exit
**File**: `backtest/engine.py`  
**Problem**: Enters at bar close, checks SL/TP on the same bar. Physically impossible. Win rate is overstated.  
**Fix**: Add `bars_held` counter. Only evaluate SL/TP when `bars_held >= 1`.

### 0.2 Executor race condition — duplicate position risk
**File**: `execution/executor.py`  
**Problem**: Open-position check happens in `main.py` before enrichment. Trailing stop can close the position in between. Executor never re-checks.  
**Fix**: Inside `execute()`, reload portfolio and re-check symbol not already open *after* acquiring the portfolio lock.

### 0.3 Weekly circuit breaker uses wrong snapshot index
**File**: `risk/circuit_breaker.py`  
**Problem**: Uses 5-positions-back as "a week ago", not 5 trading days. Misfires on holiday weeks or restarts.  
**Fix**: Find snapshot where `(now − snapshot.timestamp)` is between 4–6 trading days using date arithmetic.

### 0.4 yfinance blocks entire pipeline with no timeout
**File**: `main.py`  
**Problem**: Bare yfinance call for Nifty return with no timeout or fallback. Rate-limit halts the entire scan silently.  
**Fix**: Wrap in `try/except` with 10s timeout. Default to `nifty_return = 0.0` on failure.

### 0.5 Sentiment default positive bias
**File**: `analysis/sentiment_agent.py`  
**Problem**: No-news default is `+0.15`. LLM prompt says "avoid neutral." Systematic inflation on uncovered stocks.  
**Fix**: Default to `0.0` when no news found.

### 0.6 VIX cache TTL too long
**File**: `risk/dynamic_sizing.py`  
**Problem**: VIX cached 1 hour. Can double in 15 min during a crash.  
**Fix**: Reduce cache TTL to 15 minutes.

### 0.7 Regime has no hysteresis
**File**: `analysis/market_regime.py`  
**Problem**: Regime flips on a single scan. Volatile opens thrash sizing multipliers 3× in one session.  
**Fix**: Add `regime_stability` counter. Commit regime switch only after 2 consecutive scans agree.

---

## Phase 1 — Three-Layer Signal Architecture + Decision Journal
**Goal**: Replace confidence soup with three clean decision layers. Build the audit trail every later phase depends on.  
**Time estimate**: 5–6 days  
**Prerequisite**: Phase 0

### The Three Layers

Every signal must pass through exactly three layers in sequence. Each layer answers a different question and has a different failure mode.

```
Layer 1 — Setup Quality      Is this a valid opportunity?
Layer 2 — Market Permission  Should we even trade right now?
Layer 3 — Execution Sizing   How much and how aggressively?
```

This replaces the current pattern where ~11 enrichments (pattern boost, FII boost, support boost, etc.) all pile into a single confidence number with no accountability.

---

### 1.1 Layer 1: Setup Quality
**File**: `strategy/engine.py`  
Answers: "Is this a good trade setup on its own merits?"

Inputs (technical analysis only — no macro):
- TA score (MACD, RSI, EMA alignment, Bollinger)
- Support/resistance proximity
- Volume profile context
- Pattern recognition
- Multi-timeframe weekly confirmation
- R:R ratio (must be ≥ 1.5 to pass)

Output: `setup_quality` (0.0–1.0) and `p_direction` (directional probability)

**What does NOT go here**: FII data, regime, PCR, sector rotation, sentiment. Those are Layer 2.

---

### 1.2 Layer 2: Market Permission
**File**: `strategy/market_permission.py` (new file)  
Answers: "Does the current market environment permit this trade?"

Inputs (macro and context only):
- Market regime (Bull/Bear/Sideways/Recovery)
- PCR signal
- FII/DII net flow
- Sector rotation signal
- Market breadth (advance/decline ratio)
- Earnings guard (event risk within 3 days)
- F&O ban status

Output: `permission` (ALLOW / REDUCE / BLOCK) and a `permission_reason`

Rules:
- BLOCK: regime is Bear and action is BUY
- BLOCK: earnings within 3 days
- BLOCK: F&O ban active
- REDUCE: FII net selling + PCR bearish → reduce position size 30%
- REDUCE: regime is Recovery → reduce position size 20%
- ALLOW: all clear

Market permission does not score the trade. It gates or scales it.

---

### 1.3 Layer 3: Execution Sizing
**File**: `risk/dynamic_sizing.py` (upgrade existing)  
Answers: "Given the setup and permission, how large should this position be?"

Inputs:
- `setup_quality` from Layer 1
- `permission` modifier from Layer 2
- Portfolio heat (current open risk)
- Correlation to existing positions
- VIX / volatility regime
- Kelly fraction from historical win rate for this setup type
- Block-deal signal (size boost if present)

Output: `position_size`, `position_size_pct`, `execution_risk`

This layer is the only place position size is computed. Sentiment is a modifier here only (±10% on size, nothing else).

---

### 1.4 Refactor `TradeSignal` into a rich object
**File**: `strategy/engine.py`

```python
@dataclass
class TradeSignal:
    # Identity
    symbol: str
    action: str          # BUY / SELL / SHORT / HOLD / ABSTAIN

    # Layer 1 outputs
    p_direction: float   # probability thesis is correct (0.0–1.0)
    setup_quality: float # setup cleanliness (0.0–1.0)
    risk_reward: float   # raw R:R ratio

    # Layer 2 outputs
    permission: str      # ALLOW / REDUCE / BLOCK
    permission_reason: str

    # Layer 3 outputs
    position_size: int
    position_size_pct: float
    execution_risk: float    # friction + event proximity (0.0–1.0)

    # Derived
    expected_value: float    # p_direction × avg_win − (1−p_direction) × avg_loss

    # Prices
    entry_price: float
    stop_loss: float
    take_profit: float

    # Audit trail (accumulates through pipeline)
    journal: DecisionJournal

    # Legacy compat
    @property
    def confidence(self) -> float:
        return self.p_direction
```

---

### 1.5 Create `DecisionJournal` — the pipeline audit trail
**New file**: `strategy/decision_journal.py`

```python
@dataclass
class ModuleVote:
    module: str
    layer: int                    # 1, 2, or 3
    vote: str                     # BUY / SELL / NEUTRAL / BLOCK / REDUCE
    raw_score: float
    weight: float                 # regime-conditional weight (Phase 5)
    weighted_contribution: float
    note: str

@dataclass
class DecisionJournal:
    symbol: str
    timestamp: datetime
    regime: str
    regime_stability: int
    breadth_signal: str
    market_context: dict          # PCR value, FII net, sector rotation

    # Votes per layer
    layer1_votes: list[ModuleVote]
    layer2_votes: list[ModuleVote]
    layer3_votes: list[ModuleVote]

    # Risk gate result (Phase 2)
    risk_gate_passed: bool
    risk_gate_blocks: list[dict]  # {check, reason, value}

    # Sizing rationale
    sizing_rationale: dict        # {kelly, vix_mult, regime_mult, correlation_cost, final}

    # Final decision
    final_action: str
    abstention_reason: str | None

    # Post-trade outcomes (filled by outcome tracker)
    outcome_1d: float | None = None
    outcome_3d: float | None = None
    outcome_5d: float | None = None
    outcome_exit: float | None = None
```

### 1.6 Thread journal through pipeline
**File**: `main.py`  
Each analysis stage appends a `ModuleVote` to the correct layer of the journal. Layer 2 permission checks write to `layer2_votes`. Risk gate results write to `journal.risk_gate_blocks`. All sizing changes write to `journal.sizing_rationale`.

### 1.7 Persist journal to memory
**File**: `memory/portfolio_memory.py`  
New `decision_journals` table. `save_journal(journal)` serializes as JSON, linked to `signal_id`.

### 1.8 Demote sentiment — final role definition
**File**: `analysis/sentiment_agent.py`  
Sentiment is removed from Layer 1 and Layer 2 entirely.

Sentiment's only roles:
- Layer 3 modifier: `±10%` on `position_size_pct` (capped)
- If strongly negative + no news context: sets `execution_risk += 0.15`
- If event language detected (earnings, guidance, regulatory): contributes a BLOCK vote to Layer 2

Sentiment is explicitly **not** a directional signal. It is a sizing modifier and a risk flag.

### 1.9 Reframe AI role
**Files**: `analysis/sentiment_agent.py`, `analysis/signal_narrator.py`, `dashboard/app.py`  

AI (LLM) usage going forward is restricted to:
- **Narration**: `signal_narrator.py` explains the decision in plain English for the dashboard
- **Post-trade review**: summarizes what happened and why after exit
- **Anomaly detection**: flags unusual signal patterns for operator review
- **News/event summarization**: contextualizes earnings or macro events

AI is **not** used for raw buy/sell probability. `p_direction` comes from rules and historical calibration only. This makes the system auditable and trustworthy.

---

## Phase 2 — Unified Risk Gate + Abstention Layer
**Goal**: Make risk the true center of the system. One place where all trades must pass. No-trade becomes a first-class decision.  
**Time estimate**: 4–5 days  
**Prerequisite**: Phase 1

### The Problem with Current Risk
Risk checks are scattered: some in `strategy/engine.py`, some in `execution/executor.py`, some in `main.py`'s enrichment loop, some in `risk/`. A trade can slip through gaps between them.

### 2.1 Unified risk policy gate
**New file**: `risk/risk_gate.py`  
Single class, single `evaluate(signal, portfolio_state, market_context) -> RiskVerdict` method.

Every trade must pass through this gate before execution. The gate is the only place the following checks live:

```python
RISK_GATE_CHECKS = [
    # Portfolio heat
    "portfolio_heat > MAX_PORTFOLIO_HEAT",           # total open risk % of capital
    "open_positions >= MAX_POSITIONS",

    # Exposure controls  
    "sector_exposure > MAX_SECTOR_PCT",
    "single_stock_exposure > MAX_SINGLE_STOCK_PCT",
    "portfolio_correlation > MAX_PORTFOLIO_CORRELATION",

    # Drawdown state
    "daily_loss > MAX_DAILY_LOSS_PCT",
    "weekly_loss > MAX_WEEKLY_LOSS_PCT",
    "total_drawdown > MAX_DRAWDOWN_PCT",

    # Regime state
    "regime == BEAR and action == BUY",
    "regime_stability < STABILITY_GATE",

    # Event risk
    "earnings_within_days <= 3",
    "fno_ban == True",

    # Setup minimums
    "setup_quality < MIN_SETUP_QUALITY",
    "risk_reward < MIN_RISK_REWARD",
    "expected_value < MIN_EDGE_THRESHOLD",

    # Execution conditions
    "execution_risk > MAX_EXECUTION_RISK",
    "p_direction < MIN_DIRECTIONAL_CONVICTION",
]
```

Output: `RiskVerdict(passed: bool, blocks: list[str], suggested_action: str)`

If any check fires, `passed = False`. The gate writes all blocks to `journal.risk_gate_blocks`.

### 2.2 Remove scattered risk checks
Remove equivalent checks from `strategy/engine.py`, `main.py` enrichment loop, and `execution/executor.py`. They now live only in `risk_gate.py`. This eliminates the gap problem.

### 2.3 Abstention classifier
**New file**: `strategy/abstention.py`  
Separate from the risk gate. The risk gate asks "is this trade safe?" The abstention classifier asks "is this trade worth taking?"

Abstains if:
- Edge is present but weak (`expected_value` between `MIN_EDGE` and `GOOD_EDGE`)
- Signal conflict: Layer 1 bullish but Layer 2 bearish or mixed
- Uncertainty: `p_direction` in 0.50–0.55 band
- Regime transition in progress (stability counter < threshold)
- Portfolio already has correlated exposure reducing marginal value

When abstaining, records full reason in `journal.abstention_reason`.

### 2.4 Add abstention thresholds to config
**File**: `config.py`
```python
MIN_EDGE_THRESHOLD        = 0.8    # min expected return % after costs
MIN_SETUP_QUALITY         = 0.45
MIN_RISK_REWARD           = 1.5
MAX_EXECUTION_RISK        = 0.75
MIN_DIRECTIONAL_CONVICTION = 0.54
MAX_PORTFOLIO_HEAT        = 0.08   # max 8% of capital at risk simultaneously
REGIME_STABILITY_GATE     = 2
```

### 2.5 Track abstention rate in dashboard
**File**: `dashboard/app.py`  
Show `abstained / (executed + abstained)` per session. If > 70% for 3 consecutive sessions, flag "system may be over-cautious" and suggest threshold review.

---

## Phase 3 — Pipeline Refactor: Named Stages
**Goal**: Replace monolithic `main.py` with a pipeline of named, testable, typed stages. Highest engineering ROI.  
**Time estimate**: 4–5 days  
**Prerequisite**: Phase 1 + 2 (stages need to call the new components)

### 3.1 Split `main.py` into `pipeline/runner.py`

```python
class TradingPipeline:
    def run(self, dry_run=False) -> PipelineResult:
        ctx    = self.context_builder()        # market state, regime, breadth
        cands  = self.candidate_generator(ctx) # universe + ban filter + momentum gate
        feats  = self.feature_generator(cands, ctx)  # all Layer 1 analysis
        sigs   = self.signal_generator(feats, ctx)   # strategy engine → TradeSignal list
        sigs   = self.signal_enricher(sigs, ctx)     # Layer 2 permission + Layer 3 sizing
        sigs   = self.risk_gate(sigs, ctx)           # unified risk gate → filter list
        sigs   = self.abstention_filter(sigs, ctx)   # abstention classifier
        alloc  = self.execution_planner(sigs, ctx)   # portfolio decision layer (Phase 5)
        result = self.executor(alloc, dry_run)       # paper or live execution
        self.post_trade_recorder(result)             # memory, journal, snapshot, alerts
        return result
```

### 3.2 Stage contracts — typed I/O

| Stage | Input | Output |
|---|---|---|
| `context_builder` | config, market data | `MarketContext` |
| `candidate_generator` | `MarketContext` | `list[str]` (symbols) |
| `feature_generator` | symbols, `MarketContext` | `dict[str, FeatureSet]` |
| `signal_generator` | `FeatureSet`, `MarketContext` | `list[TradeSignal]` |
| `signal_enricher` | `list[TradeSignal]`, `MarketContext` | `list[TradeSignal]` |
| `risk_gate` | `list[TradeSignal]`, portfolio state | `list[TradeSignal]` (filtered) |
| `abstention_filter` | `list[TradeSignal]` | `list[TradeSignal]` |
| `execution_planner` | `list[TradeSignal]` | `list[Allocation]` |
| `executor` | `list[Allocation]` | `list[ExecutionResult]` |
| `post_trade_recorder` | `list[ExecutionResult]` | `None` |

Typed contracts mean: backtest replay can inject a historical `MarketContext` and `FeatureSet` directly and skip live data entirely. Each stage is unit-testable in isolation.

### 3.3 Testability layer
**New directory**: `tests/`

- Unit: `abstention.py` rules with synthetic signals
- Unit: `risk_gate.py` with synthetic portfolio states
- Unit: `execution_planner.py` scoring and allocation
- Integration: feed synthetic `FeatureSet` through full pipeline, assert `DecisionJournal` populated correctly
- Regression: run backtest on fixed historical window, assert Sharpe above baseline

---

## Phase 4 — Memory as Learning Feedback Loop
**Goal**: Historical outcomes actively improve future decisions.  
**Time estimate**: 5–6 days  
**Prerequisite**: Phase 1 (needs journal stored at decision time)

### 4.1 Outcome tracker upgrade
**File**: `analysis/outcome_tracker.py`  
Store outcomes at multiple horizons for every executed and abstained signal:
- 1-day, 3-day, 5-day mark-to-market
- Actual exit return
- Comparison against `p_direction` and `expected_value` at signal time

Write outcomes back to `decision_journals` table: `update_journal_outcome(signal_id, horizon, return_pct)`.

### 4.2 Module calibration reports
**New file**: `analysis/calibration.py`  
After N trades per module (default: 30), compute:
- Per-module win rate when it voted BUY — split by regime
- Pairwise module vote correlation (redundancy detection input for Phase 5)
- `p_direction` calibration: does 65% confidence actually win 65% of the time?

Output: `CalibrationReport` stored in memory, rendered in dashboard.

### 4.3 Setup-type performance tracking
**File**: `memory/portfolio_memory.py`  
Extend `get_stats()` to segment by setup type, regime at signal time, and `p_direction` band. Answers: "Are 75% confidence signals actually better than 60% confidence signals?"

### 4.4 Overconfidence detection + correction
**File**: `analysis/calibration.py`  
If stated `p_direction` band consistently over- or under-predicts actual win rate, compute a correction multiplier per regime and setup type. Store in `calibration_corrections` table. Applied in Phase 5.

### 4.5 Sentiment cache fix
**File**: `analysis/sentiment_agent.py`  
Add timestamp to momentum cache. Invalidate if > 24h old.

---

## Phase 5 — Backtest Upgrade: Pipeline-Faithful Replay + Ablation Tests
**Goal**: Backtest reflects what the live system actually does. Ablation tests reveal which modules drive real edge.  
**Time estimate**: 6–8 days  
**Prerequisite**: Phase 3 (pipeline stages must exist to replay them)

### 5.1 Full pipeline replay mode
**File**: `backtest/engine.py`  
Add `full_pipeline_replay=True` that runs all live stages during simulation:
- `context_builder` using historical Nifty data
- `signal_enricher` with Layer 2 permission
- `risk_gate` with historical portfolio state
- `abstention_filter`
- `execution_planner`
- Historical earnings calendar for earnings guard
- VIX history for dynamic sizing

Fast mode (`full_pipeline_replay=False`) remains for quick iteration.

### 5.2 Regime-segmented performance
**File**: `backtest/engine.py`

```
Bull periods:     Win rate 62%, Avg +2.1%, Sharpe 1.4
Sideways:         Win rate 51%, Avg +0.3%, Sharpe 0.6
Bear periods:     Win rate 38%, Avg -0.8%, Sharpe -0.2
Recovery:         Win rate 57%, Avg +1.4%, Sharpe 1.1
```

### 5.3 Ablation tests — measure actual module contribution
**File**: `backtest/ablation.py` (new file)  

Run the backtest multiple times, each time disabling one module:

| Run | Module disabled | Sharpe | Win rate | Max DD |
|---|---|---|---|---|
| Baseline | None | — | — | — |
| A | Sentiment | | | |
| B | Pattern recognition | | | |
| C | Volume profile | | | |
| D | Sector rotation | | | |
| E | PCR signal | | | |
| F | Block deals | | | |
| G | Regime only + trend + risk | | | |

This answers the key question: **which 3 inputs actually drive PnL?** Modules that add no measurable edge in this test get demoted or removed. This is the most important practical pivot — from "more intelligence" to "measured edge."

### 5.4 Survivorship bias fix
Add `data/nse500_historical_membership.csv` — symbols active per date. Only use a symbol's history while it was in-universe.

### 5.5 Paper-vs-backtest drift analysis
**New file**: `backtest/drift_analysis.py`  
Monthly comparison of paper returns vs backtest prediction for the same period. If drift > 5%, surface alert. Diagnoses: stale data, wrong execution assumptions, regime misclassification.

### 5.6 Slippage stress test
Re-run backtest with 2×, 3× slippage and 10-minute execution delay. If P&L collapses under 2× slippage, the edge is fragile and position sizes are too large.

---

## Phase 6 — Meta-Decision Engine
**Goal**: Regime-conditional, evidence-based module weighting. Portfolio decision replaces "top N signals."  
**Time estimate**: 7–10 days  
**Prerequisite**: Phase 4 (needs calibration data — run 50+ trades first)

### 6.1 Regime-conditional module weight table
**New file**: `strategy/regime_weights.py`  
Start with reasoned priors. Update from Phase 4 calibration data after 50+ trades per regime.

```python
REGIME_MODULE_WEIGHTS = {
    "Bull":      {"technical": 0.40, "momentum": 0.25, "volume_profile": 0.20, "fii_dii": 0.15},
    "Bear":      {"market_regime": 0.45, "short_signals": 0.30, "fii_dii": 0.25},
    "Recovery":  {"technical": 0.35, "fii_dii": 0.30, "support_resistance": 0.20, "volume_profile": 0.15},
    "Sideways":  {"support_resistance": 0.40, "volume_profile": 0.30, "technical": 0.20, "pcr_signal": 0.10},
}
# Modules not listed for a regime contribute 0 weight in that regime.
# Weights update monthly from calibration data.
```

Important: weights above are priors. After Phase 4 ablation tests run, modules that showed no measurable edge are removed from the table entirely — not just down-weighted.

### 6.2 Redundancy detection
Compute pairwise vote correlation over last 90 days from `decision_journals`. If two modules agree > 85% of the time, mark the lower-predictive one as "redundant." Surface in dashboard. Halve redundant module's weight automatically.

### 6.3 Confidence calibration correction
Apply Phase 4 correction multipliers to `p_direction` at signal time. Store per-regime, per-setup-type. Recalibrate monthly.

### 6.4 Portfolio candidate competition
**New file**: `strategy/execution_planner.py`  
Replace "take top 5 signals above threshold" with explicit capital competition:

```
score = (expected_value × p_direction) / (execution_risk + correlation_cost)
```

Rank all passing candidates by score. Allocate capital greedily from highest score down, subject to:
- Sector exposure budget
- Correlation budget
- Total capital deployed cap
- Per-signal position cap

Log rejected candidates with reason: "better opportunity held capital."

### 6.5 Opportunity cost tracking
Track how often capital-competition rejections would have been winners. If rejection winners > current portfolio winners, limits are too tight.

---

## Phase 7 — Research vs Production Separation
**Goal**: Protect the live system from experimental code. Enable safe iteration.  
**Time estimate**: 3–4 days  
**Prerequisite**: Phase 3 (needs pipeline stages to separate)

### Two lanes, strict separation

**Production lane** (`pipeline/runner.py`):
- Only approved, tested modules
- Config changes require dashboard + `readiness/checker.py` validation
- Strict logging — every decision logged to journal
- Execution safety — risk gate always runs
- Rollback support: snapshot portfolio state before each session

**Research lane** (`research/`):
- New signals and experimental analyzers
- Ablations and optimizer runs
- Backtests of unvalidated ideas
- No connection to live execution path
- Separate DB (`research.db`) so experiments don't pollute production memory

### Promotion gate
A module moves from Research → Production only after:
- Ablation test shows measurable positive edge (Phase 5.3)
- 30+ paper trades with positive expectancy
- `readiness/checker.py` validation passes
- Manual approval logged

### Per-asset validation gate
**File**: `readiness/checker.py`  
Before F&O, crypto, or US can be enabled in production:
- Its own Layer 1/2/3 signal stack configured separately
- 30+ paper trades with positive expectancy
- Own risk parameters set (not shared with NSE spot)

**Current directive**: Freeze F&O, crypto, US expansion until NSE core completes Phase 5. NSE Indian equities is the hero system. Do not spread maturity across asset classes.

---

## Continuous: Dashboard Upgrades

Add each panel as its underlying data becomes available:

| Panel | Phase | Content |
|---|---|---|
| Three-layer signal breakdown | Phase 1 | Layer 1 setup score, Layer 2 permission, Layer 3 sizing rationale |
| Decision journal viewer | Phase 1 | Full audit trail — votes, blocks, sizing trace |
| AI signal narrator | Phase 1 | Plain-English explanation of each decision |
| Risk gate log | Phase 2 | All gate checks, which fired, which passed |
| Abstention log | Phase 2 | All ABSTAIN decisions with reasons and abstention rate |
| Module calibration heatmap | Phase 4 | Per-module win rate by regime |
| Confidence calibration plot | Phase 4 | Stated p_direction vs actual win rate |
| Ablation results table | Phase 5 | Module contribution ranking |
| Candidate competition view | Phase 6 | Who competed for capital, who won/lost and why |
| Redundancy monitor | Phase 6 | Currently correlated/redundant module pairs |
| Research vs production activity | Phase 7 | What is in research, what is in production, promotion history |

---

## Implementation Summary

| Phase | Focus | Days | Prerequisite |
|---|---|---|---|
| **0** | Critical bug fixes | 2–3 | None |
| **1** | Three-layer signal + decision journal | 5–6 | Phase 0 |
| **2** | Unified risk gate + abstention | 4–5 | Phase 1 |
| **3** | Pipeline refactor (named stages) | 4–5 | Phase 1 + 2 |
| **4** | Memory as feedback loop | 5–6 | Phase 1 |
| **5** | Backtest upgrade + ablation tests | 6–8 | Phase 3 |
| **6** | Meta-decision engine | 7–10 | Phase 4 |
| **7** | Research vs production separation | 3–4 | Phase 3 |
| **Total** | | **36–47 days** | Sequential |

---

## The Three Questions This Plan Answers

**After Phase 0–3**: "Can I trust what the system is doing?"
→ Bugs fixed, three clean layers, full audit trail, unified risk gate

**After Phase 4–5**: "Is what the system is doing actually working?"
→ Calibrated confidence, ablation tests, pipeline-faithful backtest

**After Phase 6–7**: "Is the system improving over time?"
→ Evidence-based module weights, calibration corrections, research promotion gate

---

## The One Principle Behind All of It

> The agent should know *why* it made each decision, track *whether* that decision was right, and use that history to make better decisions next time — while saying no more often than it says yes.

That is the difference between a signal scanner and a decision engine.
