# =============================================================================
# strategy/decision_journal.py — Per-signal audit trail
#
# Every signal carries a DecisionJournal through the pipeline.
# Each analysis module appends a ModuleVote to the correct layer.
# The journal is persisted to SQLite after execution/abstention.
#
# Layers:
#   Layer 1 — Setup Quality    (TA inputs only)
#   Layer 2 — Market Permission (macro + event inputs)
#   Layer 3 — Execution Sizing  (risk + portfolio inputs)
# =============================================================================

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ModuleVote:
    """A single module's contribution to the decision at a specific layer."""
    module:               str    # "technical", "fii_dii", "support_resistance", etc.
    layer:                int    # 1, 2, or 3
    vote:                 str    # "BUY" | "SELL" | "NEUTRAL" | "BLOCK" | "REDUCE"
    raw_score:            float  # score before weighting
    weight:               float  # regime-conditional weight (updated in Phase 6)
    weighted_contribution: float # raw_score * weight
    note:                 str    # one-line human-readable reason


@dataclass
class SizingRationale:
    """Breakdown of every multiplier that went into the final position size."""
    base_risk_pct:      float = 0.0
    confidence_mult:    float = 1.0
    kelly_mult:         float = 1.0
    vix_mult:           float = 1.0
    regime_mult:        float = 1.0
    pattern_mult:       float = 1.0
    sector_mult:        float = 1.0
    fii_mult:           float = 1.0
    sentiment_modifier: float = 0.0   # ±10% additive, not multiplicative
    combined_mult:      float = 1.0
    final_risk_pct:     float = 0.0
    final_size:         int   = 0


@dataclass
class DecisionJournal:
    """
    Complete audit trail for one signal from universe entry to execution/abstention.
    Attached to TradeSignal and persisted to decision_journals table.
    """

    symbol:           str
    timestamp:        datetime
    regime:           str
    regime_stability: int
    breadth_signal:   str        = "unknown"
    market_context:   dict       = field(default_factory=dict)   # pcr, fii_net, sector

    # Module votes per layer (appended as pipeline runs)
    layer1_votes: list[ModuleVote] = field(default_factory=list)
    layer2_votes: list[ModuleVote] = field(default_factory=list)
    layer3_votes: list[ModuleVote] = field(default_factory=list)

    # Risk gate result (Phase 2)
    risk_gate_passed: bool       = True
    risk_gate_blocks: list[dict] = field(default_factory=list)   # [{check, reason, value}]

    # Sizing trace
    sizing_rationale: SizingRationale = field(default_factory=SizingRationale)

    # Final decision
    final_action:       str           = "HOLD"
    abstention_reason:  Optional[str] = None   # None if executed

    # Post-trade outcomes (filled by outcome tracker — Phase 4)
    outcome_1d:   Optional[float] = None
    outcome_3d:   Optional[float] = None
    outcome_5d:   Optional[float] = None
    outcome_exit: Optional[float] = None

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    def add_vote(self, layer: int, module: str, vote: str,
                 raw_score: float = 0.0, weight: float = 1.0,
                 note: str = "") -> None:
        """Append a module vote to the correct layer list."""
        mv = ModuleVote(
            module=module, layer=layer, vote=vote,
            raw_score=raw_score, weight=weight,
            weighted_contribution=round(raw_score * weight, 4),
            note=note,
        )
        if layer == 1:
            self.layer1_votes.append(mv)
        elif layer == 2:
            self.layer2_votes.append(mv)
        elif layer == 3:
            self.layer3_votes.append(mv)

    def add_block(self, check: str, reason: str, value=None) -> None:
        """Record a risk gate block."""
        self.risk_gate_passed = False
        self.risk_gate_blocks.append({"check": check, "reason": reason, "value": value})

    def bullish_votes(self, layer: int = 1) -> int:
        src = self.layer1_votes if layer == 1 else (
              self.layer2_votes if layer == 2 else self.layer3_votes)
        return sum(1 for v in src if v.vote in ("BUY", "ALLOW"))

    def bearish_votes(self, layer: int = 1) -> int:
        src = self.layer1_votes if layer == 1 else (
              self.layer2_votes if layer == 2 else self.layer3_votes)
        return sum(1 for v in src if v.vote in ("SELL", "BLOCK", "REDUCE"))

    def to_dict(self) -> dict:
        """Serialise for SQLite storage."""
        import json as _json
        from dataclasses import asdict

        def _safe(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            if isinstance(obj, (list, dict)):
                return obj
            return obj

        d = {
            "symbol":           self.symbol,
            "timestamp":        self.timestamp.isoformat(),
            "regime":           self.regime,
            "regime_stability": self.regime_stability,
            "breadth_signal":   self.breadth_signal,
            "market_context":   self.market_context,
            "layer1_votes":     [asdict(v) for v in self.layer1_votes],
            "layer2_votes":     [asdict(v) for v in self.layer2_votes],
            "layer3_votes":     [asdict(v) for v in self.layer3_votes],
            "risk_gate_passed": self.risk_gate_passed,
            "risk_gate_blocks": self.risk_gate_blocks,
            "sizing_rationale": asdict(self.sizing_rationale),
            "final_action":     self.final_action,
            "abstention_reason":self.abstention_reason,
            "outcome_1d":       self.outcome_1d,
            "outcome_3d":       self.outcome_3d,
            "outcome_5d":       self.outcome_5d,
            "outcome_exit":     self.outcome_exit,
        }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DecisionJournal":
        """Deserialise from SQLite JSON blob."""
        from dataclasses import fields as dc_fields

        j = cls(
            symbol=d["symbol"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            regime=d.get("regime", "unknown"),
            regime_stability=d.get("regime_stability", 0),
            breadth_signal=d.get("breadth_signal", "unknown"),
            market_context=d.get("market_context", {}),
            risk_gate_passed=d.get("risk_gate_passed", True),
            risk_gate_blocks=d.get("risk_gate_blocks", []),
            final_action=d.get("final_action", "HOLD"),
            abstention_reason=d.get("abstention_reason"),
            outcome_1d=d.get("outcome_1d"),
            outcome_3d=d.get("outcome_3d"),
            outcome_5d=d.get("outcome_5d"),
            outcome_exit=d.get("outcome_exit"),
        )
        for raw in d.get("layer1_votes", []):
            j.layer1_votes.append(ModuleVote(**raw))
        for raw in d.get("layer2_votes", []):
            j.layer2_votes.append(ModuleVote(**raw))
        for raw in d.get("layer3_votes", []):
            j.layer3_votes.append(ModuleVote(**raw))

        sr_data = d.get("sizing_rationale", {})
        if sr_data:
            j.sizing_rationale = SizingRationale(**{
                k: sr_data[k] for k in sr_data if k in SizingRationale.__dataclass_fields__
            })
        return j
