# =============================================================================
# strategy/regime_weights.py — Regime-conditional module weights
#
# Different market regimes weight different signals differently.
# Weights are relative importance scalars, not probabilities.
# They are organised by layer but stored flat per regime for fast lookup.
#
# Layer 1 — Signal generation:  technical, trend_strength, momentum,
#                                pattern, volume_profile
# Layer 2 — Permission:         market_regime, fii_dii, market_breadth,
#                                sector_rotation, earnings_guard
# Layer 3 — Sizing:             confidence, kelly, vix, regime,
#                                sector, sentiment
# =============================================================================

from __future__ import annotations

# ---------------------------------------------------------------------------
# Layer membership — used by get_layer_weights()
# ---------------------------------------------------------------------------
_LAYER_MODULES: dict[int, list[str]] = {
    1: ["technical", "trend_strength", "momentum", "pattern", "volume_profile"],
    2: ["market_regime", "fii_dii", "market_breadth", "sector_rotation", "earnings_guard"],
    3: ["confidence", "kelly", "vix", "regime", "sector", "sentiment"],
}

# ---------------------------------------------------------------------------
# Weight tables: {regime: {module_name: weight}}
# Weights sum to ~1.0 per layer per regime (relative importance, not probs).
# ---------------------------------------------------------------------------
REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    "bull": {
        # Layer 1 — signal generation weights
        "technical":         0.50,
        "trend_strength":    0.20,
        "momentum":          0.15,
        "pattern":           0.10,
        "volume_profile":    0.05,
        # Layer 2 — permission weights
        "market_regime":     0.30,
        "fii_dii":           0.25,
        "market_breadth":    0.20,
        "sector_rotation":   0.15,
        "earnings_guard":    0.10,
        # Layer 3 — sizing weights
        "confidence":        0.35,
        "kelly":             0.20,
        "vix":               0.15,
        "regime":            0.15,
        "sector":            0.10,
        "sentiment":         0.05,
    },
    "bear": {
        "technical":         0.40,
        "trend_strength":    0.30,
        "momentum":          0.15,
        "pattern":           0.10,
        "volume_profile":    0.05,
        "market_regime":     0.40,
        "fii_dii":           0.30,
        "market_breadth":    0.20,
        "sector_rotation":   0.05,
        "earnings_guard":    0.05,
        "confidence":        0.30,
        "kelly":             0.25,
        "vix":               0.25,
        "regime":            0.15,
        "sector":            0.05,
        "sentiment":         0.00,
    },
    "sideways": {
        "technical":         0.45,
        "trend_strength":    0.15,
        "momentum":          0.20,
        "pattern":           0.15,
        "volume_profile":    0.05,
        "market_regime":     0.25,
        "fii_dii":           0.20,
        "market_breadth":    0.25,
        "sector_rotation":   0.20,
        "earnings_guard":    0.10,
        "confidence":        0.30,
        "kelly":             0.20,
        "vix":               0.20,
        "regime":            0.20,
        "sector":            0.05,
        "sentiment":         0.05,
    },
    "recovery": {
        "technical":         0.50,
        "trend_strength":    0.25,
        "momentum":          0.10,
        "pattern":           0.10,
        "volume_profile":    0.05,
        "market_regime":     0.35,
        "fii_dii":           0.30,
        "market_breadth":    0.15,
        "sector_rotation":   0.15,
        "earnings_guard":    0.05,
        "confidence":        0.35,
        "kelly":             0.20,
        "vix":               0.20,
        "regime":            0.15,
        "sector":            0.05,
        "sentiment":         0.05,
    },
}


def get_weight(regime: str, module: str, layer: str = None, default: float = 1.0) -> float:
    """
    Get regime-conditional weight for a module.

    Args:
        regime:  Market regime key ("bull" | "bear" | "sideways" | "recovery").
                 Falls back to "bull" weights if unknown regime is supplied.
        module:  Module name (e.g. "technical", "vix").
        layer:   Unused — kept for API symmetry; weight table is flat per regime.
        default: Returned when the module is not found in the table.

    Returns:
        float weight value, or *default* if module not present.
    """
    regime_table = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["bull"])
    return regime_table.get(module, default)


def get_layer_weights(regime: str, layer: int) -> dict[str, float]:
    """
    Get all weights for a specific layer in a regime.

    Args:
        regime: Market regime key.
        layer:  1 (signal generation), 2 (permission), or 3 (sizing).

    Returns:
        Dict of {module_name: weight} for that layer.
        Raises ValueError for unknown layer numbers.
    """
    if layer not in _LAYER_MODULES:
        raise ValueError(
            f"Unknown layer {layer!r}. Valid layers: {sorted(_LAYER_MODULES)}"
        )
    regime_table = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["bull"])
    modules = _LAYER_MODULES[layer]
    return {m: regime_table.get(m, 0.0) for m in modules}


class RegimeWeightedScorer:
    """
    Applies regime-conditional weights to module scores to compute
    a weighted composite score.

    Usage::

        scorer = RegimeWeightedScorer()
        composite = scorer.score(
            regime="bull",
            module_scores={"technical": 0.8, "trend_strength": 0.6}
        )
    """

    def score(self, regime: str, module_scores: dict[str, float]) -> float:
        """
        Compute a weighted composite score from per-module raw scores.

        Args:
            regime:        Market regime key. Falls back to "bull" if unknown.
            module_scores: Mapping of {module_name: raw_score} where each
                           raw_score is in [0, 1].

        Returns:
            Weighted composite score in [0, 1], rounded to 4 decimal places.
            Returns 0.5 (neutral) when no matching weights are found.
        """
        weights = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["bull"])
        total_w  = 0.0
        total_ws = 0.0
        for module, raw_score in module_scores.items():
            w = weights.get(module, 0.0)
            total_w  += w
            total_ws += w * float(raw_score)
        return round(total_ws / total_w, 4) if total_w > 0 else 0.5

    def score_layer(
        self, regime: str, layer: int, module_scores: dict[str, float]
    ) -> float:
        """
        Convenience: compute weighted composite restricted to one layer.

        Only modules belonging to *layer* are considered; others in
        module_scores are silently ignored.

        Returns:
            Weighted composite for the layer, or 0.5 if no overlap found.
        """
        layer_modules = set(_LAYER_MODULES.get(layer, []))
        filtered = {m: s for m, s in module_scores.items() if m in layer_modules}
        return self.score(regime, filtered)
