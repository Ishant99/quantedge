# =============================================================================
# risk/correlation_filter.py — Position Correlation Filter
#
# Prevents buying highly correlated stocks together.
# E.g. HDFCBANK + ICICIBANK + AXISBANK all move together — that's not
# diversification, it's triple exposure to the same risk.
#
# Rules:
#   - If two stocks have correlation > 0.75, only take the stronger signal
#   - Maximum 2 stocks from same sector at once
#   - Blocks redundant positions that only add risk, not diversification
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from utils import get_logger

logger = get_logger("CorrelationFilter")

MAX_CORRELATION      = 0.75   # block if correlation > 75%
MAX_SAME_SECTOR      = 2      # max 2 stocks from same sector


class CorrelationFilter:
    """
    Filters out correlated signals to ensure true diversification.
    Works on the final list of BUY signals before execution.
    """

    def filter(
        self,
        signals:     list,
        market_data: dict,
        symbol_sectors: dict = None,
    ) -> list:
        """
        Filter signals to remove highly correlated pairs.
        Keeps the higher-confidence signal when two are correlated.

        Args:
            signals:        List of TradeSignal objects (BUY only)
            market_data:    Dict of symbol -> DataFrame
            symbol_sectors: Dict of symbol -> sector name

        Returns:
            Filtered list of non-correlated signals
        """
        if len(signals) <= 1:
            return signals

        sectors  = symbol_sectors or {}
        symbols  = [s.symbol for s in signals]

        # Build correlation matrix from recent returns
        corr_matrix = self._build_correlation(symbols, market_data)

        # Filter by correlation
        kept     = []
        blocked  = set()

        for i, sig in enumerate(signals):
            if sig.symbol in blocked:
                continue

            # Check correlation with already-kept signals
            should_block = False
            for kept_sig in kept:
                corr = self._get_correlation(
                    sig.symbol, kept_sig.symbol, corr_matrix
                )
                if corr > MAX_CORRELATION:
                    # Keep the higher confidence one
                    if sig.confidence > kept_sig.confidence:
                        # Remove kept_sig, add sig
                        kept = [k for k in kept if k.symbol != kept_sig.symbol]
                        blocked.add(kept_sig.symbol)
                        logger.info(
                            f"Correlation filter: {kept_sig.symbol} removed, "
                            f"{sig.symbol} kept "
                            f"(corr: {corr:.2f}, conf: {sig.confidence:.0%} > "
                            f"{kept_sig.confidence:.0%})"
                        )
                    else:
                        should_block = True
                        logger.info(
                            f"Correlation filter: {sig.symbol} blocked "
                            f"(corr {corr:.2f} with {kept_sig.symbol})"
                        )
                    break

            if not should_block:
                kept.append(sig)

        # Filter by sector concentration
        kept = self._filter_sector_concentration(kept, sectors)

        removed = len(signals) - len(kept)
        if removed > 0:
            logger.info(f"Correlation filter removed {removed} signals, "
                        f"{len(kept)} remain")
        return kept

    def get_correlation_report(
        self, symbols: list, market_data: dict
    ) -> pd.DataFrame:
        """Return full correlation matrix as DataFrame — useful for dashboard."""
        matrix = self._build_correlation(symbols, market_data)
        if matrix is None:
            return pd.DataFrame()
        return pd.DataFrame(matrix, index=symbols, columns=symbols).round(2)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_correlation(
        self, symbols: list, market_data: dict
    ) -> dict | None:
        """Build pairwise correlation dict from last 60 days of returns."""
        try:
            returns = {}
            for sym in symbols:
                if sym in market_data:
                    ret = market_data[sym]["close"].pct_change().dropna().tail(60)
                    if len(ret) > 20:
                        returns[sym] = ret.values

            if len(returns) < 2:
                return None

            # Build matrix
            matrix = {}
            syms   = list(returns.keys())
            for i, s1 in enumerate(syms):
                matrix[s1] = {}
                for j, s2 in enumerate(syms):
                    if s1 == s2:
                        matrix[s1][s2] = 1.0
                    else:
                        r1 = returns[s1]
                        r2 = returns[s2]
                        min_len = min(len(r1), len(r2))
                        if min_len > 20:
                            corr = float(np.corrcoef(
                                r1[-min_len:], r2[-min_len:]
                            )[0, 1])
                            matrix[s1][s2] = round(corr, 3)
                        else:
                            matrix[s1][s2] = 0.0
            return matrix

        except Exception as e:
            logger.debug(f"Correlation build failed: {e}")
            return None

    def _get_correlation(
        self, sym1: str, sym2: str, matrix: dict | None
    ) -> float:
        """Safe correlation lookup."""
        if matrix is None:
            return 0.0
        return matrix.get(sym1, {}).get(sym2, 0.0)

    def _filter_sector_concentration(
        self, signals: list, sectors: dict
    ) -> list:
        """Max MAX_SAME_SECTOR stocks from same sector."""
        sector_count = {}
        kept         = []

        for sig in signals:
            sector = sectors.get(sig.symbol, "Unknown")
            count  = sector_count.get(sector, 0)

            if count < MAX_SAME_SECTOR:
                kept.append(sig)
                sector_count[sector] = count + 1
            else:
                logger.info(
                    f"Sector filter: {sig.symbol} blocked "
                    f"({sector} already has {count} positions)"
                )

        return kept
