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

import os
import sqlite3
import json
from collections import defaultdict
from datetime import datetime, timedelta
from utils import get_logger

logger = get_logger("RegimeWeights")

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


# =============================================================================
# Task 6.1 — RegimeWeightManager
# =============================================================================

class RegimeWeightManager:
    """
    Returns regime-conditional module weights.

    Priors are from REGIME_WEIGHTS. After >= MIN_CALIBRATION_TRADES resolved
    trades per regime exist in decision_journals, weights are replaced with
    calibration-derived values from the latest CalibrationReport.

    Call recalibrate_monthly() from the scheduler to refresh.
    """

    MIN_CALIBRATION_TRADES = 50

    def get_weights(self, regime: str) -> dict:
        """
        Return weight dict for *regime*.
        Uses calibration-derived weights when data is sufficient,
        otherwise falls back to REGIME_WEIGHTS priors.
        """
        try:
            cal = self._load_calibration_weights(regime)
            if cal:
                return cal
        except Exception as e:
            logger.debug(f"RegimeWeightManager: calibration load failed: {e}")
        r = regime.lower()
        return dict(REGIME_WEIGHTS.get(r, REGIME_WEIGHTS.get("bull", {})))

    def _load_calibration_weights(self, regime: str) -> dict:
        """
        Load CalibrationReport and derive weights from per-module win rates.
        Returns empty dict if insufficient data (< MIN_CALIBRATION_TRADES).
        """
        from analysis.calibration import ConfidenceCalibrator
        report = ConfidenceCalibrator.load_latest_report()
        if not report or not report.module_stats:
            return {}

        regime_data = report.module_stats.get(regime.lower(), {})
        if not regime_data:
            return {}

        # Filter modules with enough trades
        qualified = {
            m: d for m, d in regime_data.items()
            if d.get("n_trades", 0) >= self.MIN_CALIBRATION_TRADES
        }
        if not qualified:
            return {}

        # Map edge (tp_rate - 0.5) to weight; negative-edge modules get weight 0
        raw: dict[str, float] = {}
        for module, data in qualified.items():
            edge = data.get("edge", 0.0)
            raw[module] = max(0.0, 0.5 + edge)

        total = sum(raw.values())
        if total <= 0:
            return {}
        return {m: round(w / total, 4) for m, w in raw.items()}

    def recalibrate_monthly(self) -> bool:
        """
        Run compute_module_calibration(min_trades=50) and persist result.
        Returns True if a new report was saved.
        """
        try:
            from analysis.calibration import ConfidenceCalibrator
            cal    = ConfidenceCalibrator()
            report = cal.compute_module_calibration(min_trades=self.MIN_CALIBRATION_TRADES)
            if report:
                cal.save_calibration_report(report)
                logger.info("RegimeWeightManager: monthly recalibration saved")
                return True
            logger.info(
                "RegimeWeightManager: insufficient data for recalibration "
                f"(< {self.MIN_CALIBRATION_TRADES} trades)"
            )
        except Exception as e:
            logger.warning(f"RegimeWeightManager.recalibrate_monthly: {e}")
        return False


# =============================================================================
# Task 6.3 — RedundancyDetector
# =============================================================================

class RedundancyDetector:
    """
    Detects redundant modules by computing pairwise vote agreement rates
    from decision_journals in the last N days.

    Agreement rate = fraction of signals where both modules voted the same way.
    Pairs with agreement > 0.85 are flagged as redundant.
    When applied, the lower-impact module in each redundant pair has its
    weight halved in REGIME_WEIGHTS.
    """

    REDUNDANCY_THRESHOLD = 0.85

    def compute(self, days: int = 90) -> dict:
        """
        Load layer1 + layer2 votes from decision_journals for the last *days* days.
        Compute pairwise vote agreement rate between all module pairs.

        Returns:
            {(module_a, module_b): agreement_rate, ...}
            sorted descending by agreement_rate.
        """
        from config import SQLITE_DB_FILE
        if not os.path.exists(SQLITE_DB_FILE):
            return {}

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        try:
            with sqlite3.connect(SQLITE_DB_FILE) as conn:
                rows = conn.execute("""
                    SELECT json_blob FROM decision_journals
                    WHERE timestamp >= ?
                """, (cutoff,)).fetchall()
        except Exception as e:
            logger.warning(f"RedundancyDetector.compute: query failed: {e}")
            return {}

        # Collect per-signal vote vectors: {signal_idx: {module: vote_direction}}
        signal_votes: list[dict[str, str]] = []
        for (blob_text,) in rows:
            try:
                blob = json.loads(blob_text) if blob_text else {}
            except (json.JSONDecodeError, TypeError):
                continue
            votes: dict[str, str] = {}
            for key in ("layer1_votes", "layer2_votes"):
                v = blob.get(key)
                if isinstance(v, dict):
                    for module, vote in v.items():
                        direction = (
                            "BUY" if (vote == "BUY" or vote == 1
                                      or (isinstance(vote, str) and vote.upper() == "BUY"))
                            else "SELL" if (vote == "SELL" or vote == -1
                                           or (isinstance(vote, str) and vote.upper() == "SELL"))
                            else "NEUTRAL"
                        )
                        votes[module] = direction
            if votes:
                signal_votes.append(votes)

        if not signal_votes:
            return {}

        # Accumulate pairwise agreement counts
        pair_agree: dict[tuple, int] = defaultdict(int)
        pair_total: dict[tuple, int] = defaultdict(int)

        for votes in signal_votes:
            modules = sorted(votes.keys())
            for i, ma in enumerate(modules):
                for mb in modules[i + 1:]:
                    key = (ma, mb)
                    pair_total[key] += 1
                    if votes[ma] == votes[mb]:
                        pair_agree[key] += 1

        result = {}
        for pair, total in pair_total.items():
            if total >= 10:  # minimum sample for reliability
                result[pair] = round(pair_agree[pair] / total, 4)

        return dict(sorted(result.items(), key=lambda kv: kv[1], reverse=True))

    def get_redundant_pairs(
        self, days: int = 90, threshold: float = None
    ) -> list[tuple]:
        """
        Returns list of (module_a, module_b) pairs with agreement above threshold.
        """
        t = threshold if threshold is not None else self.REDUNDANCY_THRESHOLD
        agreements = self.compute(days=days)
        return [pair for pair, rate in agreements.items() if rate >= t]

    def apply_to_weights(
        self, threshold: float = None, module_attribution: dict = None
    ) -> dict:
        """
        For each redundant pair, halve the weight of the lower-edge module.
        Uses module_attribution (from CalibrationReport) to decide which
        module has lower edge; falls back to alphabetical order if absent.

        Returns a copy of REGIME_WEIGHTS with redundant modules down-weighted.
        """
        import copy
        weights = copy.deepcopy(REGIME_WEIGHTS)
        pairs = self.get_redundant_pairs(threshold=threshold)

        for ma, mb in pairs:
            if module_attribution:
                edge_a = module_attribution.get(ma, {}).get("edge", 0)
                edge_b = module_attribution.get(mb, {}).get("edge", 0)
                weaker = ma if edge_a <= edge_b else mb
            else:
                weaker = mb  # alphabetical fallback

            logger.info(
                f"RedundancyDetector: halving weight of '{weaker}' "
                f"(redundant with '{ma if weaker == mb else mb}')"
            )
            for regime_table in weights.values():
                if weaker in regime_table:
                    regime_table[weaker] = round(regime_table[weaker] * 0.5, 4)

        return weights
