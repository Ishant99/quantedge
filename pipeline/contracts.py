# =============================================================================
# pipeline/contracts.py — Typed data contracts between pipeline stages
#
# Each dataclass defines the I/O boundary for a pipeline stage.
# Stages communicate exclusively through these typed objects so that
# adding or changing a stage never silently breaks another.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class MarketContext:
    """Output of Stage 1 (market_context). Passed through all subsequent stages."""
    regime:           str    # bull | bear | sideways | recovery
    regime_stability: int    # 0-2  (hysteresis count)
    pcr_signal:       str    # buy | sell | strong_sell | neutral
    fii_signal:       str    # buy | sell | strong_sell | neutral
    breadth_signal:   str    # strong | moderate | weak | very_weak
    nifty_trend:      str    # up | down | flat
    sector_scores:    dict   # {sector: score 0-10}
    timestamp:        datetime


@dataclass
class FeatureSet:
    """Output of Stages 2-4 (data_fetch + technical + sentiment) for one symbol."""
    symbol:         str
    ta_result:      Any          # TAResult
    sentiment_result: Any        # SentimentResult
    df:             Any          # pd.DataFrame (OHLCV)
    sector:         str  = ""
    company_name:   str  = ""
    earnings_days:  int  = 999
    fno_banned:     bool = False


@dataclass
class Allocation:
    """Output of Stages 5-8 (signal_gen + permission + risk_gate + sizing) for one symbol."""
    signal:            Any         # TradeSignal
    sizing:            Any         # SizingResult | None
    permission:        Any         # PermissionResult
    risk_passed:       bool = True
    abstained:         bool = False
    abstention_reason: str  = ""


@dataclass
class PipelineResult:
    """Final result returned by TradingPipeline.run()."""
    timestamp:        datetime
    regime:           str
    total_symbols:    int
    signals_generated: int
    buys:             int
    sells:            int
    holds:            int
    blocked:          int
    abstained:        int
    allocations:      list        # list[Allocation]
    market_context:   Any         # MarketContext
    duration_seconds: float = 0.0
    errors:           list  = field(default_factory=list)
