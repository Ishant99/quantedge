# Quantedge — Phase-by-Phase Execution Plan
**Date**: 2026-04-23  
**System identity**: Risk-first portfolio decision engine for Indian equities  
**Total phases**: 8 (Phase 0 through Phase 7)

---

## How to Read This Document

Each phase lists:
- **Goal** — what the system can do after this phase that it cannot do today
- **Tasks** — exact files to create or modify, with what changes
- **Done when** — verifiable criteria for phase completion before moving to the next

Phases are sequential. Do not start Phase N+1 until Phase N is done.

---

---

# PHASE 0 — Critical Bug Fixes
**Goal**: Stop silent wrong results before building anything new  
**Estimated time**: 2–3 days  
**Prerequisite**: None

---

### Task 0.1 — Fix backtest same-bar entry/exit
**File**: `backtest/engine.py`

What to change:
- Add a `bars_held: int` counter, initialized to `0` on entry
- In the per-bar loop, increment `bars_held` each bar
- Wrap all SL/TP exit checks inside `if bars_held >= 1:`
- Add a `min_hold_bars: int = 1` parameter to the backtest config

**Done when**: A signal that enters on bar N cannot exit on bar N. Verified by adding a test: entry at bar 10, SL immediately below close — must not exit until bar 11.

---

### Task 0.2 — Fix executor race condition
**File**: `execution/executor.py`

What to change:
- Inside `PaperExecutor.execute()`, after acquiring the portfolio lock, reload portfolio from disk and re-check that the symbol does not already have an open position
- If already open, return early with `ExecutionResult(status="skipped", reason="position already open")`
- Apply same guard to `LiveExecutor.execute()`

**Done when**: Running two concurrent execute calls for the same symbol results in only one position opened, not two.

---

### Task 0.3 — Fix weekly circuit breaker snapshot index
**File**: `risk/circuit_breaker.py`

What to change:
- Replace the `snaps[-5]` index lookup with a date-arithmetic search
- Find the snapshot where `abs((now - snapshot.timestamp).trading_days - 5) <= 1`
- Use `pandas_market_calendars` or a simple trading-day counter to compute trading day distance
- If no snapshot found in the 4–6 trading day window, fall back to the oldest available snapshot

**Done when**: On a week with a holiday (e.g. only 4 trading days), the weekly loss calculation still uses the correct reference point.

---

### Task 0.4 — Fix yfinance pipeline block
**File**: `main.py`

What to change:
- Wrap the Nifty return yfinance call in `try/except` with a 10-second timeout using `concurrent.futures.ThreadPoolExecutor`
- On timeout or exception, set `nifty_return = 0.0` and log `WARNING: Nifty return fetch failed, defaulting to 0.0`
- Add the same guard to any other bare yfinance calls in the enrichment loop

**Done when**: Blocking the network while the pipeline runs causes a warning log and continues execution — not a hang.

---

### Task 0.5 — Fix sentiment positive bias
**File**: `analysis/sentiment_agent.py`

What to change:
- Find the `no-news` fallback return value (currently `+0.15`) and change to `0.0`
- Find the LLM prompt instruction "avoid neutral" / "be decisive" and remove or soften it to "rate accurately; neutral is valid"
- Test: symbols with zero matching headlines must produce `sentiment_score = 0.0`

**Done when**: Running sentiment on a symbol with no news produces score `0.0`, not positive.

---

### Task 0.6 — Fix VIX cache TTL
**File**: `risk/dynamic_sizing.py`

What to change:
- Find the `_vix_cache` TTL check (currently `3600` seconds)
- Change to `900` (15 minutes)

**Done when**: Cache TTL constant is 900s. One-line change.

---

### Task 0.7 — Add regime hysteresis
**File**: `analysis/market_regime.py`

What to change:
- Add a `_regime_stability: int` counter and `_pending_regime: str` state variable (stored in a JSON state file between runs, same pattern as circuit breaker)
- Each run: compute the new regime from current data
- If new regime differs from current: set `_pending_regime = new_regime`, increment `_regime_stability` counter
- If they match: increment counter further
- Only commit `current_regime = _pending_regime` when `_regime_stability >= 2`
- If regime reverts before counter reaches 2, reset counter and pending regime

**Done when**: Regime does not flip on a single scan. Two consecutive scans in the same new regime are required to commit the change.

---

### Phase 0 Done When:
- [ ] All 7 tasks above complete
- [ ] Backtest on a fixed window produces a lower (more honest) win rate than before the fix
- [ ] No bare yfinance calls without timeout in `main.py`
- [ ] Sentiment score on zero-news symbol is `0.0`
- [ ] Regime does not flip mid-session on volatile Nifty day

---

---

# PHASE 1 — Three-Layer Signal Architecture + Decision Journal
**Goal**: Replace confidence soup with three clean decision layers. Build the audit trail every later phase depends on.  
**Estimated time**: 5–6 days  
**Prerequisite**: Phase 0 complete

---

### The Three Layers (Reference)

```
Layer 1 — Setup Quality      Is this a valid opportunity?         (TA only)
Layer 2 — Market Permission  Should we trade in this environment? (macro + events)
Layer 3 — Execution Sizing   How much and how aggressively?       (risk + portfolio)
```

Each layer owns distinct inputs. No layer reads inputs that belong to another layer.

---

### Task 1.1 — Create `DecisionJournal` dataclass
**New file**: `strategy/decision_journal.py`

Create two dataclasses:

```python
@dataclass
class ModuleVote:
    module: str             # "technical", "fii_dii", "support_resistance", etc.
    layer: int              # 1, 2, or 3
    vote: str               # "BUY" / "SELL" / "NEUTRAL" / "BLOCK" / "REDUCE"
    raw_score: float        # score before weighting
    weight: float           # weight applied (regime-conditional from Phase 6)
    weighted_contribution: float
    note: str               # one-line human-readable reason

@dataclass
class DecisionJournal:
    symbol: str
    timestamp: datetime
    regime: str
    regime_stability: int
    breadth_signal: str
    market_context: dict            # {"pcr": float, "fii_net": float, "sector": str}

    layer1_votes: list[ModuleVote]  # setup quality inputs
    layer2_votes: list[ModuleVote]  # market permission inputs
    layer3_votes: list[ModuleVote]  # sizing inputs

    risk_gate_passed: bool
    risk_gate_blocks: list[dict]    # [{check, reason, value}]

    sizing_rationale: dict          # {kelly, vix_mult, regime_mult, final}

    final_action: str
    abstention_reason: str | None   # None if executed

    # Filled later by outcome tracker
    outcome_1d:   float | None = None
    outcome_3d:   float | None = None
    outcome_5d:   float | None = None
    outcome_exit: float | None = None
```

**Done when**: File exists, dataclasses importable, no logic yet.

---

### Task 1.2 — Refactor `TradeSignal` into rich object
**File**: `strategy/engine.py`

Replace current flat signal object with:

```python
@dataclass
class TradeSignal:
    symbol: str
    action: str              # "BUY" / "SELL" / "SHORT" / "HOLD" / "ABSTAIN"

    # Layer 1 outputs
    p_direction: float       # directional probability (0.0–1.0)
    setup_quality: float     # setup cleanliness (0.0–1.0)
    risk_reward: float

    # Layer 2 outputs
    permission: str          # "ALLOW" / "REDUCE" / "BLOCK"
    permission_reason: str

    # Layer 3 outputs
    position_size: int
    position_size_pct: float
    execution_risk: float    # 0.0–1.0

    # Derived
    expected_value: float    # p_direction × avg_win − (1−p_dir) × avg_loss

    # Prices
    entry_price: float
    stop_loss: float
    take_profit: float

    # Audit trail
    journal: DecisionJournal

    @property
    def confidence(self) -> float:
        return self.p_direction
```

Keep `confidence` as a property alias so existing callers still work without changes.

**Done when**: `TradeSignal` imports cleanly. Existing code that reads `signal.confidence` still works.

---

### Task 1.3 — Build Layer 1: Setup Quality
**File**: `strategy/engine.py` (refactor existing signal generation)

Layer 1 inputs (only these — nothing else):
- TA score: RSI, MACD, EMA alignment, Bollinger Bands
- Support/resistance proximity
- Volume profile context
- Pattern recognition result
- Multi-timeframe weekly confirmation
- Raw R:R ratio

Each input appends a `ModuleVote(layer=1, ...)` to the journal.

Layer 1 output: `p_direction` (0.0–1.0) and `setup_quality` (0.0–1.0).

What must NOT be in Layer 1: FII data, PCR, regime, sector rotation, sentiment. These are Layer 2.

**Done when**: `generate_signal()` produces `p_direction` and `setup_quality` from TA inputs only. Journal has `layer1_votes` populated. Sentiment does not appear in Layer 1 votes.

---

### Task 1.4 — Build Layer 2: Market Permission
**New file**: `strategy/market_permission.py`

Create `MarketPermission` class with `evaluate(symbol, regime, market_context, earnings_guard, fno_ban) -> tuple[str, str]` returning `(permission, reason)`.

Rules:
```
BLOCK if: regime == "Bear" and action == "BUY"
BLOCK if: earnings within 3 days
BLOCK if: symbol on F&O ban list
REDUCE if: FII net selling AND PCR bearish → reduction_factor = 0.70
REDUCE if: regime == "Recovery"               → reduction_factor = 0.80
REDUCE if: market breadth below 40%           → reduction_factor = 0.85
ALLOW:  all clear
```

Each check appends a `ModuleVote(layer=2, ...)` to the journal.

**Done when**: `MarketPermission.evaluate()` returns `("BLOCK", "earnings within 3 days")` for an earnings-risk symbol. Returns `("ALLOW", "")` for a clean symbol in bull regime.

---

### Task 1.5 — Build Layer 3: Execution Sizing (upgrade existing)
**File**: `risk/dynamic_sizing.py`

Layer 3 is the only place position size is computed. Consolidate all sizing logic here:
- Kelly fraction from historical win rate for this setup type
- VIX multiplier (15-min cached)
- Regime multiplier (from `MarketPermission.reduction_factor`)
- Portfolio heat check (remaining risk budget)
- Correlation cost (reduces size if correlated position exists)
- Block-deal boost (+10% if block deal detected, capped)
- Sentiment modifier (±10% on size only, nothing else — sentiment's only role)

Each factor appends a `ModuleVote(layer=3, ...)` to journal. Final size written to `sizing_rationale`.

**Done when**: Position size comes only from this function. No other code computes `position_size`. Sentiment contributes max ±10% to size, does not appear in Layer 1 or 2.

---

### Task 1.6 — Redefine AI/LLM role
**File**: `analysis/sentiment_agent.py`, `analysis/signal_narrator.py`

Changes to `sentiment_agent.py`:
- Remove any LLM call that returns a directional signal ("positive" / "negative" as a BUY input)
- LLM call only returns: sentiment tone + confidence + key phrase
- That output feeds into Layer 3 sizing modifier only (±10% on size, capped)
- If LLM unavailable, sizing modifier = 0.0 (neutral, no change)

Changes to `signal_narrator.py`:
- Keep and expand: this is the primary AI role going forward
- Narrate the final decision in plain English using the `DecisionJournal`
- Output displayed in dashboard per-signal and sent in Telegram alert

Future AI roles (note for Phase 4+):
- Post-trade review summary
- Anomaly detection
- Event summarization (earnings, macro)

**Done when**: Sentiment LLM output does not affect `p_direction`. It only changes `position_size_pct` by ±10% max. `signal_narrator.py` produces a plain-English explanation using journal data.

---

### Task 1.7 — Thread journal through pipeline
**File**: `main.py`

Changes:
- Create a `DecisionJournal` at the start of each symbol's processing
- Pass it into each analysis stage call so stages can append votes
- After Layer 1 (setup quality): journal has `layer1_votes` populated
- After Layer 2 (market permission): journal has `layer2_votes` populated
- After Layer 3 (sizing): journal has `layer3_votes` and `sizing_rationale` populated
- After risk gate (Phase 2): journal has `risk_gate_passed` and `risk_gate_blocks` populated
- Attach journal to `TradeSignal` before returning

**Done when**: Every `TradeSignal` has a `journal` with all three layers populated. No layer is empty for any processed symbol.

---

### Task 1.8 — Persist journal to memory
**File**: `memory/portfolio_memory.py`

Changes:
- Add `decision_journals` table: `(id, signal_id, symbol, timestamp, regime, json_blob)`
- Add `save_journal(journal: DecisionJournal) -> int` method: serializes journal as JSON, inserts row
- Add `get_journal(signal_id: int) -> DecisionJournal` method: deserializes from JSON
- Add `update_journal_outcome(signal_id, horizon, return_pct)` method: writes outcome fields
- Link `decision_journals.signal_id` to `signals.id` foreign key

**Done when**: After a run, `decision_journals` table has one row per processed symbol. Journal round-trips (save → load) without data loss.

---

### Phase 1 Done When:
- [ ] `DecisionJournal` and `ModuleVote` importable from `strategy/decision_journal.py`
- [ ] `TradeSignal.p_direction` computed from Layer 1 TA inputs only
- [ ] `MarketPermission.evaluate()` returns correct BLOCK/REDUCE/ALLOW for test cases
- [ ] Position size computed only in `risk/dynamic_sizing.py` Layer 3
- [ ] Sentiment does not appear in Layer 1 votes
- [ ] Every signal has a populated journal with all three layers
- [ ] `decision_journals` table exists and persists after a run
- [ ] Signal narrator produces plain-English output per signal

---

---

# PHASE 2 — Unified Risk Gate + Abstention Layer
**Goal**: One place every trade must pass. No-trade is a first-class decision.  
**Estimated time**: 4–5 days  
**Prerequisite**: Phase 1 complete

---

### Task 2.1 — Create unified risk gate
**New file**: `risk/risk_gate.py`

Create `RiskGate` class with single method:
```python
def evaluate(
    self,
    signal: TradeSignal,
    portfolio_state: PortfolioState,
    market_context: MarketContext
) -> RiskVerdict:
```

`RiskVerdict` dataclass:
```python
@dataclass
class RiskVerdict:
    passed: bool
    blocks: list[str]    # list of check names that fired
    reasons: list[str]   # human-readable reason per block
```

All checks live here and only here:

```
Portfolio health:
  portfolio_heat > MAX_PORTFOLIO_HEAT            # total open risk % of capital
  open_positions >= MAX_POSITIONS

Exposure controls:
  sector_exposure > MAX_SECTOR_PCT
  single_stock_exposure > MAX_SINGLE_STOCK_PCT
  portfolio_correlation > MAX_PORTFOLIO_CORRELATION

Drawdown state:
  daily_loss > MAX_DAILY_LOSS_PCT
  weekly_loss > MAX_WEEKLY_LOSS_PCT
  total_drawdown > MAX_DRAWDOWN_PCT

Regime:
  regime == "Bear" and action == "BUY"
  regime_stability < STABILITY_GATE

Event risk:
  earnings_within_days <= 3
  fno_ban == True

Setup minimums:
  setup_quality < MIN_SETUP_QUALITY
  risk_reward < MIN_RISK_REWARD
  expected_value < MIN_EDGE_THRESHOLD

Execution conditions:
  execution_risk > MAX_EXECUTION_RISK
  p_direction < MIN_DIRECTIONAL_CONVICTION
```

Write all fired blocks to `signal.journal.risk_gate_passed` and `signal.journal.risk_gate_blocks`.

**Done when**: `RiskGate.evaluate()` returns `RiskVerdict(passed=False, blocks=["earnings_within_days"])` for an earnings-risk signal. Returns `RiskVerdict(passed=True)` for a clean signal meeting all thresholds.

---

### Task 2.2 — Remove scattered risk checks
**Files**: `strategy/engine.py`, `main.py`, `execution/executor.py`

Remove from each file: any check that duplicates what `risk_gate.py` now owns.

Specifically:
- Remove earnings guard check from `main.py` signal enrichment loop (risk gate owns it)
- Remove open-position check from `execution/executor.py` (risk gate owns it; executor still has the race-condition reload guard from Phase 0.2, keep that)
- Remove max-position limit check from `main.py` (risk gate owns it)
- Remove correlation filter inline check from `main.py` (risk gate owns it)

After removal, the only place these checks run is `risk_gate.py`.

**Done when**: grep for `MAX_POSITIONS` in `main.py` returns zero matches outside of config reads. Same for `correlation`, `earnings`, `sector_exposure`.

---

### Task 2.3 — Wire risk gate into pipeline
**File**: `main.py`

After signal enrichment (Layer 1+2+3 complete), call:
```python
verdict = risk_gate.evaluate(signal, portfolio_state, market_context)
if not verdict.passed:
    signal.action = "BLOCKED"
    signal.journal.risk_gate_passed = False
    signal.journal.risk_gate_blocks = verdict.blocks
    memory.save_journal(signal.journal)
    continue  # skip execution
```

All blocked signals are persisted to journal (not discarded) so we can review what was blocked and why.

**Done when**: A signal with earnings in 2 days is blocked by risk gate, persisted to `decision_journals` with `risk_gate_passed=False`, and skipped by executor.

---

### Task 2.4 — Create abstention classifier
**New file**: `strategy/abstention.py`

Separate from risk gate. Risk gate asks "is this safe?" Abstention asks "is this worth it?"

```python
def evaluate(signal: TradeSignal, portfolio_state: PortfolioState) -> tuple[bool, str]:
    """Returns (should_abstain, reason). True = abstain."""
```

Abstain if any of these fire:
```
expected_value < MIN_EDGE_THRESHOLD                      → "edge below minimum"
risk_reward < MIN_RISK_REWARD                            → "R:R insufficient"
p_direction between 0.50–0.54                            → "borderline conviction"
layer1_bullish_votes <= 1 and layer2 == "REDUCE"         → "weak setup + poor environment"
regime_stability == 1 (transition in progress)           → "regime uncertainty"
portfolio already holds correlated symbol                → "marginal diversification value"
```

On abstention: `signal.action = "ABSTAIN"`, write reason to `signal.journal.abstention_reason`, persist journal, skip execution.

**Done when**: A signal with `p_direction = 0.52` and `expected_value = 0.6%` returns `(True, "borderline conviction")`. A strong signal with `p_direction = 0.72` and `expected_value = 1.8%` returns `(False, "")`.

---

### Task 2.5 — Add thresholds to config
**File**: `config.py`

Add (using `_S()` helper so dashboard can override):
```python
MIN_EDGE_THRESHOLD         = _S("MIN_EDGE_THRESHOLD",         0.8)   # % expected return
MIN_SETUP_QUALITY          = _S("MIN_SETUP_QUALITY",          0.45)
MIN_RISK_REWARD            = _S("MIN_RISK_REWARD",            1.5)
MAX_EXECUTION_RISK         = _S("MAX_EXECUTION_RISK",         0.75)
MIN_DIRECTIONAL_CONVICTION = _S("MIN_DIRECTIONAL_CONVICTION", 0.54)
MAX_PORTFOLIO_HEAT         = _S("MAX_PORTFOLIO_HEAT",         0.08)  # 8% capital at risk max
REGIME_STABILITY_GATE      = _S("REGIME_STABILITY_GATE",      2)
```

**Done when**: All thresholds loadable from `user_settings.json` via dashboard.

---

### Task 2.6 — Dashboard: abstention and risk gate logs
**File**: `dashboard/app.py`

Add two new panels:

**Risk Gate Log**: table of today's blocked signals — symbol, blocks that fired, values that triggered each block.

**Abstention Log**: table of today's abstained signals — symbol, reason, `p_direction`, `expected_value`. Include session-level abstention rate: `abstained / (executed + abstained)`. If > 70% for 3 consecutive sessions, show banner: "System may be over-cautious — review thresholds."

**Done when**: After a run, dashboard shows blocked and abstained signals with reasons. Abstention rate visible.

---

### Phase 2 Done When:
- [ ] `risk/risk_gate.py` exists with all checks consolidated
- [ ] Zero duplicate risk checks remaining in `main.py`, `strategy/engine.py`, `execution/executor.py`
- [ ] Blocked signals persisted to `decision_journals` with `risk_gate_passed=False`
- [ ] `strategy/abstention.py` exists and returns correct results for edge cases
- [ ] All new thresholds in `config.py` using `_S()` helper
- [ ] Dashboard shows risk gate log and abstention log

---

---

# PHASE 3 — Pipeline Refactor: Named Stages
**Goal**: Replace monolithic `main.py` with typed, testable pipeline stages. Highest engineering ROI.  
**Estimated time**: 4–5 days  
**Prerequisite**: Phase 1 + Phase 2

---

### Task 3.1 — Create `pipeline/` directory and `runner.py`
**New file**: `pipeline/runner.py`

```python
class TradingPipeline:
    def run(self, dry_run: bool = False) -> PipelineResult:
        ctx     = self.context_builder()
        cands   = self.candidate_generator(ctx)
        feats   = self.feature_generator(cands, ctx)
        sigs    = self.signal_generator(feats, ctx)
        sigs    = self.signal_enricher(sigs, ctx)
        sigs    = self.risk_gate_stage(sigs, ctx)
        sigs    = self.abstention_stage(sigs, ctx)
        alloc   = self.execution_planner(sigs, ctx)
        result  = self.executor_stage(alloc, dry_run)
        self.post_trade_recorder(result)
        return result
```

---

### Task 3.2 — Define stage I/O contracts
**New file**: `pipeline/contracts.py`

```python
@dataclass
class MarketContext:
    timestamp: datetime
    regime: str
    regime_stability: int
    breadth_signal: str
    pcr_value: float
    fii_net: float
    sector_rotation: str
    nifty_return_1m: float

@dataclass
class FeatureSet:
    symbol: str
    ta_result: TechnicalResult
    sr_result: SupportResistanceResult
    pattern_result: PatternResult
    volume_profile: VolumeProfileResult
    mtf_result: MultiTimeframeResult
    earnings_days: int
    fno_banned: bool
    fii_vote: str
    pcr_vote: str
    sentiment_modifier: float    # Layer 3 only, ±0.10 range

@dataclass
class Allocation:
    signal: TradeSignal
    approved_size: int
    approved_size_pct: float
    allocation_rank: int         # position in candidate competition ranking
    opportunity_cost_score: float

@dataclass
class PipelineResult:
    executed: list[ExecutionResult]
    blocked: list[TradeSignal]
    abstained: list[TradeSignal]
    session_stats: dict
```

**Done when**: All dataclasses importable. No circular imports.

---

### Task 3.3 — Implement each stage as a method
**File**: `pipeline/runner.py`

Each stage:

| Stage | Input | Output | Calls |
|---|---|---|---|
| `context_builder` | config | `MarketContext` | market_regime, fii_dii, pcr_signal, sector_rotation, market_scanner |
| `candidate_generator` | `MarketContext` | `list[str]` | market_scanner, fno_ban, momentum_filter |
| `feature_generator` | symbols, `MarketContext` | `dict[str, FeatureSet]` | technical_agent, sr, patterns, volume_profile, mtf, earnings_guard, sentiment_agent |
| `signal_generator` | `FeatureSet`, `MarketContext` | `list[TradeSignal]` | strategy/engine.py (Layer 1) |
| `signal_enricher` | `list[TradeSignal]`, `MarketContext` | `list[TradeSignal]` | market_permission (Layer 2), dynamic_sizing (Layer 3) |
| `risk_gate_stage` | `list[TradeSignal]` | `list[TradeSignal]` | risk/risk_gate.py |
| `abstention_stage` | `list[TradeSignal]` | `list[TradeSignal]` | strategy/abstention.py |
| `execution_planner` | `list[TradeSignal]` | `list[Allocation]` | portfolio ranking (added fully in Phase 6) |
| `executor_stage` | `list[Allocation]`, dry_run | `list[ExecutionResult]` | execution/executor.py |
| `post_trade_recorder` | `list[ExecutionResult]` | None | memory, telegram, snapshot |

**Done when**: `TradingPipeline().run(dry_run=True)` produces the same output as the current `main.py` run.

---

### Task 3.4 — Keep `main.py` as a thin wrapper
**File**: `main.py`

Reduce `main.py` to:
```python
from pipeline.runner import TradingPipeline
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    TradingPipeline().run(dry_run=args.dry_run)
```

**Done when**: `main.py` is under 20 lines. All logic lives in `pipeline/runner.py`.

---

### Task 3.5 — Add test suite
**New directory**: `tests/`

Files:
- `tests/test_risk_gate.py` — unit tests for all risk gate checks with synthetic signals
- `tests/test_abstention.py` — unit tests for all abstention rules
- `tests/test_pipeline_integration.py` — inject synthetic `FeatureSet` and `MarketContext`, assert `DecisionJournal` is fully populated, assert blocked signals reach `decision_journals` table
- `tests/test_signal_layers.py` — assert Layer 1 vote list contains no macro inputs, Layer 2 contains no TA inputs

**Done when**: `pytest tests/` passes with all tests green.

---

### Phase 3 Done When:
- [ ] `pipeline/runner.py` exists with all 9 stages
- [ ] `pipeline/contracts.py` defines all I/O types
- [ ] `main.py` is under 20 lines
- [ ] `TradingPipeline().run(dry_run=True)` produces equivalent output to old `main.py`
- [ ] `pytest tests/` passes

---

---

# PHASE 4 — Memory as Learning Feedback Loop
**Goal**: Historical outcomes actively inform future decisions.  
**Estimated time**: 5–6 days  
**Prerequisite**: Phase 1 (journal must be stored)

---

### Task 4.1 — Upgrade outcome tracker
**File**: `analysis/outcome_tracker.py`

Changes:
- After each market session, for each signal from the previous 1, 3, 5 days: fetch current mark price and compute return
- Call `memory.update_journal_outcome(signal_id, horizon="1d", return_pct=...)` for each
- On trade close (exit): call with `horizon="exit"` and actual realized return
- Track against original `p_direction` at signal time: store `(p_direction, outcome_1d, outcome_3d, outcome_exit)` tuples for calibration

**Done when**: `decision_journals` table has non-null `outcome_1d` values for signals older than 1 trading day.

---

### Task 4.2 — Create calibration module
**New file**: `analysis/calibration.py`

Class `ConfidenceCalibrator` with methods:

`compute_module_calibration(min_trades=30) -> CalibrationReport`:
- For each module in `layer1_votes`, `layer2_votes`: compute win rate when it voted BUY
- Split by regime (Bull / Bear / Sideways / Recovery)
- Returns `CalibrationReport` per module per regime

`compute_confidence_calibration() -> dict`:
- Bucket signals by `p_direction` band: 0.50–0.59, 0.60–0.69, 0.70–0.79, 0.80+
- For each band: compute actual win rate from `outcome_exit`
- Returns `{band: (stated_p, actual_win_rate, correction_factor)}`

`detect_overconfidence(threshold=0.10) -> list[str]`:
- Returns list of `(regime, setup_type)` pairs where stated confidence exceeds actual by > threshold

Store `CalibrationReport` in memory as JSON in new `calibration_reports` table.

**Done when**: After 30+ trades in any module, `compute_module_calibration()` returns a report with actual win rates. Dashboard can display it (Task 4.4).

---

### Task 4.3 — Extend portfolio memory stats
**File**: `memory/portfolio_memory.py`

Extend `get_stats()` to segment by:
- `setup_type` — momentum / reversal / breakout / other
- `regime` at signal time
- `p_direction` band (0.50–0.59, 0.60–0.69, 0.70–0.79, 0.80+)

New method `get_calibration_data() -> list[dict]`:
- Returns all `(signal_id, p_direction, outcome_exit, regime, setup_type)` rows for calibration calculations

Fix timestamp issue: add timestamp to sentiment momentum cache, invalidate if > 24h old (`analysis/sentiment_agent.py`).

**Done when**: `get_stats()` returns per-regime, per-setup-type breakdowns. `get_calibration_data()` returns all completed trades.

---

### Task 4.4 — Dashboard: calibration panels
**File**: `dashboard/app.py`

Add two panels:

**Module Calibration Heatmap**: grid of regime × module showing actual win rate. Green = well-calibrated (>50%), Red = underperforming (<45%). Yellow = insufficient data (<30 trades).

**Confidence Calibration Plot**: bar chart showing `p_direction` band vs actual win rate. A well-calibrated system has bars close to the diagonal. Overconfident bands shown in orange.

**Done when**: Both panels render from `CalibrationReport` data. Dashboard shows "insufficient data" gracefully when < 30 trades exist.

---

### Phase 4 Done When:
- [ ] `outcome_tracker.py` updates `decision_journals` with 1d/3d/5d/exit returns
- [ ] `analysis/calibration.py` produces `CalibrationReport` from journal data
- [ ] `get_calibration_data()` returns completed trade rows
- [ ] Dashboard shows module calibration heatmap and confidence calibration plot
- [ ] Sentiment cache has timestamps, invalidated after 24h

---

---

# PHASE 5 — Backtest Upgrade + Ablation Tests
**Goal**: Backtest reflects real pipeline behavior. Ablation tests reveal which modules drive actual edge.  
**Estimated time**: 6–8 days  
**Prerequisite**: Phase 3 (pipeline stages must exist to replay)

---

### Task 5.1 — Fix survivorship bias
**Data file**: `data/nse500_historical_membership.csv`

Create/obtain a CSV with columns: `symbol, added_date, removed_date`. Sources: NSE historical index announcements.

**File**: `backtest/engine.py`  
Change: when loading a symbol's history, clip it to `[added_date, removed_date]`. Symbols not in the file at a given backtest date are excluded from that date's universe.

**Done when**: Backtest does not include a delisted stock's post-delisting performance.

---

### Task 5.2 — Full pipeline replay mode
**File**: `backtest/engine.py`

Add `full_pipeline_replay: bool = False` parameter to `BacktestEngine.run()`.

When `True`:
- `context_builder` uses historical Nifty data (yfinance) to compute regime per bar
- `signal_enricher` runs Layer 2 market permission
- `risk_gate_stage` runs with historical portfolio state
- `abstention_stage` runs
- Earnings guard uses historical earnings calendar (store in `data/earnings_calendar.csv`)
- VIX history fetched from yfinance for dynamic sizing

When `False` (fast mode): existing simplified logic. Fast mode kept for quick iteration.

**Done when**: `BacktestEngine(full_pipeline_replay=True).run("RELIANCE", "2024-01-01", "2024-12-31")` produces results that include regime-blocked sessions and abstained signals.

---

### Task 5.3 — Ablation test runner
**New file**: `backtest/ablation.py`

```python
ABLATION_RUNS = [
    {"name": "Baseline",          "disabled_modules": []},
    {"name": "No Sentiment",      "disabled_modules": ["sentiment"]},
    {"name": "No Patterns",       "disabled_modules": ["pattern_recognition"]},
    {"name": "No Volume Profile", "disabled_modules": ["volume_profile"]},
    {"name": "No Sector Rotation","disabled_modules": ["sector_rotation"]},
    {"name": "No PCR",            "disabled_modules": ["pcr_signal"]},
    {"name": "No Block Deals",    "disabled_modules": ["block_deals"]},
    {"name": "No FII/DII",        "disabled_modules": ["fii_dii"]},
    {"name": "Regime+Trend+Risk Only", "disabled_modules": [
        "sentiment", "pattern_recognition", "volume_profile",
        "sector_rotation", "pcr_signal", "block_deals"
    ]},
]
```

`AblationRunner.run(symbols, start_date, end_date)`:
- For each run: set disabled modules in config, run full-pipeline backtest, collect Sharpe / win rate / max drawdown / profit factor
- Output: comparison table sorted by Sharpe
- Modules where disabling them improves Sharpe: candidates for removal
- Modules where disabling them collapses Sharpe: confirmed keepers

This directly answers: "Which 3 inputs actually drive PnL?"

**Done when**: `AblationRunner.run(["RELIANCE","INFY"], "2024-01-01", "2024-12-31")` produces a sorted comparison table. At least one module shows positive delta from removal.

---

### Task 5.4 — Regime-segmented results
**File**: `backtest/engine.py`

Add to `BacktestResult`:
```python
regime_breakdown: dict = {
    "Bull":     {"win_rate": float, "avg_trade": float, "sharpe": float, "n_trades": int},
    "Bear":     {...},
    "Sideways": {...},
    "Recovery": {...},
}
```

Tag each backtest trade with the regime at entry time. Aggregate at end.

**Done when**: `BacktestResult.regime_breakdown` populated with per-regime stats.

---

### Task 5.5 — Per-module attribution in backtest
**File**: `backtest/engine.py`

For each trade in full-pipeline replay mode, the `DecisionJournal` is populated. After run, aggregate: for each module that voted BUY on winning trades, count. For each module that voted BUY on losing trades, count.

Output in `BacktestResult.module_attribution`:
```
technical:      voted BUY on 87 trades, 62% winners
fii_dii:        voted BUY on 43 trades, 71% winners
pcr_signal:     voted BUY on 61 trades, 48% winners  ← underperforming
volume_profile: voted BUY on 55 trades, 58% winners
```

**Done when**: `BacktestResult.module_attribution` has per-module winner rates.

---

### Task 5.6 — Paper-vs-backtest drift analysis
**New file**: `backtest/drift_analysis.py`

Monthly: compare actual paper returns vs what full-pipeline backtest predicts for same period and symbols.

```python
DriftReport:
    period: str
    paper_return: float
    backtest_return: float
    drift_pct: float
    likely_causes: list[str]   # "data staleness", "regime misclassification", etc.
```

If `drift_pct > 5.0`, send Telegram alert: "Paper/backtest drift {drift_pct:.1f}% — investigate data quality."

**Done when**: `DriftAnalyser.run()` produces a `DriftReport` and sends alert if over threshold.

---

### Task 5.7 — Slippage stress test
**File**: `backtest/engine.py`

Add `slippage_multiplier: float = 1.0` parameter.

`BacktestEngine.stress_test(symbols, start, end)`:
- Run with `slippage_multiplier` = 1.0, 2.0, 3.0
- Run with `execution_delay_mins` = 0, 5, 10
- Report how Sharpe changes under each scenario
- If Sharpe collapses to < 0.5 under 2× slippage: flag "strategy is fragile to execution costs"

**Done when**: `BacktestEngine.stress_test()` produces a 6-row table (3 slippage × 2 delay scenarios) with Sharpe per scenario.

---

### Phase 5 Done When:
- [ ] `nse500_historical_membership.csv` exists and backtest respects it
- [ ] `full_pipeline_replay=True` runs all live pipeline stages historically
- [ ] Ablation table produced with at least 8 module comparisons
- [ ] `BacktestResult.regime_breakdown` populated
- [ ] `BacktestResult.module_attribution` populated
- [ ] Drift analysis runs monthly and sends alert if > 5%
- [ ] Stress test table produced under 2× and 3× slippage

---

---

# PHASE 6 — Meta-Decision Engine
**Goal**: Regime-conditional module weighting from real data. Portfolio candidate competition replaces "top N."  
**Estimated time**: 7–10 days  
**Prerequisite**: Phase 4 complete (needs calibration data — run 50+ trades first before starting this phase)

---

### Task 6.1 — Regime-conditional module weight table
**New file**: `strategy/regime_weights.py`

Initial priors (replace after Phase 5 ablation tests confirm which modules matter):
```python
REGIME_MODULE_WEIGHTS = {
    "Bull": {
        "technical":          0.40,
        "momentum":           0.25,
        "volume_profile":     0.20,
        "fii_dii":            0.15,
    },
    "Bear": {
        "market_regime":      0.45,
        "short_signals":      0.30,
        "fii_dii":            0.25,
    },
    "Recovery": {
        "technical":          0.35,
        "fii_dii":            0.30,
        "support_resistance": 0.20,
        "volume_profile":     0.15,
    },
    "Sideways": {
        "support_resistance": 0.40,
        "volume_profile":     0.30,
        "technical":          0.20,
        "pcr_signal":         0.10,
    },
}
# Modules not listed for a regime contribute zero weight.
# Modules that ablation tests (Phase 5) showed no edge are removed entirely.
```

`RegimeWeightManager.get_weights(regime: str) -> dict`:
- Returns weight dict for current regime
- If Phase 4 calibration data exists (≥50 trades per regime): replaces priors with data-derived weights
- Monthly recalibration via `CalibrationReport`

**Done when**: `RegimeWeightManager.get_weights("Bull")` returns the correct weight dict. Returns calibration-derived weights after 50+ trades.

---

### Task 6.2 — Apply regime weights in Layer 1
**File**: `strategy/engine.py`

In `signal_generator` stage: instead of fixed weights per module, call `RegimeWeightManager.get_weights(ctx.regime)`. Apply dynamic weights when computing `p_direction`.

Modules with weight `0.0` in the current regime contribute nothing to `p_direction` (not just down-weighted — their vote is skipped entirely).

Record the weight used in each `ModuleVote.weight` field in the journal.

**Done when**: In Bear regime, `technical` module vote is weighted at 0.10 (not 0.40 as in Bull). Journal records the regime-specific weight per vote.

---

### Task 6.3 — Redundancy detection
**File**: `strategy/regime_weights.py`

`RedundancyDetector.compute(days=90) -> dict`:
- Load all `layer1_votes` from `decision_journals` in last 90 days
- Compute pairwise vote agreement rate between all module pairs
- Return dict: `{(module_a, module_b): agreement_rate}`
- Any pair with `agreement_rate > 0.85`: flag as "redundant"
- Redundant module: halve its weight in `REGIME_MODULE_WEIGHTS`

Run monthly. Surface in dashboard: "X modules currently redundant."

**Done when**: `RedundancyDetector.compute()` returns pairwise agreement rates and flags high-agreement pairs.

---

### Task 6.4 — Confidence calibration correction
**File**: `strategy/engine.py`

After computing `p_direction` from module votes:
- Load correction factors from `calibration_corrections` table (computed in Phase 4)
- Apply correction: `p_direction_calibrated = p_direction × correction_factor[regime][setup_type]`
- If no correction available (< 30 trades): use uncorrected value
- Log correction factor applied to `DecisionJournal`

**Done when**: In a regime where the system is overconfident by 15%, `p_direction` is scaled down by the correction factor.

---

### Task 6.5 — Portfolio candidate competition (execution planner)
**File**: `pipeline/runner.py` → `execution_planner` stage  
**New file**: `strategy/execution_planner.py`

Replace "take top N signals above threshold" with:

```python
def rank_candidates(signals: list[TradeSignal], portfolio_state) -> list[Allocation]:
    scored = []
    for sig in signals:
        correlation_cost = compute_correlation_cost(sig, portfolio_state)
        score = (sig.expected_value × sig.p_direction) / (sig.execution_risk + correlation_cost + 0.01)
        scored.append((score, sig))

    scored.sort(key=lambda x: x[0], reverse=True)

    allocations = []
    remaining_heat = MAX_PORTFOLIO_HEAT - portfolio_state.current_heat
    for rank, (score, sig) in enumerate(scored):
        if remaining_heat <= 0:
            sig.journal.abstention_reason = "capital budget exhausted"
            continue
        alloc = Allocation(signal=sig, allocation_rank=rank+1, ...)
        allocations.append(alloc)
        remaining_heat -= sig.position_size_pct

    # Log rejected candidates with reason
    for _, sig in scored[len(allocations):]:
        log_opportunity_cost_rejection(sig, "capital competition")

    return allocations
```

**Done when**: When 8 signals pass all gates, but portfolio heat allows only 4, the top 4 by score are allocated and the bottom 4 are logged as opportunity cost rejections.

---

### Task 6.6 — Opportunity cost tracking
**File**: `strategy/execution_planner.py`

For each capital-competition rejection: record in `decision_journals` with `abstention_reason = "opportunity_cost: rank {N}"`.

Monthly: query these records. For rejected signals, look up `outcome_exit`. If rejection winners > executed winners: surface "limits may be too tight" alert.

**Done when**: Opportunity cost rejection outcomes are tracked and available for monthly review.

---

### Task 6.7 — Dashboard: meta-decision panels
**File**: `dashboard/app.py`

Add:
- **Regime weight viewer**: current module weights by regime, highlights zero-weight modules
- **Redundancy monitor**: current high-agreement module pairs
- **Candidate competition table**: today's ranked signals, who was allocated, who was rejected and why, what their scores were

**Done when**: All three panels render correctly from live data.

---

### Phase 6 Done When:
- [ ] `regime_weights.py` returns calibration-derived weights after 50+ trades
- [ ] Zero-weight modules skipped entirely in signal generation
- [ ] Redundancy detector runs monthly and halves weights of high-agreement pairs
- [ ] `p_direction` corrected by calibration factors from Phase 4
- [ ] Candidate competition ranks all passing signals, allocates by score
- [ ] Opportunity cost rejections logged and tracked
- [ ] Dashboard shows all three new panels

---

---

# PHASE 7 — Research vs Production Separation
**Goal**: Protect the live system from experimental code. Enable safe iteration without risk of contamination.  
**Estimated time**: 3–4 days  
**Prerequisite**: Phase 3 (pipeline stages must be cleanly separated)

---

### Task 7.1 — Create `research/` directory
**New directory**: `research/`

Structure:
```
research/
├── experiments/         # new signals and analyzers under test
├── ablations/           # ablation results and notes
├── notebooks/           # analysis notebooks
├── sandbox_pipeline.py  # copy of pipeline that reads from research DB
└── research.db          # separate SQLite DB — never shared with production
```

Rule: nothing in `research/` may import from `pipeline/runner.py`. The sandbox pipeline is a copy, not a reference.

**Done when**: Directory exists. `research/sandbox_pipeline.py` runs independently from a separate DB.

---

### Task 7.2 — Define production promotion gate
**New file**: `research/promotion_checklist.py`

A module or feature is promoted from research to production only after all of these pass:

```python
PROMOTION_REQUIREMENTS = [
    "ablation_test_shows_positive_edge",          # Phase 5 ablation result > 0 Sharpe delta
    "paper_trades_count >= 30",                   # run for ≥ 30 paper trades
    "paper_expectancy > 0",                       # positive expected value
    "readiness_checker_passes",                   # readiness/checker.py validation
    "manual_approval_logged",                     # operator approval recorded with date
]
```

`PromotionChecker.evaluate(module_name) -> PromotionReport`:
- Returns status per requirement, overall pass/fail, and notes

**Done when**: `PromotionChecker.evaluate("new_volume_analyzer")` returns a report listing which requirements are met and which are pending.

---

### Task 7.3 — Per-asset validation gate
**File**: `readiness/checker.py`

Add checks: before F&O, crypto, or US asset class can be enabled in production:
- Separate `FeatureSet` fields defined for the asset class
- Separate Layer 1/2/3 analysis modules configured
- Separate risk parameters in `config.py` (not shared with NSE spot)
- 30+ paper trades with positive expectancy
- `readiness/checker.py` must pass for that asset class

Current status: F&O, crypto, US are frozen at Research lane until NSE Indian equities completes Phase 6.

Add `ASSET_CLASS_GATES` to `config.py`:
```python
ASSET_CLASS_GATES = {
    "nse_spot":  {"enabled": True,  "phase_required": 0},
    "fno":       {"enabled": False, "phase_required": 6},
    "crypto":    {"enabled": False, "phase_required": 6},
    "us_equities": {"enabled": False, "phase_required": 6},
}
```

**Done when**: F&O execution path hard-gated by `ASSET_CLASS_GATES["fno"]["enabled"]`. Cannot be enabled without Phase 6 completion.

---

### Task 7.4 — Dashboard: research vs production activity
**File**: `dashboard/app.py`

Add panel: **System Status**
- Production: active modules, current phase, last promotion date
- Research: experiments in progress, ablation results pending, modules awaiting promotion
- Asset classes: enabled / gated per `ASSET_CLASS_GATES`

**Done when**: Dashboard shows live vs research module status and asset class gate states.

---

### Phase 7 Done When:
- [ ] `research/` directory with sandbox pipeline and separate DB
- [ ] `PromotionChecker.evaluate()` returns correct pass/fail per requirement
- [ ] F&O, crypto, US gated in `ASSET_CLASS_GATES`
- [ ] `readiness/checker.py` enforces asset class gates
- [ ] Dashboard shows system status with production vs research separation

---

---

## Master Summary

| Phase | Name | Goal | Days | Prerequisite |
|---|---|---|---|---|
| **0** | Critical Bug Fixes | Stop silent wrong results | 2–3 | None |
| **1** | Three-Layer Signal + Journal | Replace confidence soup, build audit trail | 5–6 | Phase 0 |
| **2** | Unified Risk Gate + Abstention | Risk-first, no-trade is valid | 4–5 | Phase 1 |
| **3** | Pipeline Refactor | Named stages, testable, typed I/O | 4–5 | Phase 1+2 |
| **4** | Memory Feedback Loop | Outcomes inform future decisions | 5–6 | Phase 1 |
| **5** | Backtest + Ablation | Trust the numbers, find real edge | 6–8 | Phase 3 |
| **6** | Meta-Decision Engine | Evidence-based weights, portfolio competition | 7–10 | Phase 4 |
| **7** | Research vs Production | Safe iteration, asset class gates | 3–4 | Phase 3 |
| **Total** | | | **36–47 days** | Sequential |

---

## The Three Questions That Define Success

**After Phase 0–3**: Can I trust what the system is doing?
> Bugs fixed. Three clean layers. Full audit trail. One unified risk gate.

**After Phase 4–5**: Is what the system is doing actually working?
> Calibrated confidence. Ablation evidence. Pipeline-faithful backtest.

**After Phase 6–7**: Is the system getting better over time?
> Evidence-based weights. Calibration corrections. Research promotion gate. Fewer but better trades.

---

## The System Identity This Plan Builds Toward

> A risk-first portfolio decision engine that knows why it made each decision,
> tracks whether that decision was right,
> learns from the history,
> and says no more often than it says yes.
