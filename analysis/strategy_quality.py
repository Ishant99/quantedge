import sqlite3
from dataclasses import dataclass
from typing import Any

from config import (
    SQLITE_DB_FILE,
    STRATEGY_QUALITY_BLOCK_WEAK_SYMBOLS,
    STRATEGY_QUALITY_CONF_BUCKET_WEIGHT,
    STRATEGY_QUALITY_MAX_BOOST,
    STRATEGY_QUALITY_MAX_PENALTY,
    STRATEGY_QUALITY_MIN_RESOLVED,
    STRATEGY_QUALITY_REGIME_WEIGHT,
    STRATEGY_QUALITY_SETUP_WEIGHT,
    STRATEGY_QUALITY_STRONG_SYMBOL_TP_PCT,
    STRATEGY_QUALITY_SYMBOL_WEIGHT,
    STRATEGY_QUALITY_WEAK_SYMBOL_TP_PCT,
)
from utils import get_logger


logger = get_logger("StrategyQuality")


@dataclass
class QualityAssessment:
    setup_type: str
    quality_score: float
    expectancy_score: float
    adjusted_confidence: float
    size_multiplier: float
    symbol_edge: float
    setup_edge: float
    confidence_bucket_edge: float
    regime_adjustment: float
    blocked: bool
    block_reason: str
    flags: list[str]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _setup_from_fields(
    action: str,
    reasoning: str = "",
    pattern_name: str = "",
    pattern_bias: str = "",
    near_support: bool = False,
    vp_signal: str = "",
    weekly_trend: str = "",
    sentiment: str = "",
) -> str:
    reason_l = (reasoning or "").lower()
    pattern_l = (pattern_name or "").lower()
    action_l = (action or "").upper()
    if action_l == "SELL":
        return "de_risk"
    if "breakout" in reason_l or "breakout" in pattern_l:
        return "breakout"
    if pattern_bias == "bullish" and pattern_name:
        return "pattern_follow"
    if near_support:
        return "support_reversal"
    if vp_signal == "buy":
        return "volume_support"
    if weekly_trend == "up":
        return "trend_follow"
    if sentiment and sentiment.lower() in {"positive", "negative"}:
        return "sentiment_push"
    return "technical_base"


def _conf_bucket(confidence: float) -> str:
    if confidence < 0.50:
        return "0-50%"
    if confidence < 0.65:
        return "50-65%"
    if confidence < 0.80:
        return "65-80%"
    return "80%+"


class StrategyQualityEngine:
    def __init__(self, db_path: str = SQLITE_DB_FILE):
        self.db_path = db_path
        self.symbol_stats: dict[str, dict[str, float]] = {}
        self.setup_stats: dict[str, dict[str, float]] = {}
        self.conf_bucket_stats: dict[str, dict[str, float]] = {}
        self._load_history()

    def _load_history(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cols = [r[1] for r in conn.execute("PRAGMA table_info(signals)").fetchall()]
                if "outcome" not in cols:
                    return
                has_setup = "setup_type" in cols
                has_regime = "regime_tag" in cols
                query = """
                    SELECT symbol, action, confidence, ta_score, sentiment, reasoning, outcome
                    {extra_cols}
                    FROM signals
                    WHERE outcome IN ('TP_HIT','SL_HIT')
                """.format(
                    extra_cols=
                    (", setup_type" if has_setup else "")
                    + (", regime_tag" if has_regime else "")
                )
                rows = conn.execute(query).fetchall()
        except sqlite3.Error as exc:
            logger.warning(f"Strategy quality history load failed: {exc}")
            return

        symbol_bucket: dict[str, list[int]] = {}
        setup_bucket: dict[str, list[int]] = {}
        conf_bucket: dict[str, list[int]] = {}

        for row in rows:
            row = dict(row)
            win = 1 if row.get("outcome") == "TP_HIT" else 0
            symbol = str(row.get("symbol", "") or "").upper()
            if symbol:
                symbol_bucket.setdefault(symbol, []).append(win)
            setup = row.get("setup_type") or _setup_from_fields(
                action=row.get("action", ""),
                reasoning=row.get("reasoning", ""),
                sentiment=row.get("sentiment", ""),
            )
            setup_bucket.setdefault(setup, []).append(win)
            conf_key = _conf_bucket(_safe_float(row.get("confidence")))
            conf_bucket.setdefault(conf_key, []).append(win)

        self.symbol_stats = {k: self._edge_stats(v) for k, v in symbol_bucket.items()}
        self.setup_stats = {k: self._edge_stats(v) for k, v in setup_bucket.items()}
        self.conf_bucket_stats = {k: self._edge_stats(v) for k, v in conf_bucket.items()}

    @staticmethod
    def _edge_stats(results: list[int]) -> dict[str, float]:
        resolved = len(results)
        tp_rate = (sum(results) / resolved * 100.0) if resolved else 0.0
        return {
            "resolved": resolved,
            "tp_rate": tp_rate,
            "edge": (tp_rate / 100.0) - 0.5,
        }

    def assess(
        self,
        signal,
        regime_tag: str,
        pattern_name: str = "",
        pattern_bias: str = "",
        near_support: bool = False,
        vp_signal: str = "",
        weekly_trend: str = "",
        sector: str = "",
    ) -> QualityAssessment:
        setup_type = _setup_from_fields(
            action=signal.action,
            reasoning=signal.reasoning,
            pattern_name=pattern_name,
            pattern_bias=pattern_bias,
            near_support=near_support,
            vp_signal=vp_signal,
            weekly_trend=weekly_trend,
            sentiment=signal.sentiment,
        )
        symbol_stats = self.symbol_stats.get(signal.symbol.upper(), {})
        setup_stats = self.setup_stats.get(setup_type, {})
        conf_stats = self.conf_bucket_stats.get(_conf_bucket(signal.confidence), {})

        symbol_edge = _safe_float(symbol_stats.get("edge"))
        setup_edge = _safe_float(setup_stats.get("edge"))
        conf_edge = _safe_float(conf_stats.get("edge"))

        regime_adjustment = 0.0
        flags: list[str] = []
        if regime_tag == "sideways":
            regime_adjustment -= STRATEGY_QUALITY_REGIME_WEIGHT
            flags.append("sideways_regime")
        elif regime_tag == "bull":
            regime_adjustment += STRATEGY_QUALITY_REGIME_WEIGHT / 2
            flags.append("bull_regime")

        adjustment = (
            setup_edge * STRATEGY_QUALITY_SETUP_WEIGHT
            + symbol_edge * STRATEGY_QUALITY_SYMBOL_WEIGHT
            + conf_edge * STRATEGY_QUALITY_CONF_BUCKET_WEIGHT
            + regime_adjustment
        )
        adjustment = max(-STRATEGY_QUALITY_MAX_PENALTY, min(STRATEGY_QUALITY_MAX_BOOST, adjustment))

        blocked = False
        block_reason = ""
        resolved = int(symbol_stats.get("resolved", 0) or 0)
        tp_rate = _safe_float(symbol_stats.get("tp_rate"))
        if (
            signal.action == "BUY"
            and STRATEGY_QUALITY_BLOCK_WEAK_SYMBOLS
            and resolved >= STRATEGY_QUALITY_MIN_RESOLVED
            and tp_rate <= STRATEGY_QUALITY_WEAK_SYMBOL_TP_PCT
        ):
            blocked = True
            block_reason = (
                f"Historical symbol edge weak: {signal.symbol} TP rate {tp_rate:.0f}% "
                f"across {resolved} resolved signals"
            )
            flags.append("blocked_weak_symbol")
        elif resolved >= STRATEGY_QUALITY_MIN_RESOLVED and tp_rate >= STRATEGY_QUALITY_STRONG_SYMBOL_TP_PCT:
            flags.append("strong_symbol_edge")
        if int(setup_stats.get("resolved", 0) or 0) >= STRATEGY_QUALITY_MIN_RESOLVED:
            flags.append(f"setup_edge_{setup_type}")

        adjusted_confidence = max(0.0, min(0.99, signal.confidence + adjustment))
        expectancy_score = round((symbol_edge * 0.5 + setup_edge * 0.35 + conf_edge * 0.15), 3)
        quality_score = round(
            adjusted_confidence * 100
            + expectancy_score * 35
            + max(0.0, signal.ta_score - 5.0) * 2,
            2,
        )
        size_multiplier = max(0.65, min(1.35, 1.0 + expectancy_score))

        return QualityAssessment(
            setup_type=setup_type,
            quality_score=quality_score,
            expectancy_score=expectancy_score,
            adjusted_confidence=adjusted_confidence,
            size_multiplier=size_multiplier,
            symbol_edge=round(symbol_edge, 3),
            setup_edge=round(setup_edge, 3),
            confidence_bucket_edge=round(conf_edge, 3),
            regime_adjustment=round(regime_adjustment, 3),
            blocked=blocked,
            block_reason=block_reason,
            flags=flags,
        )
