# =============================================================================
# pipeline/legacy.py — Legacy monolithic run_agent() path
#
# Preserved from main.py during Phase 3 refactor.
# New code should use pipeline/runner.py (TradingPipeline) instead.
# This file is kept so the scheduler can fall back here if the typed
# pipeline fails, and to allow gradual migration.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import json
from datetime import datetime
import pandas as pd

from config import TRADING_MODE, MAX_OPEN_POSITIONS
from data.market_scanner import MarketScanner
from analysis.technical_agent import TechnicalAgent
from analysis.sentiment_agent import SentimentAgent
from analysis.market_regime import MarketRegimeFilter
from analysis.support_resistance import SupportResistanceAnalyser
from analysis.multi_timeframe import MultiTimeframeAnalyser
from analysis.pattern_recognition import PatternRecogniser
from analysis.sector_rotation import SectorRotationAnalyser
from analysis.volume_profile import VolumeProfileAnalyser
from analysis.fii_dii import FIIDIIAnalyser
from analysis.pcr_signal import PCRAnalyser
from analysis.earnings_guard import EarningsGuard
from analysis.momentum_filter import MomentumFilter
from analysis.strategy_quality import StrategyQualityEngine
from analysis.fno_ban import FnOBanFilter
from analysis.block_deals import BlockDealsAnalyser
from strategy.engine import StrategyEngine
from execution.executor import get_executor
from memory.portfolio_memory import PortfolioMemory
from risk.trailing_stop import TrailingStopMonitor
from risk.circuit_breaker import CircuitBreaker
from risk.correlation_filter import CorrelationFilter
from risk.risk_gate import RiskGate
from risk.dynamic_sizing import DynamicPositionSizer
from utils import get_logger
from utils.telegram import send_signals, send

logger = get_logger("LegacyAgent")


def run_agent(dry_run: bool = False) -> list:
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info(f"  TRADING AGENT (LEGACY) -- MODE: {TRADING_MODE.upper()}")
    logger.info(f"  {start_time.strftime('%Y-%m-%d %H:%M:%S IST')}")
    logger.info("=" * 60)

    executor        = get_executor()
    portfolio_value = executor.get_portfolio_value()
    open_positions  = executor.get_open_positions_count()

    cb_ok, cb_reason = CircuitBreaker().check(portfolio_value)
    if not cb_ok:
        logger.warning(f"[CB] {cb_reason}")
        send(f"*Circuit Breaker*\n_{cb_reason}_")
        _run_trailing_stop()
        return []

    logger.info("[REGIME] Checking market...")
    regime = MarketRegimeFilter().get_regime()
    logger.info(f"[REGIME] {regime.message}")
    bear_mode = not regime.allow_buys

    logger.info("[MARKET] Fetching market-wide signals...")
    pcr_result    = PCRAnalyser().get_signal()
    fii_result    = FIIDIIAnalyser().get_signal()
    sector_result = SectorRotationAnalyser().analyse()
    logger.info(f"[PCR] {pcr_result.message}")
    logger.info(f"[FII] {fii_result.message}")
    logger.info(f"[SECTOR] {sector_result.message}")

    if pcr_result.signal == "strong_sell" and fii_result.signal in ("sell", "strong_sell"):
        logger.warning("PCR + FII both bearish — blocking buys today")
        bear_mode = True

    logger.info("[BAN] Loading F&O ban list...")
    fno_ban = FnOBanFilter()

    logger.info("[M1] Scanning NSE stocks...")
    scanner     = MarketScanner(lookback_days=400)
    market_data = scanner.run(max_workers=10, regime=regime.regime)
    logger.info(f"[M1] {len(market_data)} stocks loaded")

    symbol_sectors = {r["symbol"]: r["sector"] for _, r in scanner.symbols_df.iterrows()}
    symbol_names   = {r["symbol"]: r["name"]   for _, r in scanner.symbols_df.iterrows()}

    logger.info("[MOMENTUM] Filtering by trend health...")
    mom_mode    = "short" if bear_mode else "buy"
    mom_results = MomentumFilter().filter_all(market_data, mode=mom_mode)
    market_data = {sym: df for sym, df in market_data.items() if sym in mom_results}
    logger.info(f"[MOMENTUM] {len(market_data)}/{len(scanner.symbols_df)} pass momentum gates")

    banned_in_scan = [s for s in list(market_data.keys()) if fno_ban.is_banned(s)]
    for s in banned_in_scan:
        del market_data[s]
    if banned_in_scan:
        logger.info(f"[BAN] Removed {len(banned_in_scan)} banned stocks: {banned_in_scan[:5]}")

    logger.info("[M2] Technical analysis...")
    ta_results = TechnicalAgent().analyse_all(market_data)
    if bear_mode:
        tradeable = {sym: r for sym, r in ta_results.items()
                     if r.tradeable or r.signal == "bearish"}
    else:
        tradeable = {sym: r for sym, r in ta_results.items() if r.tradeable}
    logger.info(f"[M2] {len(tradeable)}/{len(ta_results)} tradeable (bear_mode={bear_mode})")

    tradeable_data = {sym: market_data[sym] for sym in tradeable}

    logger.info("[EARNINGS] Checking earnings calendar...")
    earnings_guard = EarningsGuard()

    logger.info("[S/R] Support/resistance analysis...")
    sr_results = SupportResistanceAnalyser().analyse_all(tradeable_data)
    tradeable  = {sym: r for sym, r in tradeable.items() if sr_results.get(sym, None)}
    logger.info(f"[S/R] {len(tradeable)} have S/R data")

    logger.info("[MTF] Weekly confirmation...")
    tradeable_data = {sym: market_data[sym] for sym in tradeable}
    mtf_results    = MultiTimeframeAnalyser().analyse_all(tradeable_data)
    tradeable      = {sym: r for sym, r in tradeable.items()
                      if mtf_results.get(sym, None) and mtf_results[sym].confirmed}
    logger.info(f"[MTF] {len(tradeable)} confirmed by weekly")

    logger.info("[VP] Volume profile analysis...")
    tradeable_data = {sym: market_data[sym] for sym in tradeable}
    vp_results     = VolumeProfileAnalyser().analyse_all(tradeable_data)

    logger.info("[PATTERN] Chart pattern detection...")
    pattern_results = PatternRecogniser().analyse_all(tradeable_data)

    logger.info("[M3] Sentiment analysis...")
    sent_results = SentimentAgent().analyse_all(
        list(tradeable.keys()),
        symbol_names=symbol_names,
        symbol_sectors=symbol_sectors,
    )

    logger.info("[M4/M5] Generating signals...")
    strategy = StrategyEngine()
    quality_engine = StrategyQualityEngine()
    signals = strategy.generate_all(
        ta_results               = tradeable,
        sent_results             = sent_results,
        market_data              = market_data,
        portfolio_value          = portfolio_value,
        open_positions           = open_positions,
        position_size_multiplier = regime.position_size_multiplier,
    )

    if bear_mode:
        from analysis.short_signals import ShortSignalGenerator
        _open_syms = set(executor.portfolio.get("positions", {}).keys()) \
                     if hasattr(executor, "portfolio") else set()
        sell_signals  = [s for s in signals if s.action == "SELL" and s.symbol in _open_syms]
        short_signals = ShortSignalGenerator().generate_all(
            ta_results=ta_results, sent_results=sent_results,
            market_data=market_data, portfolio_value=portfolio_value, top_n=5,
        )
        logger.info(f"[REGIME] Bear mode — {len(sell_signals)} SELL, {len(short_signals)} SHORT")

        if sell_signals:
            send("*Bear Mode — Close Positions*\n"
                 + "\n".join(f"• {s.symbol} — close position" for s in sell_signals))
        if short_signals:
            lines = ["*Bear Mode — SHORT Watchlist*", ""]
            for s in short_signals:
                lines.append(
                    f"*{s.symbol}* ({s.confidence:.0%} conf)\n"
                    f"Entry Rs.{s.entry_price:,.0f} | SL Rs.{s.stop_loss:,.0f} | "
                    f"Target Rs.{s.take_profit:,.0f}\n_{s.reasoning[:100]}_\n"
                )
            send("\n".join(lines))

        memory = PortfolioMemory()
        for sig in sell_signals:
            signal_id = memory.save_signal(sig)
            if getattr(sig, "journal", None) is not None:
                try:
                    memory.save_journal(sig.journal, signal_id=signal_id)
                except Exception:
                    pass
            if not dry_run:
                result = executor.execute(sig)
                logger.info(f"  SELL {sig.symbol}: {result.get('status','unknown')}")
                if result.get("status") == "filled" and signal_id:
                    memory.mark_signal_executed(signal_id)

        _run_trailing_stop()
        memory.save_snapshot(executor.get_portfolio_summary())
        return sell_signals + short_signals

    buy_signals = [s for s in signals if s.action == "BUY"]

    sizer       = DynamicPositionSizer()
    block_deals = BlockDealsAnalyser()

    _nifty_ret_1m = 0.0
    try:
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
        import yfinance as _yf
        def _fetch_nifty():
            ni = _yf.Ticker("^NSEI").history(period="1mo", interval="1d")
            if not ni.empty and len(ni) >= 2:
                return float((ni["Close"].iloc[-1] / ni["Close"].iloc[0] - 1) * 100)
            return 0.0
        with ThreadPoolExecutor(max_workers=1) as _pool:
            _future = _pool.submit(_fetch_nifty)
            try:
                _nifty_ret_1m = _future.result(timeout=10)
            except (FuturesTimeout, Exception) as _ne:
                logger.warning(f"Nifty return fetch failed ({_ne}) — defaulting to 0.0%")
    except Exception:
        pass

    enriched = []
    blocked_quality = []
    for sig in buy_signals:
        pat  = pattern_results.get(sig.symbol)
        sr   = sr_results.get(sig.symbol)
        mtf  = mtf_results.get(sig.symbol)
        vp   = vp_results.get(sig.symbol)
        sec  = symbol_sectors.get(sig.symbol, "Unknown")
        sec_mult = SectorRotationAnalyser.get_sector_multiplier(sec, sector_result)

        extra = 0.0
        if pat and pat.bias == "bullish":
            extra += 0.05
            sig.reasoning += f". Pattern: {pat.primary_pattern}"
        if sr and sr.near_support:
            extra += 0.03
            sig.reasoning += f". Near support Rs.{sr.nearest_support:,.0f}"
        if vp and vp.signal == "buy":
            extra += 0.02
            sig.reasoning += f". POC Rs.{vp.poc:,.0f}"
        if mtf:
            sig.reasoning += f". Weekly: {mtf.weekly_trend}"
            if mtf.confirmed and mtf.weekly_trend == "up" and mtf.daily_signal == "bullish":
                extra += 0.05
                sig.reasoning += " (MTF confirmed +5%)"
            elif mtf.mtf_penalty > 0:
                extra -= mtf.mtf_penalty
                sig.reasoning += f" (MTF penalty -{mtf.mtf_penalty:.0%})"
        if fii_result.signal in ("buy", "strong_buy"):
            extra += 0.02

        mom = mom_results.get(sig.symbol)
        if mom and mom.price > 0:
            try:
                _df_tmp = market_data.get(sig.symbol)
                if _df_tmp is not None and len(_df_tmp) >= 200:
                    _ema200 = float(_df_tmp["close"].ewm(span=200).mean().iloc[-1])
                    if mom.price < _ema200:
                        extra -= 0.05
                        sig.reasoning += ". Below EMA200 (recovery trade, -5%)"
            except Exception:
                pass

        df = market_data.get(sig.symbol)
        if df is not None and len(df) >= 252:
            try:
                high_52w = float(df["close"].rolling(252).max().iloc[-1])
                last_px  = float(df["close"].iloc[-1])
                if high_52w > 0 and (last_px / high_52w) >= 0.95:
                    extra += 0.04
                    sig.reasoning += f". Near 52w high ({last_px/high_52w:.1%})"
            except Exception:
                pass

        mom = mom_results.get(sig.symbol)
        if mom and hasattr(mom, "ret_3m") and _nifty_ret_1m != 0.0:
            stock_ret_1m = mom.ret_3m / 3
            if stock_ret_1m > _nifty_ret_1m + 2:
                extra += 0.03
                sig.reasoning += f". RS: +{stock_ret_1m - _nifty_ret_1m:.1f}% vs Nifty"

        bd_boost, bd_note = block_deals.get_boost(sig.symbol)
        if bd_boost > 0:
            extra += bd_boost
            sig.reasoning += f". {bd_note}"

        if sr and sr.recommendation == "sell_zone":
            from config import SR_SELL_ZONE_PENALTY
            extra -= SR_SELL_ZONE_PENALTY
            sig.reasoning += f" (S/R sell zone penalty -{SR_SELL_ZONE_PENALTY:.0%})"

        sig.confidence = max(0.0, min(0.99, sig.confidence + extra))

        atr = _atr(df) if df is not None else sig.entry_price * 0.02
        sizing = sizer.calculate(
            symbol=sig.symbol, confidence=sig.confidence,
            entry_price=sig.entry_price, atr=atr,
            portfolio_value=portfolio_value,
            pattern_bias=pat.bias if pat else "neutral",
            sr_near_support=sr.near_support if sr else False,
            sector_multiplier=sec_mult,
            regime_multiplier=regime.position_size_multiplier,
            fii_score=fii_result.score,
            setup_type=getattr(sig, "setup_type", ""),
        )
        sig.position_size   = sizing.position_size
        sig.capital_at_risk = sizing.capital_at_risk
        sig.stop_loss       = sizing.stop_loss
        sig.take_profit     = sizing.take_profit

        quality = quality_engine.assess(
            sig, regime_tag=regime.regime,
            pattern_name=pat.primary_pattern if pat else "",
            pattern_bias=pat.bias if pat else "",
            near_support=sr.near_support if sr else False,
            vp_signal=vp.signal if vp else "",
            weekly_trend=mtf.weekly_trend if mtf else "",
            sector=sec,
        )
        if quality.blocked:
            sig.reasoning += f". Quality block: {quality.block_reason}"
            blocked_quality.append(sig)
            continue

        sig.setup_type       = quality.setup_type
        sig.regime_tag       = regime.regime
        sig.quality_score    = quality.quality_score
        sig.expectancy_score = quality.expectancy_score
        sig.symbol_edge      = quality.symbol_edge
        sig.setup_edge       = quality.setup_edge
        sig.quality_flags    = quality.flags
        sig.confidence       = quality.adjusted_confidence
        sig.position_size    = max(0, int(sig.position_size * quality.size_multiplier))
        sig.capital_at_risk  = round(sig.capital_at_risk * quality.size_multiplier, 2)
        sig.reasoning += (
            f". Setup: {quality.setup_type}"
            f". Quality {quality.quality_score:.1f}"
            f". Expectancy {quality.expectancy_score:+.2f}"
        )
        if quality.flags:
            sig.reasoning += f". Flags: {', '.join(quality.flags[:3])}"
        enriched.append(sig)

    buy_signals = enriched
    if blocked_quality:
        logger.info(f"[QUALITY] Blocked {len(blocked_quality)} weak-history BUY signals")

    buy_signals, blocked_earnings = earnings_guard.filter_signals(buy_signals)
    if blocked_earnings:
        logger.info(f"[EARNINGS] Blocked {len(blocked_earnings)} signals near earnings")

    buy_signals = _deduplicate_signals(buy_signals)

    logger.info("[CORR] Correlation filter...")
    buy_signals = CorrelationFilter().filter(buy_signals, market_data, symbol_sectors)

    logger.info(f"Final: {len(buy_signals)} BUY signals after all filters")

    remaining_slots = max(0, MAX_OPEN_POSITIONS - open_positions)
    try:
        from strategy.execution_planner import ExecutionPlanner
        _deployed_pct = min(1.0, open_positions / MAX_OPEN_POSITIONS) if MAX_OPEN_POSITIONS else 0.0
        ranked = ExecutionPlanner().rank_and_allocate(
            signals=buy_signals,
            max_slots=remaining_slots,
            open_position_sectors=set(symbol_sectors.get(sym, "") for sym in
                                       (executor.portfolio.get("positions", {}).keys()
                                        if hasattr(executor, "portfolio") else [])),
            market_data=market_data,
            portfolio_deployed_pct=_deployed_pct,
        )
        buy_signals = [c.signal for c in ranked if c.slot_allocated]
        logger.info(f"[PLANNER] {len(buy_signals)} allocated from {len(ranked)} candidates")
    except Exception as e:
        logger.warning(f"ExecutionPlanner failed ({e}) — using quality sort fallback")
        buy_signals = sorted(
            buy_signals,
            key=lambda x: (x.quality_score or 0.0, x.confidence, x.ta_score),
            reverse=True,
        )

    _risk_gate = RiskGate()
    _pstate    = {"portfolio_value": portfolio_value, "open_positions": open_positions}
    _passed, _blocked = [], []
    for _sig in buy_signals:
        _rg = _risk_gate.check(_sig, _pstate, open_positions)
        if _rg.passed:
            _passed.append(_sig)
        else:
            _reasons = "; ".join(b.get("reason", "") for b in _rg.blocks)
            logger.info(f"[RISK_GATE] BLOCK {_sig.symbol}: {_reasons}")
            _blocked.append(_sig)
    if _blocked:
        logger.info(f"[RISK_GATE] {len(_blocked)} blocked, {len(_passed)} passed")

    actionable_signals = _passed
    _print_signals(actionable_signals, regime)

    memory = PortfolioMemory()
    signal_ids = {}
    for sig in actionable_signals:
        signal_ids[sig.symbol] = memory.save_signal(sig)
        if getattr(sig, "journal", None) is not None:
            try:
                memory.save_journal(sig.journal, signal_id=signal_ids[sig.symbol])
            except Exception:
                pass

    stats = memory.get_stats()
    send_signals(actionable_signals, stats, mode=TRADING_MODE, dry_run=dry_run)

    if dry_run:
        logger.info("[M7] DRY RUN -- not executed")
    else:
        for sig in actionable_signals:
            result = executor.execute(sig)
            logger.info(f"  {sig.symbol}: {result.get('status','unknown')}")
            if result.get("status") == "filled":
                signal_id = signal_ids.get(sig.symbol)
                if signal_id:
                    memory.mark_signal_executed(signal_id)

    _run_trailing_stop()
    memory.save_snapshot(executor.get_portfolio_summary())

    try:
        from readiness.checker import ReadinessChecker
        report = ReadinessChecker().check()
        logger.info(f"Readiness: {report.passed_count}/{report.total_gates} gates")
    except Exception as e:
        logger.debug(f"Readiness skipped: {e}")

    elapsed = (datetime.now() - start_time).seconds
    logger.info(
        f"\nDone in {elapsed}s | Signals: {len(actionable_signals)} | "
        f"Trades: {stats['total_trades']} | Win rate: {stats['win_rate_pct']:.1f}%"
    )
    logger.info("=" * 60)
    return actionable_signals


def run_pipeline(symbols: list = None, dry_run: bool = False) -> list:
    """
    Try the typed 9-stage TradingPipeline; fall back to run_agent() on failure.
    This is the function the scheduler should call.
    """
    try:
        from pipeline.runner import TradingPipeline
        executor = get_executor()
        memory   = PortfolioMemory()
        pipeline = TradingPipeline(
            mode=TRADING_MODE,
            portfolio_value=executor.get_portfolio_value(),
            memory=memory,
        )
        if symbols is None:
            scanner = MarketScanner(lookback_days=400)
            scanner.run(max_workers=1, regime=None)
            symbols     = [r["symbol"] for _, r in scanner.symbols_df.iterrows()]
            sym_names   = {r["symbol"]: r["name"]   for _, r in scanner.symbols_df.iterrows()}
            sym_sectors = {r["symbol"]: r["sector"] for _, r in scanner.symbols_df.iterrows()}
        else:
            sym_names, sym_sectors = {}, {}
        result = pipeline.run(symbols, symbol_names=sym_names, symbol_sectors=sym_sectors)
        logger.info(
            f"Pipeline complete: {result.buys} BUY, {result.sells} SELL, "
            f"{result.blocked} BLOCKED in {result.duration_seconds:.1f}s"
        )
        return [a.signal for a in result.allocations if a.signal.action == "BUY"]
    except Exception as e:
        logger.warning(f"Pipeline failed ({e}) — falling back to run_agent()")
        return run_agent(dry_run=dry_run)


def _deduplicate_signals(signals: list) -> list:
    try:
        from config import SQLITE_DB_FILE
        if not os.path.exists(SQLITE_DB_FILE):
            return signals
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM signals "
                "WHERE timestamp >= datetime('now', '-24 hours')"
            ).fetchall()
        recent = {r[0] for r in rows}
    except Exception:
        return signals

    allowed, dupes = [], []
    for sig in signals:
        if sig.symbol in recent:
            dupes.append(sig.symbol)
        else:
            allowed.append(sig)
    if dupes:
        logger.info(f"[DUP] Blocked {len(dupes)} duplicate signals (24h window): {dupes}")
    return allowed


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    try:
        hi, lo, cl = df["high"], df["low"], df["close"]
        tr = pd.concat([hi - lo, (hi - cl.shift()).abs(),
                        (lo - cl.shift()).abs()], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except Exception:
        return float(df["close"].iloc[-1]) * 0.02


def _run_trailing_stop():
    try:
        TrailingStopMonitor().run()
    except Exception as e:
        logger.debug(f"Trailing stop: {e}")


def _print_signals(signals: list, regime=None):
    if not signals:
        print("\n  No BUY signals today.\n")
        return
    if regime:
        print(f"\n  Market: {regime.regime.upper()} | "
              f"Nifty RSI: {regime.nifty_rsi} | "
              f"1M: {regime.nifty_1m_return:+.1f}%")
    print("\n" + "=" * 70)
    print(f"  TOP {len(signals)} SIGNALS -- {datetime.now().strftime('%d %b %Y')}")
    print("=" * 70)
    for i, s in enumerate(signals, 1):
        print(f"\n#{i}  {s.symbol}")
        print(f"    Confidence : {s.confidence:.0%}")
        print(f"    Entry      : Rs.{s.entry_price:,.2f}")
        print(f"    Stop Loss  : Rs.{s.stop_loss:,.2f}")
        print(f"    Take Profit: Rs.{s.take_profit:,.2f}")
        print(f"    Position   : {s.position_size} shares")
        print(f"    TA Score   : {s.ta_score}/10")
        print(f"    Setup      : {getattr(s, 'setup_type', 'technical_base')}")
        print(f"    Quality    : {getattr(s, 'quality_score', 0.0):.1f}")
        print(f"    Sentiment  : {s.sentiment}")
        print(f"    Reason     : {s.reasoning}")
    print("\n" + "=" * 70 + "\n")
