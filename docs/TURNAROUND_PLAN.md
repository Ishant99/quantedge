# QuantEdge — Turnaround Plan

_Generated: 2026-06-10. Companion to `GAP_ANALYSIS.md` (bug-level fixes).
This document addresses the strategic question: **why 3 months of paper trading
produced neither profits nor learning, and what to change about the approach itself.**_

---

## The Honest Diagnosis

After 3 months: win rate 27.8%, F&O Rs.-30,657 in the last 30 days, drift 0.581 (HALT).
The gap analysis lists ~40 fixable defects — but fixing all of them only makes the
system *lose more slowly* unless the deeper problems are addressed:

### 1. The core signal was never validated — and the live data says it has no edge

- The default setup `technical_base` (a weighted blend of ~10 TA indicators) drives
  nearly all trades. It was never put through the system's own promotion gate.
- **The promotion framework already exists** (`research/promotion_checklist.py`) and
  requires: ablation shows positive edge, ≥30 paper trades, positive expectancy,
  readiness check, manual approval. **The production strategy itself would fail this
  checklist today** (27.8% WR at 2:1 R:R = expectancy ≈ -0.17R per trade).
- The backtest said the strategy works; live says it doesn't (drift 0.581). The
  backtest is the one that's wrong: optimizer tuned on 7 hand-picked large-caps,
  costs modeled at 0.03%, daily-bar fills. Classic overfit.

### 2. Sample starvation — the system cannot learn at its current trade rate

- ~9 trades/month, spread across 4 asset classes and 3 setup types.
- To statistically validate ONE setup you need ~30-50 resolved trades.
  At the current rate that is **10+ months per setup**. The calibration system,
  Kelly sizing, strategy-quality scoring, and module attribution are all starving:
  every feedback loop in the system is built but sits below its minimum sample size.
- This is why 3 months produced no learning: **the learning machinery is fine,
  the data velocity is ~10× too low.**

### 3. Complexity exceeds the evidence

- 24 scheduler jobs, 9 pipeline stages, ~20 analysis modules, 4 asset classes —
  built on top of a signal with negative measured expectancy.
- Every trade outcome is attributed across dozens of factors, so no single factor
  ever accumulates enough evidence to be confirmed or killed.
- The system optimizes *execution* of signals that have no demonstrated edge.

### 4. F&O was the worst possible proving ground

- Long weekly options must overcome theta decay, IV crush, bid-ask spread, AND get
  direction right on a short clock. It is the hardest game in the market.
- Sizing underestimated risk 3-5× (see GAP_ANALYSIS #1), so the hardest game was
  also played with the biggest bets. Result: 99% of all losses.

---

## The Strategy: Validate → Focus → Scale

The purpose of paper trading is **not to simulate a finished system — it is to
generate statistically valid evidence as fast as possible.** Optimize for
resolved-trades-per-week per setup, not for simulated P&L.

### Phase 0 — Stop and reset (Day 1)

| Action | Detail |
|--------|--------|
| **Disable F&O entirely** | Set the asset-class gate off. No new F&O entries until an equity edge is proven AND spreads replace naked buys. F&O is 99% of losses and 0% of learning. |
| **Disable crypto + US scanning for entries** | Same logic — focus all sample-collection on one market (NSE) where data and liquidity are best understood. Keep monitors for open positions only. |
| **Apply GAP_ANALYSIS Phase-1 safety fixes** | Drawdown breaker, calibration threshold 10→5, WAL/indices/backups. |

### Phase 1 — Honest revalidation (Week 1-2)

Re-run the research loop the system was designed for but never executed:

1. **Walk-forward backtest each setup SEPARATELY** — `technical_base`,
   `breakout_52w`, `rsi2_mean_reversion` — on the full NSE500 universe (not 7 symbols),
   with realistic costs (0.10% round-trip + slippage), strict out-of-sample splits
   (train 2 years → test 6 months, rolled forward).
2. **Segment results by regime** (bull/sideways/bear/recovery). A setup only counts
   as validated in the regimes where it tested positive.
3. **Kill anything with OOS expectancy ≤ 0.** Expected outcome: `technical_base` as
   currently weighted dies or gets restructured; `rsi2_mean_reversion` (a historically
   documented edge) and `breakout_52w` likely survive in specific regimes.
4. **Run the ablation framework** (`backtest/ablation.py` — built, never used) to find
   which of the ~20 modules actually add edge. Drop the rest from the vote.

### Phase 2 — High-velocity focused paper trading (Week 2-6)

1. **Trade only the 1-2 setups that survived Phase 1**, only in their validated regimes.
2. **Increase trade frequency deliberately**: widen the universe scan, relax the
   TOP_N cap for these setups, and use small fixed sizes. In paper mode the goal is
   sample size, not P&L — target **15-25 resolved trades/month per setup**
   (vs ~9/month total today).
3. **Champion/challenger structure**: run the focused book ("challenger") alongside
   the current full pipeline ("champion") as two separate paper portfolios with
   separate P&L and scorecards. This makes the comparison explicit instead of
   anecdotal. (The treasury already supports per-bucket allocation.)
4. **Weekly per-setup scorecard** (extend `scripts/review.py`): resolved trades,
   win rate with **Wilson 95% lower bound**, expectancy in R, vs. its backtest
   expectation. The Wilson lower bound is the decision number — not the raw win rate.

### Phase 3 — Statistical decision gates (Month 2-3)

Pre-committed rules, evaluated by the scheduler, not by feel:

| Gate | Rule | Action |
|------|------|--------|
| **Kill** | After 30 resolved trades: Wilson 95% lower bound of win rate < breakeven WR for its R:R | Auto-disable setup, Telegram alert |
| **Probation** | After 20 trades: expectancy < -0.1R | Halve size, flag for review |
| **Scale** | After 50 trades: Wilson lower bound > breakeven + 5pp | Increase size step (e.g., 1% → 1.5% risk) |
| **Promote to live-candidate** | Passes the existing `PromotionChecklist` (all 5 requirements) | Eligible for real capital discussion |

This is exactly what `research/promotion_checklist.py` was built for — wire it into
the monthly scheduler job instead of leaving it as an unused library.

### Phase 4 — Re-expand only on evidence (Month 3+)

- **F&O returns only as defined-risk spreads** (bull call / bear put), only after the
  underlying directional signal has passed its equity-market gates, and only with
  risk-based lot sizing (GAP_ANALYSIS Phase-1 fix #1).
- **Short-side** enters the same way: validate short setups in backtest first, then
  route `ShortSignalGenerator` to F&O PE-spreads in confirmed bear regimes.
- **Crypto/US** re-enable one at a time, each through the same promotion gate.
- Add one new candidate setup per month at most, through the research sandbox
  (`research/sandbox_pipeline.py` — also already built).

---

## What Success Looks Like (90-day checkpoint)

| Question | Evidence required |
|----------|-------------------|
| Do we have a validated edge? | ≥1 setup with 50+ resolved paper trades and Wilson lower bound above breakeven |
| Is the model honest? | Drift < 0.20; all calibration bands active; stated vs actual confidence within 5pp |
| Is risk bounded? | Zero trades exceeding intended risk; max drawdown < 10%; no asset class below its monthly loss limit |
| Are we learning faster? | ≥40 resolved trades/month system-wide (vs ~9 today) |

If after 90 days **no setup passes its gate**, the honest conclusion is that the
current signal families (daily-bar TA on liquid NSE names) carry no retail-accessible
edge, and the next iteration should change the *signal source* (e.g., event-driven,
cross-sectional momentum, volatility risk premium via defined-risk selling) rather
than continue tuning execution.

---

## Decision Summary

1. **Stop**: F&O, crypto, US entries — today.
2. **Validate**: per-setup walk-forward on full universe with real costs — this week.
3. **Focus**: 1-2 surviving setups, high trade velocity, champion/challenger — weeks 2-6.
4. **Gate**: pre-committed Wilson-bound kill/scale rules run by the scheduler — month 2+.
5. **Re-expand**: one asset class / setup at a time, each through the existing
   promotion checklist — month 3+.
