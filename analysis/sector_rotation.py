# =============================================================================
# analysis/sector_rotation.py — Sector Rotation Detection
#
# Detects which sectors are outperforming this week.
# Boosts signals for stocks in hot sectors, reduces for cold ones.
# =============================================================================

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from dataclasses import dataclass
import yfinance as yf
from utils import get_logger

logger = get_logger("SectorRotation")

# NSE Sector ETF tickers on yfinance
SECTOR_ETFS = {
    "Banking":    "^NSEBANK",
    "IT":         "^CNXIT",
    "Pharma":     "^CNXPHARMA",
    "Auto":       "^CNXAUTO",
    "FMCG":       "^CNXFMCG",
    "Energy":     "^CNXENERGY",
    "Realty":     "^CNXREALTY",
    "Metals":     "^CNXMETAL",
    "Infra":      "^CNXINFRA",
    "MidCap":     "^NSEMDCP50",
}


@dataclass
class SectorRotationResult:
    hot_sectors:     list[str]    # outperforming sectors
    cold_sectors:    list[str]    # underperforming sectors
    sector_returns:  dict         # sector -> 1-week return %
    rotation_signal: str          # risk_on | risk_off | mixed
    message:         str


class SectorRotationAnalyser:
    """
    Identifies sector momentum to ride the strongest sectors.
    """

    def analyse(self) -> SectorRotationResult:
        """Fetch sector ETF returns and rank sectors."""
        returns = {}
        for sector, ticker in SECTOR_ETFS.items():
            try:
                hist = yf.Ticker(ticker).history(period="1mo", interval="1d")
                if not hist.empty and len(hist) >= 5:
                    ret_1w = float(
                        (hist["Close"].iloc[-1] / hist["Close"].iloc[-5] - 1) * 100
                    )
                    returns[sector] = round(ret_1w, 2)
            except Exception:
                pass

        if not returns:
            return self._default()

        # Rank sectors
        sorted_sectors = sorted(returns.items(), key=lambda x: x[1], reverse=True)
        hot_sectors    = [s for s, r in sorted_sectors if r > 1.0][:3]
        cold_sectors   = [s for s, r in sorted_sectors if r < -1.0][:3]

        # Risk-on: Banking + IT leading = good
        # Risk-off: Pharma + FMCG leading = defensive
        top3 = [s for s, _ in sorted_sectors[:3]]
        risk_on_sectors  = {"Banking", "IT", "Auto", "Metals"}
        risk_off_sectors = {"Pharma", "FMCG", "Energy"}

        risk_on_count  = sum(1 for s in top3 if s in risk_on_sectors)
        risk_off_count = sum(1 for s in top3 if s in risk_off_sectors)

        if risk_on_count >= 2:
            rotation_signal = "risk_on"
            msg = f"Risk-on rotation — {', '.join(hot_sectors)} leading"
        elif risk_off_count >= 2:
            rotation_signal = "risk_off"
            msg = f"Defensive rotation — {', '.join(hot_sectors)} leading"
        else:
            rotation_signal = "mixed"
            msg = "Mixed sector rotation"

        logger.info(f"Sector rotation: {rotation_signal} | "
                    f"Hot: {hot_sectors} | Cold: {cold_sectors}")

        return SectorRotationResult(
            hot_sectors     = hot_sectors,
            cold_sectors    = cold_sectors,
            sector_returns  = returns,
            rotation_signal = rotation_signal,
            message         = msg,
        )

    def get_sector_multiplier(self, sector: str,
                               result: SectorRotationResult) -> float:
        """
        Return position size multiplier based on sector momentum.
        Hot sector = 1.2x, cold sector = 0.7x, neutral = 1.0x
        """
        if sector in result.hot_sectors:
            return 1.2
        elif sector in result.cold_sectors:
            return 0.7
        return 1.0

    def _default(self) -> SectorRotationResult:
        return SectorRotationResult(
            hot_sectors=[], cold_sectors={},
            sector_returns={}, rotation_signal="mixed",
            message="Sector data unavailable — neutral weighting"
        )
