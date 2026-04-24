# =============================================================================
# pipeline/runner.py — 9-stage typed trading pipeline
#
# Replaces the monolithic logic in main.py with clearly bounded stages.
# Each stage logs timing, catches per-symbol errors, and passes a shared
# MarketContext through so every stage has access to regime/macro data.
#
# Usage:
#   from pipeline.runner import TradingPipeline
#   result = TradingPipeline(mode="paper").run(symbols, symbol_names, symbol_sectors)
# =============================================================================

from __future__ import annotations

import sys
import os
import time
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import VIRTUAL_CAPITAL, MAX_OPEN_POSITIONS, TRADING_MODE
from utils import get_logger
from pipeline.contracts import MarketContext, FeatureSet, Allocation, PipelineResult

logger = get_logger("Pipeline")


class TradingPipeline:
    """
    9-stage typed pipeline. Each stage has a clear input/output contract.

    Stage 1: market_context    — fetch regime, PCR, FII, breadth
    Stage 2: data_fetch        — download OHLCV for all symbols
    Stage 3: technical         — run TechnicalAgent on each symbol
    Stage 4: sentiment         — run SentimentAgent on each symbol
    Stage 5: signal_gen        — run StrategyEngine.generate() per symbol
    Stage 6: layer2_permission — run MarketPermission.evaluate() per signal
    Stage 7: risk_gate         — run RiskGate.check() per signal
    Stage 8: sizing            — run DynamicPositionSizer.calculate() per approved signal
    Stage 9: execution         — route to PaperExecutor or LiveExecutor
    """

    def __init__(
        self,
        mode: str = "paper",
        portfolio_value: Optional[float] = None,
        memory=None,
    ):
        self.mode = mode or TRADING_MODE
        self._portfolio_value = portfolio_value
        self._memory = memory

        # Lazy imports — resolved on first use to avoid circular imports
        self._regime_filter = None
        self._pcr_analyser = None
        self._fii_tracker = None
        self._breadth_analyser = None
        self._sector_analyser = None
        self._market_scanner = None
        self._ta_agent = None
        self._sent_agent = None
        self._strategy_engine = None
        self._market_permission = None
        self._risk_gate = None
        self._position_sizer = None
        self._executor = None

    # ------------------------------------------------------------------
    # Lazy accessors — each import is deferred until first call
    # ------------------------------------------------------------------

    def _get_regime_filter(self):
        if self._regime_filter is None:
            from analysis.market_regime import MarketRegimeFilter
            self._regime_filter = MarketRegimeFilter()
        return self._regime_filter

    def _get_pcr_analyser(self):
        if self._pcr_analyser is None:
            from analysis.pcr_signal import PCRAnalyser
            self._pcr_analyser = PCRAnalyser()
        return self._pcr_analyser

    def _get_fii_tracker(self):
        if self._fii_tracker is None:
            from analysis.fii_dii import FIIDIITracker
            self._fii_tracker = FIIDIITracker()
        return self._fii_tracker

    def _get_breadth_analyser(self):
        if self._breadth_analyser is None:
            from analysis.market_breadth import MarketBreadthAnalyser
            self._breadth_analyser = MarketBreadthAnalyser()
        return self._breadth_analyser

    def _get_sector_analyser(self):
        if self._sector_analyser is None:
            from analysis.sector_rotation import SectorRotationAnalyser
            self._sector_analyser = SectorRotationAnalyser()
        return self._sector_analyser

    def _get_ta_agent(self):
        if self._ta_agent is None:
            from analysis.technical_agent import TechnicalAgent
            self._ta_agent = TechnicalAgent()
        return self._ta_agent

    def _get_sent_agent(self):
        if self._sent_agent is None:
            from analysis.sentiment_agent import SentimentAgent
            self._sent_agent = SentimentAgent()
        return self._sent_agent

    def _get_strategy_engine(self):
        if self._strategy_engine is None:
            from strategy.engine import StrategyEngine
            self._strategy_engine = StrategyEngine()
        return self._strategy_engine

    def _get_market_permission(self):
        if self._market_permission is None:
            from strategy.market_permission import MarketPermission
            self._market_permission = MarketPermission()
        return self._market_permission

    def _get_risk_gate(self):
        if self._risk_gate is None:
            from risk.risk_gate import RiskGate
            self._risk_gate = RiskGate()
        return self._risk_gate

    def _get_position_sizer(self):
        if self._position_sizer is None:
            from risk.dynamic_sizing import DynamicPositionSizer
            self._position_sizer = DynamicPositionSizer()
        return self._position_sizer

    def _get_executor(self):
        if self._executor is None:
            from execution.executor import get_executor
            self._executor = get_executor()
        return self._executor

    # ------------------------------------------------------------------
    # Stage helpers
    # ------------------------------------------------------------------

    def _elapsed(self, t0: float) -> str:
        return f"{time.time() - t0:.2f}s"

    # ------------------------------------------------------------------
    # Stage 1 — Market Context
    # ------------------------------------------------------------------

    def _stage_market_context(self) -> MarketContext:
        t0 = time.time()
        logger.info("[Stage 1] market_context — start")

        regime_result = self._get_regime_filter().get_regime()
        regime = regime_result.regime
        stability = getattr(regime_result, "stability_count", 0)
        nifty_trend = regime_result.nifty_trend

        # PCR
        try:
            pcr = self._get_pcr_analyser().get_signal()
            pcr_signal = pcr.signal
        except Exception as exc:
            logger.warning(f"[Stage 1] PCR fetch failed — defaulting to neutral: {exc}")
            pcr_signal = "neutral"

        # FII/DII
        try:
            fii = self._get_fii_tracker().get_signal()
            fii_signal = fii.signal
        except Exception as exc:
            logger.warning(f"[Stage 1] FII fetch failed — defaulting to neutral: {exc}")
            fii_signal = "neutral"

        # Market breadth
        try:
            breadth = self._get_breadth_analyser().get_breadth()
            raw_breadth = breadth.breadth_signal  # e.g. strong_bull | bull | neutral | bear
            if "strong" in raw_breadth and "bull" in raw_breadth:
                breadth_signal = "strong"
            elif "bull" in raw_breadth:
                breadth_signal = "moderate"
            elif "bear" in raw_breadth and "strong" in raw_breadth:
                breadth_signal = "very_weak"
            elif "bear" in raw_breadth:
                breadth_signal = "weak"
            else:
                breadth_signal = "moderate"
        except Exception as exc:
            logger.warning(f"[Stage 1] Breadth fetch failed — defaulting to moderate: {exc}")
            breadth_signal = "moderate"

        # Sector scores
        sector_scores: dict = {}
        try:
            sector_result = self._get_sector_analyser().analyse()
            if hasattr(sector_result, "sector_scores"):
                sector_scores = sector_result.sector_scores
        except Exception as exc:
            logger.warning(f"[Stage 1] Sector fetch failed: {exc}")

        ctx = MarketContext(
            regime=regime,
            regime_stability=stability,
            pcr_signal=pcr_signal,
            fii_signal=fii_signal,
            breadth_signal=breadth_signal,
            nifty_trend=nifty_trend,
            sector_scores=sector_scores,
            timestamp=datetime.now(),
        )
        logger.info(
            f"[Stage 1] done ({self._elapsed(t0)}) — regime={regime} "
            f"pcr={pcr_signal} fii={fii_signal} breadth={breadth_signal}"
        )
        return ctx

    # ------------------------------------------------------------------
    # Stage 2 — Data Fetch
    # ------------------------------------------------------------------

    def _stage_data_fetch(self, symbols: list[str]) -> dict:
        """Returns {symbol: pd.DataFrame} with OHLCV data."""
        t0 = time.time()
        logger.info(f"[Stage 2] data_fetch — {len(symbols)} symbols")
        from data.market_scanner import MarketScanner
        scanner = MarketScanner(lookback_days=400)
        market_data = scanner.run(max_workers=10, regime="bull")
        # Only keep requested symbols that were actually fetched
        result = {sym: df for sym, df in market_data.items() if sym in symbols}
        logger.info(f"[Stage 2] done ({self._elapsed(t0)}) — {len(result)}/{len(symbols)} fetched")
        return result

    # ------------------------------------------------------------------
    # Stage 3 — Technical Analysis
    # ------------------------------------------------------------------

    def _stage_technical(self, market_data: dict, ctx: MarketContext) -> dict:
        """Returns {symbol: TAResult}."""
        t0 = time.time()
        logger.info(f"[Stage 3] technical — {len(market_data)} symbols")
        ta_results = self._get_ta_agent().analyse_all(market_data)
        bear_mode = ctx.regime in ("bear",)
        if bear_mode:
            tradeable = {sym: r for sym, r in ta_results.items()
                         if r.tradeable or r.signal == "bearish"}
        else:
            tradeable = {sym: r for sym, r in ta_results.items() if r.tradeable}
        logger.info(
            f"[Stage 3] done ({self._elapsed(t0)}) — "
            f"{len(tradeable)}/{len(ta_results)} tradeable"
        )
        return tradeable

    # ------------------------------------------------------------------
    # Stage 4 — Sentiment Analysis
    # ------------------------------------------------------------------

    def _stage_sentiment(
        self,
        symbols: list[str],
        symbol_names: dict,
        symbol_sectors: dict,
    ) -> dict:
        """Returns {symbol: SentimentResult}."""
        t0 = time.time()
        logger.info(f"[Stage 4] sentiment — {len(symbols)} symbols")
        sent_results = self._get_sent_agent().analyse_all(
            symbols,
            symbol_names=symbol_names or {},
            symbol_sectors=symbol_sectors or {},
        )
        logger.info(f"[Stage 4] done ({self._elapsed(t0)}) — {len(sent_results)} results")
        return sent_results

    # ------------------------------------------------------------------
    # Stage 5 — Signal Generation
    # ------------------------------------------------------------------

    def _stage_signal_gen(
        self,
        ta_results: dict,
        sent_results: dict,
        market_data: dict,
        ctx: MarketContext,
        portfolio_value: float,
        open_positions: int,
    ) -> list:
        """Returns list[TradeSignal]."""
        t0 = time.time()
        logger.info(f"[Stage 5] signal_gen — {len(ta_results)} candidates")
        from strategy.engine import StrategyEngine
        engine = self._get_strategy_engine()
        signals = engine.generate_all(
            ta_results=ta_results,
            sent_results=sent_results,
            market_data=market_data,
            portfolio_value=portfolio_value,
            open_positions=open_positions,
            position_size_multiplier=1.0,
            regime=ctx.regime,
            regime_stability=ctx.regime_stability,
        )
        logger.info(f"[Stage 5] done ({self._elapsed(t0)}) — {len(signals)} signals")
        return signals

    # ------------------------------------------------------------------
    # Stage 6 — Layer 2 Permission
    # ------------------------------------------------------------------

    def _stage_layer2_permission(
        self,
        signals: list,
        ctx: MarketContext,
        symbol_sectors: dict,
        sent_results: dict,
        earnings_guard,
        fno_ban,
    ) -> list:
        """
        Applies MarketPermission per signal.
        BLOCK signals get action="BLOCKED" on the TradeSignal object.
        Returns list[tuple[signal, PermissionResult]].
        """
        t0 = time.time()
        logger.info(f"[Stage 6] layer2_permission — {len(signals)} signals")
        permission_layer = self._get_market_permission()
        results = []
        for sig in signals:
            try:
                sector = symbol_sectors.get(sig.symbol, "") if symbol_sectors else ""
                sector_score = ctx.sector_scores.get(sector, 5.0)
                sector_signal = (
                    "bullish" if sector_score >= 7.0
                    else "bearish" if sector_score <= 3.0
                    else "neutral"
                )
                earnings_days = 999
                if earnings_guard is not None:
                    try:
                        earnings_days = earnings_guard.days_to_earnings(sig.symbol)
                    except Exception:
                        pass
                fno_banned = False
                if fno_ban is not None:
                    try:
                        fno_banned = fno_ban.is_banned(sig.symbol)
                    except Exception:
                        pass

                perm = permission_layer.evaluate(
                    symbol=sig.symbol,
                    action=sig.action,
                    regime=ctx.regime,
                    regime_stability=ctx.regime_stability,
                    pcr_signal=ctx.pcr_signal,
                    fii_signal=ctx.fii_signal,
                    sector_signal=sector_signal,
                    breadth_signal=ctx.breadth_signal,
                    earnings_days=earnings_days,
                    fno_banned=fno_banned,
                    journal=getattr(sig, "journal", None),
                )
                if perm.permission == "BLOCK":
                    sig.action = "BLOCKED"
                    sig.permission = "BLOCK"
                    sig.permission_reason = perm.reason
                    logger.debug(f"[Stage 6] BLOCKED {sig.symbol}: {perm.reason}")
                else:
                    sig.permission = perm.permission
                    sig.permission_reason = perm.reason
                results.append((sig, perm))
            except Exception as exc:
                logger.error(f"[Stage 6] permission error for {sig.symbol}: {exc}")
                results.append((sig, None))
        blocked = sum(1 for s, _ in results if s.action == "BLOCKED")
        logger.info(f"[Stage 6] done ({self._elapsed(t0)}) — {blocked} blocked")
        return results

    # ------------------------------------------------------------------
    # Stage 7 — Risk Gate
    # ------------------------------------------------------------------

    def _stage_risk_gate(
        self,
        permission_results: list,
        portfolio_state: dict,
        open_positions: int,
    ) -> list:
        """
        Applies RiskGate per signal.
        Signals that fail get action="ABSTAIN".
        Returns list[tuple[signal, PermissionResult, RiskGateResult]].
        """
        t0 = time.time()
        logger.info(f"[Stage 7] risk_gate — {len(permission_results)} signals")
        risk_gate = self._get_risk_gate()
        results = []
        for sig, perm in permission_results:
            if sig.action in ("BLOCKED",):
                results.append((sig, perm, None))
                continue
            try:
                rg = risk_gate.check(
                    signal=sig,
                    portfolio_state=portfolio_state,
                    open_positions_count=open_positions,
                )
                if not rg.passed:
                    sig.action = "ABSTAIN"
                    logger.debug(
                        f"[Stage 7] ABSTAIN {sig.symbol}: "
                        + "; ".join(b.get("reason", "") for b in rg.blocks)
                    )
                results.append((sig, perm, rg))
            except Exception as exc:
                logger.error(f"[Stage 7] risk_gate error for {sig.symbol}: {exc}")
                results.append((sig, perm, None))
        abstained = sum(1 for s, _, _ in results if s.action == "ABSTAIN")
        logger.info(f"[Stage 7] done ({self._elapsed(t0)}) — {abstained} abstained")
        return results

    # ------------------------------------------------------------------
    # Stage 8 — Dynamic Sizing
    # ------------------------------------------------------------------

    def _stage_sizing(
        self,
        risk_gate_results: list,
        ctx: MarketContext,
        sent_results: dict,
        symbol_sectors: dict,
        portfolio_value: float,
    ) -> list:
        """
        Sizes BUY signals that passed the risk gate.
        Returns list[Allocation].
        """
        t0 = time.time()
        sizer = self._get_position_sizer()
        sent_agent = self._get_sent_agent()
        allocations = []

        for sig, perm, rg in risk_gate_results:
            sizing = None
            abstained = sig.action in ("ABSTAIN", "BLOCKED")
            abstention_reason = ""

            if sig.action == "BUY" and rg is not None and rg.passed:
                try:
                    sent_result = sent_results.get(sig.symbol) if sent_results else None
                    sentiment_modifier = 0.0
                    if sent_result is not None:
                        sentiment_modifier = sent_agent.get_sizing_modifier(sent_result)

                    sector = (symbol_sectors or {}).get(sig.symbol, "")
                    sector_score = ctx.sector_scores.get(sector, 5.0)
                    sector_multiplier = 1.2 if sector_score >= 7.0 else 0.8 if sector_score <= 3.0 else 1.0

                    reduction = perm.reduction_factor if perm else 1.0

                    sizing = sizer.calculate(
                        symbol=sig.symbol,
                        confidence=sig.p_direction,
                        entry_price=sig.entry_price,
                        atr=getattr(sig, "_atr", 0.0) or (sig.entry_price * 0.015),
                        portfolio_value=portfolio_value,
                        pattern_bias=getattr(sig, "setup_type", "neutral"),
                        sector_multiplier=sector_multiplier * reduction,
                        regime_multiplier=1.0,
                        setup_type=getattr(sig, "setup_type", ""),
                        sentiment_modifier=sentiment_modifier,
                        journal=getattr(sig, "journal", None),
                    )
                except Exception as exc:
                    logger.error(f"[Stage 8] sizing error for {sig.symbol}: {exc}")

            if sig.action == "ABSTAIN":
                abstained = True
                abstention_reason = (
                    "; ".join(b.get("reason", "") for b in rg.blocks)
                    if rg and rg.blocks else "risk gate failed"
                )
            elif sig.action == "BLOCKED":
                abstained = True
                abstention_reason = sig.permission_reason

            allocations.append(Allocation(
                signal=sig,
                sizing=sizing,
                permission=perm,
                risk_passed=(rg.passed if rg is not None else (sig.action not in ("ABSTAIN", "BLOCKED"))),
                abstained=abstained,
                abstention_reason=abstention_reason,
            ))

        sized = sum(1 for a in allocations if a.sizing is not None)
        logger.info(f"[Stage 8] done ({self._elapsed(t0)}) — {sized} positions sized")
        return allocations

    # ------------------------------------------------------------------
    # Stage 9 — Execution
    # ------------------------------------------------------------------

    def _stage_execution(self, allocations: list) -> list:
        """Routes actionable signals to executor. Returns allocations unchanged."""
        t0 = time.time()
        executor = self._get_executor()
        actionable = [a for a in allocations if a.signal.action in ("BUY", "SELL") and not a.abstained]
        logger.info(f"[Stage 9] execution — {len(actionable)} actionable signals")
        for alloc in actionable:
            try:
                result = executor.execute(alloc.signal)
                status = result.get("status", "unknown") if isinstance(result, dict) else str(result)
                logger.info(f"[Stage 9] {alloc.signal.action} {alloc.signal.symbol} → {status}")
                if self._memory is not None:
                    try:
                        sig_id = self._memory.save_signal(alloc.signal)
                        if result.get("status") == "filled" and sig_id:
                            self._memory.mark_signal_executed(sig_id)
                    except Exception as mem_exc:
                        logger.warning(f"[Stage 9] memory save failed for {alloc.signal.symbol}: {mem_exc}")
            except Exception as exc:
                logger.error(f"[Stage 9] execution error for {alloc.signal.symbol}: {exc}")
        logger.info(f"[Stage 9] done ({self._elapsed(t0)})")
        return allocations

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    def run(
        self,
        symbols: list[str],
        symbol_names: dict = None,
        symbol_sectors: dict = None,
    ) -> PipelineResult:
        """
        Orchestrate all 9 stages and return a PipelineResult.
        Per-symbol errors are caught and appended to result.errors.
        """
        pipeline_start = time.time()
        run_ts = datetime.now()
        errors: list[str] = []
        logger.info("=" * 60)
        logger.info(f"  TradingPipeline.run() — {run_ts.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"  mode={self.mode}  symbols={len(symbols)}")
        logger.info("=" * 60)

        # ---- Stage 1: Market Context ---------------------------------
        try:
            ctx = self._stage_market_context()
        except Exception as exc:
            logger.error(f"[Stage 1] fatal: {exc}")
            ctx = MarketContext(
                regime="bull", regime_stability=0,
                pcr_signal="neutral", fii_signal="neutral",
                breadth_signal="moderate", nifty_trend="flat",
                sector_scores={}, timestamp=run_ts,
            )
            errors.append(f"stage1_market_context: {exc}")

        # ---- Stage 2: Data Fetch -------------------------------------
        try:
            market_data = self._stage_data_fetch(symbols)
        except Exception as exc:
            logger.error(f"[Stage 2] fatal: {exc}")
            errors.append(f"stage2_data_fetch: {exc}")
            return PipelineResult(
                timestamp=run_ts, regime=ctx.regime,
                total_symbols=len(symbols), signals_generated=0,
                buys=0, sells=0, holds=0, blocked=0, abstained=0,
                allocations=[], market_context=ctx,
                duration_seconds=time.time() - pipeline_start,
                errors=errors,
            )

        # ---- Determine portfolio state --------------------------------
        portfolio_value = self._portfolio_value
        open_positions = 0
        try:
            executor = self._get_executor()
            if portfolio_value is None:
                portfolio_value = executor.get_portfolio_value()
            open_positions = executor.get_open_positions_count()
        except Exception as exc:
            logger.warning(f"Could not read executor state: {exc}")
            portfolio_value = portfolio_value or float(VIRTUAL_CAPITAL)

        portfolio_state = {
            "portfolio_value": portfolio_value,
            "open_positions": open_positions,
        }

        # ---- Stage 3: Technical Analysis -----------------------------
        try:
            ta_results = self._stage_technical(market_data, ctx)
        except Exception as exc:
            logger.error(f"[Stage 3] fatal: {exc}")
            errors.append(f"stage3_technical: {exc}")
            ta_results = {}

        tradeable_symbols = list(ta_results.keys())
        tradeable_data = {sym: market_data[sym] for sym in tradeable_symbols if sym in market_data}

        # ---- Stage 4: Sentiment Analysis -----------------------------
        try:
            sent_results = self._stage_sentiment(
                tradeable_symbols, symbol_names, symbol_sectors
            )
        except Exception as exc:
            logger.error(f"[Stage 4] fatal: {exc}")
            errors.append(f"stage4_sentiment: {exc}")
            sent_results = {}

        # ---- Stage 5: Signal Generation ------------------------------
        try:
            signals = self._stage_signal_gen(
                ta_results=ta_results,
                sent_results=sent_results,
                market_data=tradeable_data,
                ctx=ctx,
                portfolio_value=portfolio_value,
                open_positions=open_positions,
            )
        except Exception as exc:
            logger.error(f"[Stage 5] fatal: {exc}")
            errors.append(f"stage5_signal_gen: {exc}")
            signals = []

        # ---- Auxiliary helpers (earnings guard + F&O ban) ------------
        earnings_guard = None
        fno_ban = None
        try:
            from analysis.earnings_guard import EarningsGuard
            earnings_guard = EarningsGuard()
        except Exception as exc:
            logger.warning(f"EarningsGuard unavailable: {exc}")
        try:
            from analysis.fno_ban import FnOBanFilter
            fno_ban = FnOBanFilter()
        except Exception as exc:
            logger.warning(f"FnOBanFilter unavailable: {exc}")

        # ---- Stage 6: Layer 2 Permission -----------------------------
        try:
            permission_results = self._stage_layer2_permission(
                signals=signals,
                ctx=ctx,
                symbol_sectors=symbol_sectors,
                sent_results=sent_results,
                earnings_guard=earnings_guard,
                fno_ban=fno_ban,
            )
        except Exception as exc:
            logger.error(f"[Stage 6] fatal: {exc}")
            errors.append(f"stage6_layer2_permission: {exc}")
            permission_results = [(sig, None) for sig in signals]

        # ---- Stage 7: Risk Gate --------------------------------------
        try:
            risk_gate_results = self._stage_risk_gate(
                permission_results=permission_results,
                portfolio_state=portfolio_state,
                open_positions=open_positions,
            )
        except Exception as exc:
            logger.error(f"[Stage 7] fatal: {exc}")
            errors.append(f"stage7_risk_gate: {exc}")
            risk_gate_results = [(sig, perm, None) for sig, perm in permission_results]

        # ---- Stage 8: Sizing -----------------------------------------
        try:
            allocations = self._stage_sizing(
                risk_gate_results=risk_gate_results,
                ctx=ctx,
                sent_results=sent_results,
                symbol_sectors=symbol_sectors,
                portfolio_value=portfolio_value,
            )
        except Exception as exc:
            logger.error(f"[Stage 8] fatal: {exc}")
            errors.append(f"stage8_sizing: {exc}")
            allocations = [
                Allocation(signal=sig, sizing=None, permission=perm,
                           risk_passed=rg.passed if rg else False)
                for sig, perm, rg in risk_gate_results
            ]

        # ---- Stage 9: Execution --------------------------------------
        try:
            allocations = self._stage_execution(allocations)
        except Exception as exc:
            logger.error(f"[Stage 9] fatal: {exc}")
            errors.append(f"stage9_execution: {exc}")

        # ---- Assemble PipelineResult ---------------------------------
        duration = time.time() - pipeline_start
        actions = [a.signal.action for a in allocations]
        result = PipelineResult(
            timestamp=run_ts,
            regime=ctx.regime,
            total_symbols=len(symbols),
            signals_generated=len(signals),
            buys=actions.count("BUY"),
            sells=actions.count("SELL"),
            holds=actions.count("HOLD"),
            blocked=actions.count("BLOCKED"),
            abstained=actions.count("ABSTAIN"),
            allocations=allocations,
            market_context=ctx,
            duration_seconds=duration,
            errors=errors,
        )
        logger.info(
            f"Pipeline complete in {duration:.1f}s — "
            f"buys={result.buys} sells={result.sells} holds={result.holds} "
            f"blocked={result.blocked} abstained={result.abstained} "
            f"errors={len(errors)}"
        )
        return result
