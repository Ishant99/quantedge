# =============================================================================
# analysis/signal_narrator.py — Signal Narrator
#
# Generates plain-English 2-3 sentence "trade stories" from a TradeSignal.
# Primarily template-based (no LLM required).
# If Ollama is available, optionally enhances with LLM polish.
#
# Usage:
#   from analysis.signal_narrator import SignalNarrator
#   narrator = SignalNarrator()
#   story = narrator.narrate(signal)   # returns str
# =============================================================================

import re
import requests
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import OLLAMA_BASE_URL, SENTIMENT_MODEL
from utils import get_logger

logger = get_logger("SignalNarrator")


class SignalNarrator:
    """
    Converts a TradeSignal into a concise, human-readable trade story.

    Template engine first — fast and always works.
    Ollama polish optional — enriches language when available.
    """

    def __init__(self, use_llm: bool = True):
        self._llm_enabled = use_llm and self._check_ollama()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def narrate(self, signal) -> str:
        """
        Generate a 2-3 sentence story explaining the trade signal.

        Args:
            signal: TradeSignal dataclass from strategy/engine.py

        Returns:
            Plain-English string, e.g.:
            "RELIANCE is showing a strong bullish setup with RSI recovering
             from oversold levels and a fresh MACD crossover. Volume is 2.4x
             average, confirming institutional buying. Entry near 2,840 with
             a stop at 2,790 targets a 3R reward."
        """
        template_story = self._template_story(signal)

        if self._llm_enabled:
            try:
                enhanced = self._llm_polish(signal, template_story)
                if enhanced:
                    return enhanced
            except Exception as e:
                logger.debug(f"LLM polish failed for {signal.symbol}: {e}")

        return template_story

    def narrate_all(self, signals: list) -> dict[str, str]:
        """Narrate all signals. Returns {symbol: story}."""
        return {s.symbol: self.narrate(s) for s in signals}

    # ------------------------------------------------------------------
    # Template engine
    # ------------------------------------------------------------------

    def _template_story(self, signal) -> str:
        sym   = signal.symbol
        conf  = signal.confidence
        action = signal.action
        raw   = signal.raw_ta or {}

        parts = []

        # --- Sentence 1: Setup summary ---
        if action == "BUY":
            setup = self._buy_setup_sentence(sym, conf, raw)
        elif action == "SELL":
            setup = self._sell_setup_sentence(sym, conf, raw)
        else:
            return (
                f"{sym} is consolidating with no clear directional edge "
                f"(confidence {conf:.0%}). Watching for a breakout before entering."
            )
        parts.append(setup)

        # --- Sentence 2: Confirmation detail ---
        detail = self._detail_sentence(raw, signal.sentiment, signal.sentiment_score)
        if detail:
            parts.append(detail)

        # --- Sentence 3: Risk/reward framing ---
        if action in ("BUY", "SELL"):
            rr = self._risk_reward_sentence(signal)
            parts.append(rr)

        return " ".join(parts)

    def _buy_setup_sentence(self, sym: str, conf: float, raw: dict) -> str:
        rsi   = raw.get("rsi", 50)
        adx   = raw.get("adx", 0)
        macd_hist = raw.get("macd_hist", 0)

        if rsi < 35:
            momentum = "bouncing back from oversold territory"
        elif macd_hist and macd_hist > 0:
            momentum = "with a fresh MACD bullish crossover"
        elif adx and adx > 25:
            momentum = "in a confirmed uptrend"
        else:
            momentum = "building upside momentum"

        conf_word = "strong" if conf >= 0.75 else ("moderate" if conf >= 0.6 else "developing")
        return (
            f"{sym} is showing a {conf_word} bullish setup {momentum} "
            f"({conf:.0%} confidence)."
        )

    def _sell_setup_sentence(self, sym: str, conf: float, raw: dict) -> str:
        rsi  = raw.get("rsi", 50)
        adx  = raw.get("adx", 0)
        bb_pct = raw.get("bb_pct", 0.5)

        if rsi > 70:
            pressure = "after reaching overbought RSI levels"
        elif bb_pct and bb_pct > 0.9:
            pressure = "near the upper Bollinger Band"
        elif adx and adx > 25:
            pressure = "in a confirmed downtrend"
        else:
            pressure = "showing distribution signals"

        conf_word = "strong" if conf <= 0.3 else "moderate"
        return (
            f"{sym} is showing a {conf_word} bearish setup {pressure} "
            f"({(1-conf):.0%} bearish confidence)."
        )

    def _detail_sentence(self, raw: dict, sentiment: str, sent_score: float) -> str:
        details = []

        vol_ratio = raw.get("vol_ratio", 1.0)
        obv_bull  = raw.get("obv_bullish", None)
        stoch_k   = raw.get("stoch_k", 50)

        if vol_ratio and vol_ratio >= 1.5:
            details.append(f"Volume is {vol_ratio:.1f}x average, confirming the move")
        elif obv_bull is True:
            details.append("OBV trend is rising, indicating accumulation")

        if stoch_k and stoch_k < 25:
            details.append("Stochastic is in oversold territory and turning up")
        elif stoch_k and stoch_k > 75:
            details.append("Stochastic is in overbought territory — exit risk elevated")

        if sentiment == "positive" and sent_score > 0.3:
            details.append("recent news sentiment is positive")
        elif sentiment == "negative" and sent_score < -0.3:
            details.append("recent news sentiment is negative — adds bearish weight")

        if not details:
            return ""

        return " ".join([details[0].capitalize() + "."]) if len(details) == 1 \
            else f"{details[0].capitalize()}, and {details[1]}."

    def _risk_reward_sentence(self, signal) -> str:
        entry = signal.entry_price
        sl    = signal.stop_loss
        tp    = signal.take_profit
        size  = signal.position_size

        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)
        rr = round(tp_dist / sl_dist, 1) if sl_dist > 0 else 0

        entry_str = f"{entry:,.0f}"
        sl_str    = f"{sl:,.0f}"
        tp_str    = f"{tp:,.0f}"

        if signal.action == "BUY":
            return (
                f"Entry near {entry_str} with stop at {sl_str} "
                f"targets {tp_str} — a {rr}R reward ({size} shares)."
            )
        else:
            return (
                f"Short entry near {entry_str}, cover at {tp_str} "
                f"with stop at {sl_str} — {rr}R setup ({size} shares)."
            )

    # ------------------------------------------------------------------
    # LLM polish (optional)
    # ------------------------------------------------------------------

    def _llm_polish(self, signal, draft: str) -> str:
        """Ask LLM to rewrite the draft more naturally. Returns empty string on failure."""
        prompt = f"""You are a professional stock market analyst writing for retail traders.
Rewrite this trade summary in 2-3 clear, engaging sentences. Keep all numbers exactly as-is.
Be specific and confident — avoid vague language like "might" or "could".

Draft: {draft}

Stock: {signal.symbol}
Action: {signal.action}
Confidence: {signal.confidence:.0%}

Return ONLY the rewritten summary, no preamble."""

        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": SENTIMENT_MODEL, "prompt": prompt,
                  "stream": False, "options": {"temperature": 0.3, "num_predict": 150}},
            timeout=20
        )
        text = response.json().get("response", "").strip()
        # Sanity check — must mention the symbol and be reasonable length
        if signal.symbol in text and 50 < len(text) < 600:
            return text
        return ""

    def _check_ollama(self) -> bool:
        try:
            r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False
