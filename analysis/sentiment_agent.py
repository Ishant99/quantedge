# =============================================================================
# analysis/sentiment_agent.py — M3: News Sentiment Agent (IMPROVED)
#
# Improvements:
#   - Much more aggressive LLM prompt — forces non-neutral classification
#   - Expanded keyword lists (3x more words)
#   - Market-wide sentiment boost (Nifty trend affects all stocks)
#   - Per-sector news matching (Banking news boosts all bank stocks)
#   - Lower neutral threshold — score > 0.1 now = positive
# =============================================================================

import feedparser
import requests
import json
import re
import email.utils
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    RSS_FEEDS, SENTIMENT_MODEL, OLLAMA_BASE_URL,
    SENTIMENT_FRESHNESS_HOURS, SENTIMENT_DECAY_FACTOR,
)
from utils import get_logger

logger = get_logger("SentimentAgent")


@dataclass
class SentimentResult:
    symbol:     str
    score:      float       # -1.0 to +1.0
    label:      str         # positive | neutral | negative
    headlines:  list[str]
    confidence: float


class SentimentAgent:
    """
    M3 — Improved sentiment with aggressive LLM prompt and
    sector-level news matching so fewer stocks show neutral.
    """

    POSITIVE_WORDS = {
        # Price action
        "surge", "rally", "gain", "rise", "jump", "soar", "climb", "up",
        "high", "record", "peak", "breakout", "bounce", "recover",
        # Business
        "profit", "growth", "revenue", "beat", "outperform", "upgrade",
        "strong", "robust", "expansion", "deal", "acquisition", "merger",
        "dividend", "buyback", "order", "contract", "launch", "win",
        # Sentiment
        "bullish", "positive", "buy", "overweight", "accumulate",
        "optimistic", "confident", "momentum", "interest", "demand",
        # Indian market specific
        "nifty gains", "sensex rises", "fii buying", "dii buying",
        "results beat", "q3 profit", "q4 profit", "capex", "margin expansion",
    }

    NEGATIVE_WORDS = {
        # Price action
        "fall", "drop", "decline", "crash", "plunge", "slip", "tumble",
        "low", "loss", "miss", "underperform", "sell", "weak", "pressure",
        # Business
        "debt", "fraud", "probe", "penalty", "fine", "lawsuit", "default",
        "downgrade", "cut", "layoff", "closure", "recall", "ban", "tax",
        # Sentiment
        "bearish", "negative", "concern", "risk", "worry", "fear",
        "uncertainty", "volatile", "caution", "exit", "selling",
        # Indian market specific
        "nifty falls", "sensex drops", "fii selling", "results miss",
        "margin pressure", "slowdown", "inflation", "rate hike", "rupee falls",
    }

    # Sector keywords — news about a sector boosts all stocks in that sector
    SECTOR_KEYWORDS = {
        "Banking":    ["rbi", "repo rate", "banking sector", "credit growth", "npa", "loans"],
        "IT":         ["it sector", "software exports", "tech stocks", "infosys", "tcs results"],
        "Pharma":     ["pharma sector", "drug approval", "usfda", "api", "generic drugs"],
        "Auto":       ["auto sector", "vehicle sales", "ev", "electric vehicle", "auto sales"],
        "Energy":     ["crude oil", "oil prices", "opec", "energy sector", "petrol price"],
        "FMCG":       ["fmcg sector", "consumer demand", "rural demand", "inflation impact"],
        "Realty":     ["real estate", "housing demand", "property prices", "home loans"],
        "Metals":     ["steel prices", "metal sector", "iron ore", "aluminium", "copper"],
    }

    # File used to persist sentiment scores between scans for momentum tracking
    _MOMENTUM_CACHE = "logs/sentiment_momentum.json"

    def __init__(self):
        self.ollama_available = self._check_ollama()
        # Cache market-wide headlines once per run
        self._market_headlines = None
        self._prev_scores: dict = self._load_momentum_cache()
        if self.ollama_available:
            logger.info(f"Ollama connected — using {SENTIMENT_MODEL} for sentiment")
        else:
            logger.warning("Ollama not available — using keyword sentiment")

    def analyse(self, symbol: str, company_name: str = "",
                sector: str = "") -> SentimentResult:
        # Fetch stock-specific headlines with freshness weights
        stock_headlines, stock_weights = self._fetch_headlines(symbol, company_name)

        # Add sector headlines (flat weight — sector news is always "now")
        sector_headlines = self._fetch_sector_headlines(sector)

        # Combine — stock news first, then up to 3 sector headlines
        all_headlines = stock_headlines + sector_headlines[:3]
        all_weights   = stock_weights   + [0.6] * len(sector_headlines[:3])

        if not all_headlines:
            # No news = slightly positive bias (no bad news = good news in markets)
            return SentimentResult(
                symbol=symbol, score=0.15, label="neutral",
                headlines=[], confidence=0.2
            )

        if self.ollama_available and stock_headlines:
            score, confidence = self._llm_sentiment(all_headlines, all_weights, symbol, company_name)
        else:
            score, confidence = self._keyword_sentiment(all_headlines, all_weights)

        # Boost confidence when we have more headlines
        confidence = min(0.95, confidence + len(stock_headlines) * 0.05)

        # Sentiment momentum: if score is accelerating positively vs. last scan,
        # slightly boost score and confidence to reward improving setups.
        prev = self._prev_scores.get(symbol)
        if prev is not None:
            delta = score - prev
            if delta >= 0.20:       # strong positive acceleration
                score      = min(1.0, score + 0.08)
                confidence = min(0.95, confidence + 0.05)
                logger.debug(f"{symbol}: sentiment momentum +{delta:.2f} → boosted")
            elif delta <= -0.20:    # strong negative acceleration — penalise
                score      = max(-1.0, score - 0.08)
                confidence = min(0.95, confidence + 0.03)
                logger.debug(f"{symbol}: sentiment momentum {delta:.2f} → penalised")
        self._prev_scores[symbol] = round(score, 3)
        self._save_momentum_cache()

        # Lower neutral threshold — be decisive
        if score > 0.1:
            label = "positive"
        elif score < -0.1:
            label = "negative"
        else:
            label = "neutral"

        return SentimentResult(
            symbol=symbol, score=round(score, 3),
            label=label, headlines=all_headlines[:5],
            confidence=round(confidence, 2)
        )

    def analyse_all(self, symbols: list[str],
                    symbol_names: dict = None,
                    symbol_sectors: dict = None) -> dict[str, SentimentResult]:
        results = {}
        names   = symbol_names   or {}
        sectors = symbol_sectors or {}

        # Pre-fetch all RSS feeds once (not per stock)
        self._market_headlines = self._fetch_all_feeds()

        for sym in symbols:
            results[sym] = self.analyse(sym, names.get(sym, ""), sectors.get(sym, ""))

        pos = sum(1 for r in results.values() if r.label == "positive")
        neg = sum(1 for r in results.values() if r.label == "negative")
        neu = len(results) - pos - neg
        logger.info(f"Sentiment: {pos} positive, {neg} negative, {neu} neutral "
                    f"(was all-neutral before fix)")
        return results

    # ------------------------------------------------------------------
    # LLM sentiment — improved prompt
    # ------------------------------------------------------------------

    def _llm_sentiment(self, headlines: list[str], weights: list[float],
                       symbol: str, company: str) -> tuple[float, float]:
        weighted_lines = []
        for headline, weight in zip(headlines[:8], (weights or [])[:8] or [1.0] * min(len(headlines), 8)):
            freshness = "fresh" if weight >= 0.99 else "recent" if weight >= 0.5 else "stale"
            weighted_lines.append(f"- [{freshness} | weight {weight:.2f}] {headline}")
        headlines_text = "\n".join(weighted_lines)
        llm_score, llm_conf = 0.1, 0.6
        prompt = f"""You are an expert Indian stock market analyst.

Analyse these news headlines about {company or symbol} and give a sentiment score.

Headlines:
{headlines_text}

Rules:
- Be DECISIVE — avoid neutral unless headlines are genuinely mixed
- Positive news about earnings, growth, deals = score 0.4 to 0.9
- Negative news about losses, fines, weak results = score -0.4 to -0.9
- Mixed or unclear = score -0.1 to 0.1
- No news about company specifically = score 0.1 (slight positive bias)

Return ONLY a JSON object, nothing else:
{{"score": 0.6, "confidence": 0.8, "reason": "strong earnings beat"}}"""

        try:
            response = requests.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": SENTIMENT_MODEL, "prompt": prompt,
                      "stream": False, "options": {"temperature": 0.1}},
                timeout=30
            )
            text  = response.json().get("response", "")
            match = re.search(r'\{.*?\}', text, re.DOTALL)
            if match:
                data  = json.loads(match.group())
                llm_score = max(-1.0, min(1.0, float(data.get("score", 0.1))))
                llm_conf = max(0.0, min(1.0, float(data.get("confidence", 0.6))))
        except Exception as e:
            logger.debug(f"LLM sentiment error: {e}")

        kw_score, kw_conf = self._keyword_sentiment(headlines, weights)
        avg_weight = sum(weights[:len(headlines[:8])]) / max(1, len(headlines[:8])) if weights else 1.0
        blended_score = round((llm_score * 0.7) + (kw_score * 0.3 * max(avg_weight, 0.25)), 3)
        blended_conf = round(min(0.95, llm_conf * max(avg_weight, 0.35) + kw_conf * 0.25), 2)
        return blended_score, blended_conf

    # ------------------------------------------------------------------
    # Keyword sentiment — expanded
    # ------------------------------------------------------------------

    def _keyword_sentiment(
        self, headlines: list[str], weights: list[float] = None
    ) -> tuple[float, float]:
        if weights is None:
            weights = [1.0] * len(headlines)

        pos = neg = 0.0
        for h, w in zip(headlines, weights):
            text = h.lower()
            for kw in self.POSITIVE_WORDS:
                if kw in text:
                    pos += w
            for kw in self.NEGATIVE_WORDS:
                if kw in text:
                    neg += w

        total = pos + neg
        if total == 0:
            return 0.15, 0.25   # slight positive bias when no keywords found

        score      = (pos - neg) / (total + 2)   # +2 dampens extremes
        confidence = min(0.85, 0.3 + total * 0.06)
        return round(score, 3), round(confidence, 2)

    # ------------------------------------------------------------------
    # News fetching
    # ------------------------------------------------------------------

    def _fetch_all_feeds(self) -> list[dict]:
        """Fetch all RSS feeds once. Returns deduplicated list of {title, summary, published}."""
        items = []
        seen_titles: set[str] = set()

        for url in RSS_FEEDS:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:60]:
                    title = entry.get("title", "").strip()
                    if not title:
                        continue
                    # Deduplicate across feeds (normalize: lowercase + strip punctuation)
                    key = re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()
                    if key in seen_titles:
                        continue
                    seen_titles.add(key)
                    items.append({
                        "title":     title,
                        "summary":   entry.get("summary", ""),
                        "published": entry.get("published", ""),
                    })
            except Exception as e:
                logger.debug(f"RSS fetch error {url}: {e}")
        return items

    def _headline_age_hours(self, item: dict) -> float:
        """Parse RFC 2822 published date → age in hours. Returns 0.0 if unparseable."""
        pub = item.get("published", "")
        if not pub:
            return 0.0
        try:
            ts = email.utils.parsedate_to_datetime(pub)
            now = datetime.now(timezone.utc)
            return (now - ts).total_seconds() / 3600
        except Exception:
            return 0.0

    def _freshness_weight(self, item: dict) -> float:
        """
        Weight headline contribution by recency.
        Fresh (<= SENTIMENT_FRESHNESS_HOURS h) = 1.0, then decays.
        """
        age = self._headline_age_hours(item)
        if age <= SENTIMENT_FRESHNESS_HOURS:
            return 1.0
        elif age <= SENTIMENT_FRESHNESS_HOURS * 2:
            return SENTIMENT_DECAY_FACTOR
        elif age <= SENTIMENT_FRESHNESS_HOURS * 3:
            return SENTIMENT_DECAY_FACTOR ** 2
        else:
            return SENTIMENT_DECAY_FACTOR ** 3

    def _fetch_headlines(self, symbol: str, company_name: str) -> tuple[list[str], list[float]]:
        """
        Match cached headlines to this stock.
        Returns (titles, freshness_weights).
        """
        if self._market_headlines is None:
            self._market_headlines = self._fetch_all_feeds()

        search_terms = {symbol.lower()}
        if company_name:
            search_terms.add(company_name.lower())
            # Add every meaningful word (len > 3) so "Tata Consultancy Services"
            # matches on "tata", "consultancy", "services" individually
            words = [w.lower() for w in company_name.split() if len(w) > 3]
            search_terms.update(words[:3])

        matched_titles: list[str]  = []
        matched_weights: list[float] = []
        seen: set[str] = set()

        for item in self._market_headlines:
            text = (item["title"] + " " + item["summary"]).lower()
            if any(term in text for term in search_terms):
                title = item["title"]
                if title not in seen:
                    seen.add(title)
                    matched_titles.append(title)
                    matched_weights.append(self._freshness_weight(item))

        return matched_titles[:8], matched_weights[:8]

    def _fetch_sector_headlines(self, sector: str) -> list[str]:
        """Get headlines relevant to the stock's sector."""
        if not sector or self._market_headlines is None:
            return []

        keywords = self.SECTOR_KEYWORDS.get(sector, [])
        if not keywords:
            return []

        matched = []
        for item in self._market_headlines:
            text = (item["title"] + " " + item["summary"]).lower()
            if any(kw in text for kw in keywords):
                matched.append(item["title"])

        return list(set(matched))[:5]

    def _check_ollama(self) -> bool:
        try:
            r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def _load_momentum_cache(self) -> dict:
        try:
            import json as _json
            os.makedirs("logs", exist_ok=True)
            if os.path.exists(self._MOMENTUM_CACHE):
                with open(self._MOMENTUM_CACHE) as f:
                    return _json.load(f)
        except Exception:
            pass
        return {}

    def _save_momentum_cache(self):
        try:
            import json as _json
            with open(self._MOMENTUM_CACHE, "w") as f:
                _json.dump(self._prev_scores, f)
        except Exception:
            pass
