import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
from datetime import datetime
import pandas as pd

from config import TRADING_MODE, TOP_N_SIGNALS, VIRTUAL_CAPITAL, MAX_OPEN_POSITIONS
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
from strategy.engine import StrategyEngine
from execution.executor import get_executor
from memory.portfolio_memory import PortfolioMemory
from risk.trailing_stop import TrailingStopMonitor
from risk.circuit_breaker import CircuitBreaker
from risk.correlation_filter import CorrelationFilter
from risk.dynamic_sizing import DynamicPositionSizer
from utils import get_logger
from utils.telegram import send_signals, send

logger = get_logger("Main")


def run_agent(dry_run: bool = False) -> list:
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info(f"  TRADING AGENT -- MODE: {TRADING_MODE.upper()}")
    logger.info(f"  {start_time.strftime('%Y-%m-%d %H:%M:%S IST')}")
    logger.info("=" * 60)

    executor        = get_executor()
    portfolio_value = executor.get_portfolio_value()
    open_positions  = executor.get_open_positions_count()

    # ------------------------------------------------------------------
    # 1. CIRCUIT BREAKER
    # ------------------------------------------------------------------
    cb_ok, cb_reason = CircuitBreaker().check(portfolio_value)
    if not cb_ok:
        logger.warning(f"[CB] {cb_reason}")
        send(f"*Circuit Breaker*\n_{cb_reason}_")
        _run_trailing_stop()
        return []

    # ------------------------------------------------------------------
    # 2. MARKET REGIME
    # ------------------------------------------------------------------
    logger.info("[REGIME] Checking market...")
    regime = MarketRegimeFilter().get_regime()
    logger.info(f"[REGIME] {regime.message}")
    bear_mode = not regime.allow_buys  # scan continues but BUY signals blocked

    # ------------------------------------------------------------------
    # 3. MARKET-WIDE SIGNALS (PCR + FII/DII + Sector Rotation)
    # ------------------------------------------------------------------
    logger.info("[MARKET] Fetching market-wide signals...")
    pcr_result     = PCRAnalyser().get_signal()
    fii_result     = FIIDIIAnalyser().get_signal()
    sector_result  = SectorRotationAnalyser().analyse()
    logger.info(f"[PCR] {pcr_result.message}")
    logger.info(f"[FII] {fii_result.message}")
    logger.info(f"[SECTOR] {sector_result.message}")

    # If both PCR and FII are strongly bearish, add extra block on buys
    if pcr_result.signal == "strong_sell" and fii_result.signal in ("sell", "strong_sell"):
        logger.warning("PCR + FII both bearish — blocking buys today")
        bear_mode = True

    # ------------------------------------------------------------------
    # 4. MARKET SCANNER
    # ------------------------------------------------------------------
    logger.info("[M1] Scanning NSE stocks...")
    scanner     = MarketScanner(lookback_days=400)
    market_data = scanner.run(max_workers=10)
    logger.info(f"[M1] {len(market_data)} stocks loaded")

    symbol_sectors = {r["symbol"]: r["sector"] for _, r in scanner.symbols_df.iterrows()}
    symbol_names   = {r["symbol"]: r["name"]   for _, r in scanner.symbols_df.iterrows()}

    # ------------------------------------------------------------------
    # 5. MOMENTUM FILTER — only trade stocks in confirmed trends
    # ------------------------------------------------------------------
    logger.info("[MOMENTUM] Filtering by trend health...")
    mom_mode    = "short" if bear_mode else "buy"
    mom_results = MomentumFilter().filter_all(market_data, mode=mom_mode)
    market_data = {sym: df for sym, df in market_data.items() if sym in mom_results}
    total_scanned = len(scanner.symbols_df)
    logger.info(f"[MOMENTUM] {len(market_data)}/{total_scanned} pass momentum gates")

    # ------------------------------------------------------------------
    # 6. TECHNICAL ANALYSIS
    # ------------------------------------------------------------------
    logger.info("[M2] Technical analysis...")
    ta_results = TechnicalAgent().analyse_all(market_data)
    if bear_mode:
        tradeable = {sym: r for sym, r in ta_results.items()
                     if r.tradeable or r.signal == "bearish"}
    else:
        tradeable = {sym: r for sym, r in ta_results.items() if r.tradeable}
    logger.info(f"[M2] {len(tradeable)}/{len(ta_results)} tradeable (bear_mode={bear_mode})")

    tradeable_data = {sym: market_data[sym] for sym in tradeable}

    # ------------------------------------------------------------------
    # 6. EARNINGS GUARD
    # ------------------------------------------------------------------
    logger.info("[EARNINGS] Checking earnings calendar...")
    earnings_guard = EarningsGuard()

    # ------------------------------------------------------------------
    # 7. SUPPORT & RESISTANCE
    # ------------------------------------------------------------------
    logger.info("[S/R] Support/resistance analysis...")
    sr_results = SupportResistanceAnalyser().analyse_all(tradeable_data)
    # Note: sell_zone stocks are kept but penalized later (SR_SELL_ZONE_PENALTY)
    tradeable = {sym: r for sym, r in tradeable.items()
                 if sr_results.get(sym, None)}
    logger.info(f"[S/R] {len(tradeable)} have S/R data")

    # ------------------------------------------------------------------
    # 8. MULTI-TIMEFRAME CONFIRMATION
    # ------------------------------------------------------------------
    logger.info("[MTF] Weekly confirmation...")
    tradeable_data = {sym: market_data[sym] for sym in tradeable}
    mtf_results    = MultiTimeframeAnalyser().analyse_all(tradeable_data)
    tradeable      = {sym: r for sym, r in tradeable.items()
                      if mtf_results.get(sym, None) and mtf_results[sym].confirmed}
    logger.info(f"[MTF] {len(tradeable)} confirmed by weekly")

    # ------------------------------------------------------------------
    # 9. VOLUME PROFILE
    # ------------------------------------------------------------------
    logger.info("[VP] Volume profile analysis...")
    tradeable_data = {sym: market_data[sym] for sym in tradeable}
    vp_results     = VolumeProfileAnalyser().analyse_all(tradeable_data)

    # ------------------------------------------------------------------
    # 10. PATTERN RECOGNITION
    # ------------------------------------------------------------------
    logger.info("[PATTERN] Chart pattern detection...")
    pattern_results = PatternRecogniser().analyse_all(tradeable_data)

    # ------------------------------------------------------------------
    # 11. SENTIMENT
    # ------------------------------------------------------------------
    logger.info("[M3] Sentiment analysis...")
    sent_results = SentimentAgent().analyse_all(
        list(tradeable.keys()),
        symbol_names=symbol_names,
        symbol_sectors=symbol_sectors,
    )

    # ------------------------------------------------------------------
    # 12. STRATEGY ENGINE
    # ------------------------------------------------------------------
    logger.info("[M4/M5] Generating signals...")
    strategy = StrategyEngine()
    quality_engine = StrategyQualityEngine()
    signals  = strategy.generate_all(
        ta_results               = tradeable,
        sent_results             = sent_results,
        market_data              = market_data,
        portfolio_value          = portfolio_value,
        open_positions           = open_positions,
        position_size_multiplier = regime.position_size_multiplier,
    )

    if bear_mode:
        # Block new BUYs — close OPEN positions + generate SHORT watchlist
        from analysis.short_signals import ShortSignalGenerator
        # Only close positions we actually hold — not every bearish stock in the scan
        _open_syms   = set(executor.portfolio.get("positions", {}).keys()) \
                       if hasattr(executor, "portfolio") else set()
        sell_signals = [s for s in signals
                        if s.action == "SELL" and s.symbol in _open_syms]
        short_signals = ShortSignalGenerator().generate_all(
            ta_results      = ta_results,
            sent_results    = sent_results,
            market_data     = market_data,
            portfolio_value = portfolio_value,
            top_n           = 5,
        )
        logger.info(f"[REGIME] Bear mode — {len(sell_signals)} SELL, "
                    f"{len(short_signals)} SHORT setups")

        if sell_signals:
            send("*Bear Mode — Close Positions*\n"
                 + "\n".join(f"• {s.symbol} — close position" for s in sell_signals))

        if short_signals:
            lines = ["*Bear Mode — SHORT Watchlist*", ""]
            for s in short_signals:
                lines.append(
                    f"*{s.symbol}* ({s.confidence:.0%} conf)\n"
                    f"Entry Rs.{s.entry_price:,.0f} | "
                    f"SL Rs.{s.stop_loss:,.0f} | "
                    f"Target Rs.{s.take_profit:,.0f}\n"
                    f"_{s.reasoning[:100]}_\n"
                )
            send("\n".join(lines))

        memory = PortfolioMemory()
        for sig in sell_signals:
            signal_id = memory.save_signal(sig)
            if not dry_run:
                result = executor.execute(sig)
                logger.info(f"  SELL {sig.symbol}: {result.get('status','unknown')}")
                if result.get("status") == "filled" and signal_id:
                    memory.mark_signal_executed(signal_id)

        _run_trailing_stop()
        summary = executor.get_portfolio_summary()
        memory.save_snapshot(summary)
        return sell_signals + short_signals

    buy_signals = [s for s in signals if s.action == "BUY"]

    # ------------------------------------------------------------------
    # 13. DYNAMIC POSITION SIZING + SIGNAL ENRICHMENT
    # ------------------------------------------------------------------
    sizer = DynamicPositionSizer()
    enriched = []
    blocked_quality = []
    for sig in buy_signals:
        pat  = pattern_results.get(sig.symbol)
        sr   = sr_results.get(sig.symbol)
        mtf  = mtf_results.get(sig.symbol)
        vp   = vp_results.get(sig.symbol)
        sec  = symbol_sectors.get(sig.symbol, "Unknown")
        sec_mult = SectorRotationAnalyser.get_sector_multiplier(sec, sector_result)

        # Boost confidence for bullish patterns, S/R, VP
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
        # S/R sell zone penalty (instead of hard-blocking)
        if sr and sr.recommendation == "sell_zone":
            from config import SR_SELL_ZONE_PENALTY
            extra -= SR_SELL_ZONE_PENALTY
            sig.reasoning += f" (S/R sell zone penalty -{SR_SELL_ZONE_PENALTY:.0%})"

        sig.confidence = max(0.0, min(0.99, sig.confidence + extra))

        # Dynamic sizing
        df  = market_data.get(sig.symbol)
        atr = _atr(df) if df is not None else sig.entry_price * 0.02
        sizing = sizer.calculate(
            symbol            = sig.symbol,
            confidence        = sig.confidence,
            entry_price       = sig.entry_price,
            atr               = atr,
            portfolio_value   = portfolio_value,
            pattern_bias      = pat.bias if pat else "neutral",
            sr_near_support   = sr.near_support if sr else False,
            sector_multiplier = sec_mult,
            regime_multiplier = regime.position_size_multiplier,
            fii_score         = fii_result.score,
            setup_type        = getattr(sig, "setup_type", ""),
        )
        sig.position_size   = sizing.position_size
        sig.capital_at_risk = sizing.capital_at_risk
        sig.stop_loss       = sizing.stop_loss
        sig.take_profit     = sizing.take_profit
        quality = quality_engine.assess(
            sig,
            regime_tag=regime.regime,
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
        sig.setup_type = quality.setup_type
        sig.regime_tag = regime.regime
        sig.quality_score = quality.quality_score
        sig.expectancy_score = quality.expectancy_score
        sig.symbol_edge = quality.symbol_edge
        sig.setup_edge = quality.setup_edge
        sig.quality_flags = quality.flags
        sig.confidence = quality.adjusted_confidence
        sig.position_size = max(0, int(sig.position_size * quality.size_multiplier))
        sig.capital_at_risk = round(sig.capital_at_risk * quality.size_multiplier, 2)
        quality_note = (
            f". Setup: {quality.setup_type}"
            f". Quality {quality.quality_score:.1f}"
            f". Expectancy {quality.expectancy_score:+.2f}"
        )
        if quality.flags:
            quality_note += f". Flags: {', '.join(quality.flags[:3])}"
        sig.reasoning += quality_note
        enriched.append(sig)

    buy_signals = enriched
    if blocked_quality:
        logger.info(f"[QUALITY] Blocked {len(blocked_quality)} weak-history BUY signals")

    # ------------------------------------------------------------------
    # 14. EARNINGS GUARD FILTER
    # ------------------------------------------------------------------
    buy_signals, blocked_earnings = earnings_guard.filter_signals(buy_signals)
    if blocked_earnings:
        logger.info(f"[EARNINGS] Blocked {len(blocked_earnings)} signals near earnings")

    # ------------------------------------------------------------------
    # 15. CORRELATION FILTER
    # ------------------------------------------------------------------
    logger.info("[CORR] Correlation filter...")
    buy_signals = CorrelationFilter().filter(buy_signals, market_data, symbol_sectors)

    # Final sort
    buy_signals = sorted(
        buy_signals,
        key=lambda x: (x.quality_score or 0.0, x.confidence, x.ta_score),
        reverse=True,
    )
    logger.info(f"Final: {len(buy_signals)} BUY signals after all 13 filters")

    remaining_slots = max(0, MAX_OPEN_POSITIONS - open_positions)
    actionable_limit = min(TOP_N_SIGNALS, remaining_slots)
    actionable_signals = buy_signals[:actionable_limit]
    if remaining_slots <= 0:
        logger.info(f"[M7] Max open positions reached ({MAX_OPEN_POSITIONS}) -- skipping new BUY executions")
    elif actionable_limit < min(TOP_N_SIGNALS, len(buy_signals)):
        logger.info(f"[M7] Limiting executions to {actionable_limit} BUY signals due to open position cap")

    _print_signals(actionable_signals, regime)

    # ------------------------------------------------------------------
    # 16. MEMORY + TELEGRAM + EXECUTE
    # ------------------------------------------------------------------
    memory = PortfolioMemory()
    signal_ids = {}
    for sig in actionable_signals:
        signal_ids[sig.symbol] = memory.save_signal(sig)

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
    summary = executor.get_portfolio_summary()
    memory.save_snapshot(summary)

    # Readiness check
    try:
        from readiness.checker import ReadinessChecker
        report = ReadinessChecker().check()
        logger.info(f"Phase 2: {report.passed_count}/{report.total_gates} gates")
    except Exception as e:
        logger.debug(f"Readiness skipped: {e}")

    elapsed = (datetime.now() - start_time).seconds
    logger.info(f"\nDone in {elapsed}s | "
                f"Signals: {len(actionable_signals)} | "
                f"Trades: {stats['total_trades']} | "
                f"Win rate: {stats['win_rate_pct']:.1f}%")
    logger.info("=" * 60)
    return actionable_signals


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    try:
        hi, lo, cl = df["high"], df["low"], df["close"]
        tr = pd.concat([hi-lo, (hi-cl.shift()).abs(),
                        (lo-cl.shift()).abs()], axis=1).max(axis=1)
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


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_agent(dry_run=args.dry_run)
