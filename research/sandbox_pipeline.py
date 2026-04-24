# =============================================================================
# research/sandbox_pipeline.py — Isolated research / experimentation pipeline
#
# Identical logic to TradingPipeline but:
#   • Uses research/research.db  (never touches logs/trades.db)
#   • Never calls executor        (no orders are placed)
#   • Logs all decisions to research_signals table
#   • Accepts ablation_config to swap/disable individual modules
#
# Usage:
#   from research.sandbox_pipeline import SandboxPipeline
#   result = SandboxPipeline().run(symbols, symbol_names, symbol_sectors)
#
#   # Ablation — disable sentiment, use only TA + trend
#   result = SandboxPipeline(
#       ablation_config={"disable_sentiment": True}
#   ).run(symbols)
# =============================================================================

from __future__ import annotations

import os
import sys
import json
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import get_logger

logger = get_logger("SandboxPipeline")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PRODUCTION_DB = os.path.join(_BASE_DIR, "logs", "trades.db")


class SandboxPipeline:
    """
    Research-mode pipeline. Identical logic to TradingPipeline but:
    - Uses research/research.db (not logs/trades.db)
    - Never calls executor (never places orders)
    - Logs all decisions to research_signals table
    - Can be run with experimental module configs (ablation_config)

    ablation_config keys (all optional, all default False / None):
        disable_sentiment    bool  — replace sentiment with neutral baseline
        disable_layer2       bool  — skip Layer 2 permission gate
        disable_risk_gate    bool  — skip risk gate check
        force_regime         str   — override detected regime
        ta_weight_override   float — override TA_WEIGHT config value
        trend_weight_override float — override TREND_WEIGHT config value
    """

    SANDBOX_DB = os.path.join(_BASE_DIR, "research", "research.db")

    def __init__(self, ablation_config: Optional[dict] = None):
        self.ablation_config = ablation_config or {}
        self._init_db()

        # Lazy module references — initialised on first use
        self._regime_filter    = None
        self._pcr_analyser     = None
        self._fii_tracker      = None
        self._breadth_analyser = None
        self._sector_analyser  = None
        self._ta_agent         = None
        self._sent_agent       = None
        self._strategy_engine  = None
        self._market_permission = None
        self._risk_gate        = None
        self._position_sizer   = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        symbols: list[str],
        symbol_names: Optional[dict] = None,
        symbol_sectors: Optional[dict] = None,
    ) -> dict:
        """
        Run research pipeline. Returns summary dict with counts and signal list.

        No executor is called — results are only written to research.db.
        """
        t_start = time.time()
        symbol_names   = symbol_names or {}
        symbol_sectors = symbol_sectors or {}

        logger.info(
            f"[Sandbox] Starting research run — {len(symbols)} symbols "
            f"ablation={self.ablation_config}"
        )

        errors: list[str] = []

        # ------------------------------------------------------------------
        # Stage 1 — Market Context
        # ------------------------------------------------------------------
        try:
            ctx = self._stage_market_context()
        except Exception as exc:
            logger.error(f"[Sandbox] Stage 1 failed: {exc}")
            return {"error": str(exc), "symbols": len(symbols)}

        # Allow forced regime override for ablation
        forced_regime = self.ablation_config.get("force_regime")
        if forced_regime:
            logger.info(f"[Sandbox] Regime override: {ctx.regime} → {forced_regime}")
            ctx.regime = forced_regime

        # ------------------------------------------------------------------
        # Stage 2 — Data Fetch
        # ------------------------------------------------------------------
        try:
            market_data = self._stage_data_fetch(symbols)
        except Exception as exc:
            logger.error(f"[Sandbox] Stage 2 failed: {exc}")
            return {"error": str(exc), "symbols": len(symbols)}

        # ------------------------------------------------------------------
        # Stage 3 — Technical Analysis
        # ------------------------------------------------------------------
        try:
            ta_results = self._stage_technical(market_data, ctx)
        except Exception as exc:
            logger.error(f"[Sandbox] Stage 3 failed: {exc}")
            ta_results = {}
            errors.append(f"technical: {exc}")

        # ------------------------------------------------------------------
        # Stage 4 — Sentiment Analysis (optional via ablation)
        # ------------------------------------------------------------------
        if self.ablation_config.get("disable_sentiment"):
            logger.info("[Sandbox] Sentiment disabled (ablation) — using neutral baseline")
            from analysis.sentiment_agent import SentimentResult
            sent_results = {
                sym: SentimentResult(symbol=sym, label="neutral", score=0.5,
                                     reasoning=["ablation: sentiment disabled"])
                for sym in ta_results
            }
        else:
            try:
                sent_results = self._stage_sentiment(
                    list(ta_results.keys()), symbol_names, symbol_sectors
                )
            except Exception as exc:
                logger.warning(f"[Sandbox] Stage 4 sentiment failed: {exc}")
                from analysis.sentiment_agent import SentimentResult
                sent_results = {
                    sym: SentimentResult(symbol=sym, label="neutral", score=0.5,
                                         reasoning=["fallback: sentiment error"])
                    for sym in ta_results
                }
                errors.append(f"sentiment: {exc}")

        # ------------------------------------------------------------------
        # Stage 5 — Signal Generation
        # ------------------------------------------------------------------
        try:
            from config import VIRTUAL_CAPITAL
            signals = self._stage_signal_gen(
                ta_results, sent_results, market_data, ctx,
                portfolio_value=VIRTUAL_CAPITAL,
                open_positions=0,   # sandbox has no live positions
            )
        except Exception as exc:
            logger.error(f"[Sandbox] Stage 5 failed: {exc}")
            signals = []
            errors.append(f"signal_gen: {exc}")

        # ------------------------------------------------------------------
        # Stage 6 — Layer 2 Permission (optional via ablation)
        # ------------------------------------------------------------------
        if not self.ablation_config.get("disable_layer2"):
            try:
                signals = self._stage_layer2_permission(
                    signals, ctx, symbol_sectors
                )
            except Exception as exc:
                logger.warning(f"[Sandbox] Stage 6 layer2 failed: {exc}")
                errors.append(f"layer2: {exc}")

        # ------------------------------------------------------------------
        # Stage 7 — Risk Gate (optional via ablation)
        # ------------------------------------------------------------------
        if not self.ablation_config.get("disable_risk_gate"):
            try:
                signals = self._stage_risk_gate(signals, ctx)
            except Exception as exc:
                logger.warning(f"[Sandbox] Stage 7 risk_gate failed: {exc}")
                errors.append(f"risk_gate: {exc}")

        # ------------------------------------------------------------------
        # Stage 8 — Sizing
        # ------------------------------------------------------------------
        try:
            signals = self._stage_sizing(signals, ctx)
        except Exception as exc:
            logger.warning(f"[Sandbox] Stage 8 sizing failed: {exc}")
            errors.append(f"sizing: {exc}")

        # ------------------------------------------------------------------
        # Persist to research.db (no executor call)
        # ------------------------------------------------------------------
        self._persist_signals(signals, ctx)

        duration = round(time.time() - t_start, 2)
        buys     = [s for s in signals if getattr(s, "action", "") == "BUY"]
        sells    = [s for s in signals if getattr(s, "action", "") == "SELL"]
        holds    = [s for s in signals if getattr(s, "action", "") == "HOLD"]
        blocked  = [s for s in signals if getattr(s, "action", "") in ("BLOCKED", "ABSTAIN")]

        summary = {
            "timestamp":   datetime.now().isoformat(),
            "regime":      ctx.regime,
            "total_symbols": len(symbols),
            "fetched":     len(market_data),
            "tradeable":   len(ta_results),
            "signals":     len(signals),
            "buys":        len(buys),
            "sells":       len(sells),
            "holds":       len(holds),
            "blocked":     len(blocked),
            "duration_s":  duration,
            "errors":      errors,
            "ablation":    self.ablation_config,
            "signal_list": [_signal_to_dict(s) for s in signals],
        }
        logger.info(
            f"[Sandbox] Run complete in {duration}s — "
            f"BUY={len(buys)} SELL={len(sells)} HOLD={len(holds)} "
            f"BLOCKED={len(blocked)} errors={len(errors)}"
        )
        return summary

    def compare_to_production(self, lookback_days: int = 30) -> dict:
        """
        Compare research signals to production signals from logs/trades.db.

        Compares the *action* field for symbols that appear in both databases
        within the last *lookback_days*.

        Returns:
            {
                "period_days":       int,
                "research_signals":  int,
                "production_signals": int,
                "common_symbols":    int,
                "agreement_rate":    float,     # fraction of common where action matches
                "divergences":       list[dict], # symbols where actions differ
                "divergence_patterns": dict,    # {(research_action, prod_action): count}
            }
        """
        since = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

        # --- research signals ---
        research_rows: dict[str, str] = {}
        try:
            with sqlite3.connect(self.SANDBOX_DB) as conn:
                cur = conn.execute(
                    "SELECT symbol, action FROM research_signals WHERE created_at >= ?",
                    (since,),
                )
                for row in cur.fetchall():
                    research_rows[row[0]] = row[1]
        except Exception as exc:
            logger.warning(f"[Sandbox] compare: research DB read failed: {exc}")

        # --- production signals ---
        production_rows: dict[str, str] = {}
        if os.path.exists(_PRODUCTION_DB):
            try:
                with sqlite3.connect(_PRODUCTION_DB) as conn:
                    # unified_signals table used by production pipeline
                    cur = conn.execute(
                        "SELECT symbol, action FROM unified_signals WHERE created_at >= ?",
                        (since,),
                    )
                    for row in cur.fetchall():
                        production_rows[row[0]] = row[1]
            except Exception as exc:
                logger.warning(f"[Sandbox] compare: production DB read failed: {exc}")

        common = set(research_rows) & set(production_rows)
        if not common:
            return {
                "period_days":        lookback_days,
                "research_signals":   len(research_rows),
                "production_signals": len(production_rows),
                "common_symbols":     0,
                "agreement_rate":     None,
                "divergences":        [],
                "divergence_patterns": {},
            }

        agreements   = 0
        divergences  = []
        patterns: dict[tuple, int] = {}

        for sym in common:
            r_action = research_rows[sym]
            p_action = production_rows[sym]
            if r_action == p_action:
                agreements += 1
            else:
                divergences.append({
                    "symbol":             sym,
                    "research_action":    r_action,
                    "production_action":  p_action,
                })
                key = (r_action, p_action)
                patterns[key] = patterns.get(key, 0) + 1

        agreement_rate = round(agreements / len(common), 4)
        return {
            "period_days":        lookback_days,
            "research_signals":   len(research_rows),
            "production_signals": len(production_rows),
            "common_symbols":     len(common),
            "agreement_rate":     agreement_rate,
            "divergences":        divergences,
            "divergence_patterns": {str(k): v for k, v in patterns.items()},
        }

    # ------------------------------------------------------------------
    # DB init
    # ------------------------------------------------------------------

    def _init_db(self):
        """Create research/research.db with research_signals table."""
        db_dir = os.path.dirname(self.SANDBOX_DB)
        os.makedirs(db_dir, exist_ok=True)
        with sqlite3.connect(self.SANDBOX_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_signals (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at    TEXT    NOT NULL,
                    symbol        TEXT    NOT NULL,
                    action        TEXT    NOT NULL,
                    regime        TEXT,
                    p_direction   REAL,
                    setup_quality REAL,
                    expected_value REAL,
                    execution_risk REAL,
                    position_size INTEGER,
                    entry_price   REAL,
                    stop_loss     REAL,
                    take_profit   REAL,
                    permission    TEXT,
                    permission_reason TEXT,
                    reasoning     TEXT,
                    ablation_config TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_rs_symbol_ts "
                "ON research_signals (symbol, created_at)"
            )
            conn.commit()
        logger.debug(f"[Sandbox] DB ready: {self.SANDBOX_DB}")

    # ------------------------------------------------------------------
    # Stage implementations
    # ------------------------------------------------------------------

    def _stage_market_context(self):
        """Fetch regime, PCR, FII, breadth — identical to TradingPipeline."""
        from analysis.market_regime import MarketRegimeFilter
        from pipeline.contracts import MarketContext

        regime_result = MarketRegimeFilter().get_regime()
        regime    = regime_result.regime
        stability = getattr(regime_result, "stability_count", 0)
        nifty_trend = regime_result.nifty_trend

        try:
            from analysis.pcr_signal import PCRAnalyser
            pcr_signal = PCRAnalyser().get_signal().signal
        except Exception:
            pcr_signal = "neutral"

        try:
            from analysis.fii_dii import FIIDIITracker
            fii_signal = FIIDIITracker().get_signal().signal
        except Exception:
            fii_signal = "neutral"

        try:
            from analysis.market_breadth import MarketBreadthAnalyser
            raw = MarketBreadthAnalyser().get_breadth().breadth_signal
            if "strong" in raw and "bull" in raw:
                breadth_signal = "strong"
            elif "bull" in raw:
                breadth_signal = "moderate"
            elif "strong" in raw and "bear" in raw:
                breadth_signal = "very_weak"
            elif "bear" in raw:
                breadth_signal = "weak"
            else:
                breadth_signal = "moderate"
        except Exception:
            breadth_signal = "moderate"

        sector_scores: dict = {}
        try:
            from analysis.sector_rotation import SectorRotationAnalyser
            sr = SectorRotationAnalyser().analyse()
            if hasattr(sr, "sector_scores"):
                sector_scores = sr.sector_scores
        except Exception:
            pass

        return MarketContext(
            regime=regime,
            regime_stability=stability,
            pcr_signal=pcr_signal,
            fii_signal=fii_signal,
            breadth_signal=breadth_signal,
            nifty_trend=nifty_trend,
            sector_scores=sector_scores,
            timestamp=datetime.now(),
        )

    def _stage_data_fetch(self, symbols: list[str]) -> dict:
        from data.market_scanner import MarketScanner
        scanner = MarketScanner(lookback_days=400)
        market_data = scanner.run(max_workers=10, regime="bull")
        return {sym: df for sym, df in market_data.items() if sym in symbols}

    def _stage_technical(self, market_data: dict, ctx) -> dict:
        if self._ta_agent is None:
            from analysis.technical_agent import TechnicalAgent
            self._ta_agent = TechnicalAgent()
        ta_results = self._ta_agent.analyse_all(market_data)
        bear_mode = ctx.regime == "bear"
        if bear_mode:
            return {s: r for s, r in ta_results.items()
                    if r.tradeable or r.signal == "bearish"}
        return {s: r for s, r in ta_results.items() if r.tradeable}

    def _stage_sentiment(
        self,
        symbols: list[str],
        symbol_names: dict,
        symbol_sectors: dict,
    ) -> dict:
        if self._sent_agent is None:
            from analysis.sentiment_agent import SentimentAgent
            self._sent_agent = SentimentAgent()
        return self._sent_agent.analyse_all(
            symbols,
            symbol_names=symbol_names,
            symbol_sectors=symbol_sectors,
        )

    def _stage_signal_gen(
        self,
        ta_results: dict,
        sent_results: dict,
        market_data: dict,
        ctx,
        portfolio_value: float,
        open_positions: int,
    ) -> list:
        if self._strategy_engine is None:
            from strategy.engine import StrategyEngine
            self._strategy_engine = StrategyEngine()
        return self._strategy_engine.generate_all(
            ta_results=ta_results,
            sent_results=sent_results,
            market_data=market_data,
            portfolio_value=portfolio_value,
            open_positions=open_positions,
            position_size_multiplier=1.0,
            regime=ctx.regime,
            regime_stability=ctx.regime_stability,
        )

    def _stage_layer2_permission(self, signals: list, ctx, symbol_sectors: dict) -> list:
        if self._market_permission is None:
            from strategy.market_permission import MarketPermission
            self._market_permission = MarketPermission()

        for sig in signals:
            try:
                sector = symbol_sectors.get(sig.symbol, "") if symbol_sectors else ""
                sector_score = ctx.sector_scores.get(sector, 5.0)
                sector_signal = (
                    "bullish" if sector_score >= 7.0
                    else "bearish" if sector_score <= 3.0
                    else "neutral"
                )
                perm = self._market_permission.evaluate(
                    symbol=sig.symbol,
                    action=sig.action,
                    regime=ctx.regime,
                    regime_stability=ctx.regime_stability,
                    pcr_signal=ctx.pcr_signal,
                    fii_signal=ctx.fii_signal,
                    sector_signal=sector_signal,
                    breadth_signal=ctx.breadth_signal,
                    earnings_days=999,
                    fno_banned=False,
                    journal=getattr(sig, "journal", None),
                )
                if perm.permission == "BLOCK":
                    sig.action = "BLOCKED"
                    sig.permission = "BLOCK"
                    sig.permission_reason = perm.reason
                else:
                    sig.permission = perm.permission
                    sig.permission_reason = perm.reason
            except Exception as exc:
                logger.debug(f"[Sandbox] layer2 error for {sig.symbol}: {exc}")
        return signals

    def _stage_risk_gate(self, signals: list, ctx) -> list:
        if self._risk_gate is None:
            from risk.risk_gate import RiskGate
            self._risk_gate = RiskGate()
        filtered = []
        for sig in signals:
            try:
                passed = self._risk_gate.check(sig, regime=ctx.regime)
                if not passed:
                    sig.action = "ABSTAIN"
                    sig.reasoning = (sig.reasoning or "") + " | risk_gate: blocked"
            except Exception:
                pass
            filtered.append(sig)
        return filtered

    def _stage_sizing(self, signals: list, ctx) -> list:
        if self._position_sizer is None:
            try:
                from risk.dynamic_sizing import DynamicPositionSizer
                self._position_sizer = DynamicPositionSizer()
            except Exception:
                return signals
        for sig in signals:
            if sig.action != "BUY":
                continue
            try:
                sizing = self._position_sizer.calculate(
                    signal=sig,
                    regime=ctx.regime,
                )
                if sizing:
                    sig.position_size     = getattr(sizing, "position_size",     sig.position_size)
                    sig.position_size_pct = getattr(sizing, "position_size_pct", sig.position_size_pct)
                    sig.execution_risk    = getattr(sizing, "execution_risk",     sig.execution_risk)
            except Exception:
                pass
        return signals

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_signals(self, signals: list, ctx) -> None:
        """Write signals to research_signals table in research.db."""
        ablation_json = json.dumps(self.ablation_config)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = []
        for sig in signals:
            rows.append((
                now,
                getattr(sig, "symbol",           ""),
                getattr(sig, "action",            "HOLD"),
                ctx.regime,
                getattr(sig, "p_direction",       None),
                getattr(sig, "setup_quality",     None),
                getattr(sig, "expected_value",    None),
                getattr(sig, "execution_risk",    None),
                getattr(sig, "position_size",     None),
                getattr(sig, "entry_price",       None),
                getattr(sig, "stop_loss",         None),
                getattr(sig, "take_profit",       None),
                getattr(sig, "permission",        None),
                getattr(sig, "permission_reason", None),
                getattr(sig, "reasoning",         None),
                ablation_json,
            ))
        if not rows:
            return
        try:
            with sqlite3.connect(self.SANDBOX_DB) as conn:
                conn.executemany("""
                    INSERT INTO research_signals (
                        created_at, symbol, action, regime,
                        p_direction, setup_quality, expected_value, execution_risk,
                        position_size, entry_price, stop_loss, take_profit,
                        permission, permission_reason, reasoning, ablation_config
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, rows)
                conn.commit()
            logger.info(f"[Sandbox] Persisted {len(rows)} signals to research.db")
        except Exception as exc:
            logger.error(f"[Sandbox] DB write failed: {exc}")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _signal_to_dict(sig) -> dict:
    """Convert a TradeSignal to a plain dict for JSON serialisation."""
    return {
        "symbol":           getattr(sig, "symbol",           ""),
        "action":           getattr(sig, "action",           ""),
        "p_direction":      getattr(sig, "p_direction",      0.0),
        "setup_quality":    getattr(sig, "setup_quality",    0.0),
        "expected_value":   getattr(sig, "expected_value",   0.0),
        "execution_risk":   getattr(sig, "execution_risk",   0.0),
        "position_size":    getattr(sig, "position_size",    0),
        "entry_price":      getattr(sig, "entry_price",      0.0),
        "stop_loss":        getattr(sig, "stop_loss",        0.0),
        "take_profit":      getattr(sig, "take_profit",      0.0),
        "permission":       getattr(sig, "permission",       ""),
        "permission_reason": getattr(sig, "permission_reason", ""),
        "reasoning":        getattr(sig, "reasoning",        ""),
    }
