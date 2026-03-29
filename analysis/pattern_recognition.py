# =============================================================================
# analysis/pattern_recognition.py — AI Chart Pattern Recognition
#
# Detects classic chart patterns from price data:
#   - Double Bottom (bullish reversal)
#   - Cup & Handle (bullish continuation)
#   - Bull Flag (bullish continuation)
#   - Head & Shoulders (bearish reversal)
#   - Double Top (bearish reversal)
#   - Golden Cross / Death Cross (EMA crossovers)
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from utils import get_logger

logger = get_logger("PatternRecognition")


@dataclass
class PatternResult:
    symbol:           str
    patterns_found:   list[str]       # list of detected pattern names
    pattern_score:    float           # 0-10, higher = stronger patterns
    primary_pattern:  str             # strongest detected pattern
    bias:             str             # bullish | bearish | neutral
    confidence:       float           # 0-1
    description:      str             # human readable summary


class PatternRecogniser:
    """
    Detects classic chart patterns using price data.
    Each pattern adds to the signal score.
    """

    def analyse(self, symbol: str, df: pd.DataFrame) -> PatternResult:
        """Run all pattern detectors on a stock's price history."""
        try:
            close  = df["close"].values
            high   = df["high"].values
            low    = df["low"].values
            volume = df["volume"].values

            patterns  = []
            scores    = []

            # Run all detectors
            checks = [
                self._golden_cross(close),
                self._death_cross(close),
                self._double_bottom(close, low),
                self._double_top(close, high),
                self._bull_flag(close, high, low, volume),
                self._cup_and_handle(close),
                self._head_and_shoulders(close, high),
            ]

            for name, score, detected in checks:
                if detected:
                    patterns.append(name)
                    scores.append(score)

            if not patterns:
                return PatternResult(
                    symbol=symbol, patterns_found=[],
                    pattern_score=5.0, primary_pattern="None",
                    bias="neutral", confidence=0.3,
                    description="No significant patterns detected"
                )

            total_score  = min(10.0, sum(scores) / len(scores) + len(patterns) * 0.3)
            primary      = patterns[scores.index(max(scores))]

            # Determine bias
            bullish_pats = {"Double Bottom","Cup & Handle","Bull Flag","Golden Cross"}
            bearish_pats = {"Head & Shoulders","Double Top","Death Cross"}
            bull_count   = sum(1 for p in patterns if p in bullish_pats)
            bear_count   = sum(1 for p in patterns if p in bearish_pats)

            if bull_count > bear_count:
                bias = "bullish"
            elif bear_count > bull_count:
                bias = "bearish"
            else:
                bias = "neutral"

            confidence = min(0.9, 0.4 + len(patterns) * 0.1 + max(scores) * 0.03)

            description = f"{', '.join(patterns)} detected. Primary: {primary}. Bias: {bias}."

            return PatternResult(
                symbol        = symbol,
                patterns_found= patterns,
                pattern_score = round(total_score, 2),
                primary_pattern= primary,
                bias          = bias,
                confidence    = round(confidence, 2),
                description   = description,
            )

        except Exception as e:
            logger.debug(f"{symbol} pattern detection failed: {e}")
            return self._default(symbol)

    def analyse_all(self, market_data: dict) -> dict[str, PatternResult]:
        results = {}
        for sym, df in market_data.items():
            results[sym] = self.analyse(sym, df)
        bullish = sum(1 for r in results.values() if r.bias == "bullish")
        logger.info(f"Patterns: {bullish}/{len(results)} stocks with bullish patterns")
        return results

    # ------------------------------------------------------------------
    # Pattern detectors
    # ------------------------------------------------------------------

    def _golden_cross(self, close: np.ndarray) -> tuple[str, float, bool]:
        """EMA50 crosses above EMA200 — strong bullish signal."""
        s   = pd.Series(close)
        e50 = s.ewm(span=50).mean()
        e200= s.ewm(span=200).mean()
        # Cross in last 5 days
        recent_cross = (e50.iloc[-1] > e200.iloc[-1] and
                        e50.iloc[-6] <= e200.iloc[-6])
        above        = e50.iloc[-1] > e200.iloc[-1]
        if recent_cross:
            return "Golden Cross", 8.5, True
        return "Golden Cross", 8.5, False

    def _death_cross(self, close: np.ndarray) -> tuple[str, float, bool]:
        """EMA50 crosses below EMA200 — strong bearish signal."""
        s   = pd.Series(close)
        e50 = s.ewm(span=50).mean()
        e200= s.ewm(span=200).mean()
        recent_cross = (e50.iloc[-1] < e200.iloc[-1] and
                        e50.iloc[-6] >= e200.iloc[-6])
        if recent_cross:
            return "Death Cross", 2.0, True
        return "Death Cross", 2.0, False

    def _double_bottom(self, close: np.ndarray,
                       low: np.ndarray) -> tuple[str, float, bool]:
        """
        Two similar lows separated by a peak — bullish reversal.
        W-shape pattern.
        """
        if len(low) < 40:
            return "Double Bottom", 8.0, False

        window = low[-60:]
        # Find two lowest points
        idx1   = np.argmin(window)
        temp   = window.copy()
        temp[max(0,idx1-5):idx1+5] = np.inf
        idx2   = np.argmin(temp)

        if idx1 == idx2:
            return "Double Bottom", 8.0, False

        bottom1 = window[idx1]
        bottom2 = window[idx2]

        # Bottoms should be similar (within 3%)
        similar = abs(bottom1 - bottom2) / bottom1 < 0.03

        # Must be separated by at least 10 bars
        separated = abs(idx1 - idx2) >= 10

        # Recent price should be breaking above the middle peak
        mid_high = max(window[min(idx1,idx2):max(idx1,idx2)])
        breaking = close[-1] > mid_high * 0.98

        detected = similar and separated and breaking
        return "Double Bottom", 8.0, detected

    def _double_top(self, close: np.ndarray,
                    high: np.ndarray) -> tuple[str, float, bool]:
        """Two similar highs — bearish reversal."""
        if len(high) < 40:
            return "Double Top", 2.5, False

        window = high[-60:]
        idx1   = np.argmax(window)
        temp   = window.copy()
        temp[max(0,idx1-5):idx1+5] = -np.inf
        idx2   = np.argmax(temp)

        if idx1 == idx2:
            return "Double Top", 2.5, False

        top1    = window[idx1]
        top2    = window[idx2]
        similar = abs(top1 - top2) / top1 < 0.03
        separated = abs(idx1 - idx2) >= 10

        # Recent price falling below the middle trough
        mid_low  = min(window[min(idx1,idx2):max(idx1,idx2)])
        breaking = close[-1] < mid_low * 1.02

        detected = similar and separated and breaking
        return "Double Top", 2.5, detected

    def _bull_flag(self, close: np.ndarray, high: np.ndarray,
                   low: np.ndarray, volume: np.ndarray) -> tuple[str, float, bool]:
        """
        Sharp upward move (pole) followed by tight consolidation (flag).
        High-probability continuation pattern.
        """
        if len(close) < 30:
            return "Bull Flag", 7.5, False

        # Pole: strong rise in last 10-20 days
        pole_start = -25
        pole_end   = -10
        pole_return = (close[pole_end] - close[pole_start]) / close[pole_start]

        # Flag: tight consolidation in last 10 days
        flag_high  = max(high[-10:])
        flag_low   = min(low[-10:])
        flag_range = (flag_high - flag_low) / flag_high

        # Volume: higher during pole, lower during flag
        pole_vol   = np.mean(volume[pole_start:pole_end])
        flag_vol   = np.mean(volume[-10:])

        detected = (
            pole_return > 0.08 and       # pole: 8%+ rise
            flag_range  < 0.05 and       # flag: tight < 5% range
            flag_vol    < pole_vol        # volume contracts in flag
        )
        return "Bull Flag", 7.5, detected

    def _cup_and_handle(self, close: np.ndarray) -> tuple[str, float, bool]:
        """
        U-shaped recovery (cup) followed by small pullback (handle).
        Strong bullish continuation.
        """
        if len(close) < 60:
            return "Cup & Handle", 8.0, False

        cup = close[-60:-10]
        handle = close[-10:]

        # Cup: U shape — high at start and end, low in middle
        cup_start = cup[0]
        cup_end   = cup[-1]
        cup_low   = min(cup)

        cup_depth  = (min(cup_start, cup_end) - cup_low) / min(cup_start, cup_end)
        cup_symmetry = abs(cup_start - cup_end) / cup_start

        # Handle: small pullback < 50% of cup depth
        handle_high = max(handle)
        handle_low  = min(handle)
        handle_retrace = (handle_high - handle_low) / handle_high

        detected = (
            cup_depth      > 0.10 and    # cup depth > 10%
            cup_depth      < 0.35 and    # but not too deep
            cup_symmetry   < 0.05 and    # symmetric
            handle_retrace < 0.05 and    # handle tight
            close[-1]      > cup_end * 0.98  # breaking out
        )
        return "Cup & Handle", 8.0, detected

    def _head_and_shoulders(self, close: np.ndarray,
                            high: np.ndarray) -> tuple[str, float, bool]:
        """
        Three peaks with middle highest — bearish reversal pattern.
        """
        if len(high) < 60:
            return "Head & Shoulders", 2.0, False

        window = high[-60:]
        # Find 3 peaks
        peaks = []
        for i in range(5, len(window)-5):
            if window[i] == max(window[i-5:i+5]):
                peaks.append((i, window[i]))

        if len(peaks) < 3:
            return "Head & Shoulders", 2.0, False

        # Check last 3 peaks: left shoulder, head, right shoulder
        left, head, right = peaks[-3], peaks[-2], peaks[-1]

        # Head should be highest
        head_highest  = head[1] > left[1] and head[1] > right[1]
        # Shoulders roughly equal
        shoulders_eq  = abs(left[1] - right[1]) / left[1] < 0.05
        # Price breaking below neckline
        neckline      = min(close[left[0]], close[right[0]])
        breaking_down = close[-1] < neckline * 1.01

        detected = head_highest and shoulders_eq and breaking_down
        return "Head & Shoulders", 2.0, detected

    def _default(self, symbol: str) -> PatternResult:
        return PatternResult(
            symbol=symbol, patterns_found=[],
            pattern_score=5.0, primary_pattern="None",
            bias="neutral", confidence=0.3,
            description="Pattern analysis unavailable"
        )
