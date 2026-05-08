# =============================================================================
# analysis/btc_dominance.py — BTC Dominance Filter for Crypto Signals
#
# BTC Dominance (BTC.D) = BTC market cap / total crypto market cap × 100
#
# Interpretation:
#   BTC.D rising    → capital flowing INTO Bitcoin, OUT of altcoins
#                     → reduce/skip altcoin longs; BTC-only mode
#   BTC.D falling   → capital rotating to altcoins
#                     → allow altcoin longs; full crypto scan
#   BTC.D > 55%     → Bitcoin dominance phase — only BTCUSDT/ETHUSDT
#   BTC.D 45–55%    → Balanced — standard scan (all pairs)
#   BTC.D < 45%     → Altcoin season — prioritise altcoins
#
# Data source: CoinGecko public API (free, no key required)
# Fallback: allow all signals if API unavailable
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from dataclasses import dataclass
from datetime import datetime
from utils import get_logger

logger = get_logger("BTCDominance")

COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
REQUEST_TIMEOUT  = 8   # seconds

# Dominance thresholds
DOMINANCE_HIGH   = 55.0   # BTC-only mode above this
DOMINANCE_LOW    = 45.0   # altcoin-season below this

# Blue-chip pairs always allowed regardless of dominance
BLUE_CHIPS = {"BTCUSDT", "ETHUSDT"}


@dataclass
class DominanceResult:
    btc_dominance:  float    # BTC.D percentage
    eth_dominance:  float    # ETH.D percentage
    phase:          str      # "btc_season" | "balanced" | "alt_season"
    trend:          str      # "rising" | "falling" | "flat" (inferred from pct change)
    blue_chip_only: bool     # True when BTC.D > DOMINANCE_HIGH
    allow_alts:     bool     # True when BTC.D < DOMINANCE_HIGH
    total_mcap_usd: float    # total crypto market cap in USD
    timestamp:      str


class BTCDominanceFilter:
    """
    Fetches BTC dominance from CoinGecko and determines which crypto
    pairs should be traded.
    """

    def get_dominance(self) -> DominanceResult:
        """
        Fetch current BTC dominance from CoinGecko global endpoint.
        Returns a DominanceResult with phase classification.
        Falls back to a neutral (allow all) result on any error.
        """
        try:
            resp = requests.get(COINGECKO_GLOBAL, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                logger.warning(f"CoinGecko returned HTTP {resp.status_code} — allowing all signals")
                return self._fallback()

            data         = resp.json().get("data", {})
            mcp          = data.get("market_cap_percentage", {})
            btc_dom      = float(mcp.get("btc", 50.0))
            eth_dom      = float(mcp.get("eth", 15.0))
            total_mcap   = float(data.get("total_market_cap", {}).get("usd", 0))
            mcap_change  = float(data.get("market_cap_change_percentage_24h_usd", 0))

            # Phase classification
            if btc_dom > DOMINANCE_HIGH:
                phase = "btc_season"
            elif btc_dom < DOMINANCE_LOW:
                phase = "alt_season"
            else:
                phase = "balanced"

            # Trend from 24h market cap change as proxy
            # (rising total mcap with falling BTC.D → alts gaining; falling mcap → defensive)
            if mcap_change > 2.0:
                trend = "falling"   # market expanding → alts gaining share
            elif mcap_change < -2.0:
                trend = "rising"    # market contracting → BTC gaining share
            else:
                trend = "flat"

            blue_chip_only = btc_dom > DOMINANCE_HIGH
            allow_alts     = not blue_chip_only

            result = DominanceResult(
                btc_dominance  = round(btc_dom,   2),
                eth_dominance  = round(eth_dom,   2),
                phase          = phase,
                trend          = trend,
                blue_chip_only = blue_chip_only,
                allow_alts     = allow_alts,
                total_mcap_usd = total_mcap,
                timestamp      = datetime.utcnow().isoformat(),
            )
            logger.info(
                f"BTC Dominance: {btc_dom:.1f}% ({phase}) | "
                f"Trend: {trend} | Alt-season: {allow_alts}"
            )
            return result

        except Exception as e:
            logger.warning(f"BTC dominance fetch failed: {e} — allowing all signals")
            return self._fallback()

    def filter_symbols(
        self,
        symbols: list[str],
        dominance: DominanceResult | None = None,
    ) -> list[str]:
        """
        Filter a list of crypto symbols based on current BTC dominance.
        Always keeps BTCUSDT and ETHUSDT (blue chips).
        Returns only the symbols that should be traded right now.
        """
        if dominance is None:
            dominance = self.get_dominance()

        if dominance.blue_chip_only:
            allowed = [s for s in symbols if s in BLUE_CHIPS]
            logger.info(
                f"BTC dominance {dominance.btc_dominance:.1f}% > {DOMINANCE_HIGH}% — "
                f"blue-chip-only mode: {len(allowed)}/{len(symbols)} symbols kept"
            )
            return allowed

        # Alt season or balanced — keep everything
        return symbols

    def _fallback(self) -> DominanceResult:
        """Neutral result — allow all signals when data unavailable."""
        return DominanceResult(
            btc_dominance=50.0, eth_dominance=15.0,
            phase="balanced", trend="flat",
            blue_chip_only=False, allow_alts=True,
            total_mcap_usd=0.0,
            timestamp=datetime.utcnow().isoformat(),
        )
