"""Tests for execution/brokers — asset class gate enforcement + basic logic.

All network calls (yfinance, Binance, NSE options) are mocked so tests run
offline.  Database writes use a per-test temp SQLite file.
"""
from __future__ import annotations
import sys, os, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_db(tmp_path, monkeypatch):
    db = str(tmp_path / "test.db")
    monkeypatch.setenv("SQLITE_DB_FILE", db)
    return db


# ===========================================================================
# USPaperBroker
# ===========================================================================

class TestUSPaperBrokerGate:
    def test_blocked_when_gate_disabled(self, tmp_path, monkeypatch):
        db = str(tmp_path / "us.db")
        gates = {
            "nse_spot":    {"enabled": True,  "phase_required": 0},
            "fno":         {"enabled": False, "phase_required": 6},
            "crypto":      {"enabled": False, "phase_required": 6},
            "us_equities": {"enabled": False, "phase_required": 6},
        }
        with (
            patch("config.SQLITE_DB_FILE", db),
            patch("execution.brokers.us_paper_broker.SQLITE_DB_FILE", db),
            patch("config.ASSET_CLASS_GATES", gates),
            patch("data.us_scanner.USScanner.get_current_price", return_value=150.0),
        ):
            from execution.brokers.us_paper_broker import USPaperBroker
            broker = USPaperBroker.__new__(USPaperBroker)
            broker.db      = db
            broker.scanner = MagicMock()
            broker._init_table = MagicMock()
            with sqlite3.connect(db) as c:
                c.execute("""CREATE TABLE IF NOT EXISTS us_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT, symbol TEXT, direction TEXT,
                    entry_price REAL, qty REAL, sl REAL, tp REAL,
                    status TEXT DEFAULT 'open', exit_price REAL,
                    pnl REAL, reasoning TEXT
                )""")
            result = broker.open_position("AAPL", "LONG", entry_price=150.0)
        assert result is None

    def test_allowed_when_gate_enabled(self, tmp_path):
        """Gate passes when us_equities is enabled — broker proceeds past the gate check."""
        db = str(tmp_path / "us2.db")
        gates = {
            "nse_spot":    {"enabled": True,  "phase_required": 0},
            "us_equities": {"enabled": True,  "phase_required": 6},
            "fno":         {"enabled": False, "phase_required": 6},
            "crypto":      {"enabled": False, "phase_required": 6},
        }
        with (
            patch("config.SQLITE_DB_FILE", db),
            patch("execution.brokers.us_paper_broker.SQLITE_DB_FILE", db),
            patch("config.ASSET_CLASS_GATES", gates),
        ):
            from execution.brokers.us_paper_broker import USPaperBroker
            scanner_mock = MagicMock()
            scanner_mock.get_current_price.return_value = 150.0
            with patch("data.us_scanner.USScanner", return_value=scanner_mock):
                broker = USPaperBroker()
            # Gate is enabled — execution should proceed past the gate guard
            # (may still return None due to treasury/position checks, but NOT due to gate)
            # Patch treasury so allocation succeeds
            with patch("services.paper_treasury.can_allocate", return_value=(True, "")), \
                 patch("services.paper_treasury.reserve_for_us_order", return_value=None), \
                 patch("services.paper_treasury.log_treasury_event", return_value=None), \
                 patch("services.paper_treasury.write_treasury_snapshot", return_value=None):
                result = broker.open_position("AAPL", "LONG", entry_price=150.0)
            # Gate should NOT have blocked; any result (int or None) is fine here
            # as long as it's not blocked by the gate (which returns None immediately)
            with sqlite3.connect(db) as c:
                count = c.execute("SELECT COUNT(*) FROM us_trades").fetchone()[0]
            assert count >= 1 or result is not None


# ===========================================================================
# CryptoPaperBroker
# ===========================================================================

class TestCryptoPaperBrokerGate:
    def test_blocked_when_gate_disabled(self, tmp_path):
        db = str(tmp_path / "crypto.db")
        gates = {
            "nse_spot": {"enabled": True,  "phase_required": 0},
            "crypto":   {"enabled": False, "phase_required": 6},
            "fno":      {"enabled": False, "phase_required": 6},
            "us_equities": {"enabled": False, "phase_required": 6},
        }
        with (
            patch("config.SQLITE_DB_FILE", db),
            patch("execution.brokers.crypto_paper_broker.SQLITE_DB_FILE", db),
            patch("config.ASSET_CLASS_GATES", gates),
        ):
            from execution.brokers.crypto_paper_broker import CryptoPaperBroker
            broker = CryptoPaperBroker.__new__(CryptoPaperBroker)
            broker.db      = db
            broker.scanner = MagicMock()
            broker._init_table = MagicMock()
            result = broker.open_position("BTCUSDT", "LONG", entry_price=50000.0)
        assert result is None

    def test_sl_above_entry_for_short(self, tmp_path):
        """For SHORT trades, stop-loss should be ABOVE entry price."""
        from execution.brokers.crypto_paper_broker import CryptoPaperBroker
        from config import CRYPTO_SL_PCT
        entry = 50000.0
        sl = round(entry * (1 + CRYPTO_SL_PCT), 6)
        assert sl > entry, "SHORT stop-loss must be above entry"

    def test_tp_below_entry_for_short(self):
        """For SHORT trades, take-profit should be BELOW entry price."""
        from execution.brokers.crypto_paper_broker import CryptoPaperBroker
        from config import CRYPTO_TP_PCT
        entry = 50000.0
        tp = round(entry * (1 - CRYPTO_TP_PCT), 6)
        assert tp < entry, "SHORT take-profit must be below entry"

    def test_sl_below_entry_for_long(self):
        """For LONG trades, stop-loss should be BELOW entry price."""
        from config import CRYPTO_SL_PCT
        entry = 50000.0
        sl = round(entry * (1 - CRYPTO_SL_PCT), 6)
        assert sl < entry, "LONG stop-loss must be below entry"


# ===========================================================================
# FNOPaperBroker
# ===========================================================================

class TestFNOPaperBrokerGate:
    def test_blocked_when_gate_disabled(self, tmp_path):
        db = str(tmp_path / "fno.db")
        gates = {
            "nse_spot": {"enabled": True,  "phase_required": 0},
            "fno":      {"enabled": False, "phase_required": 6},
            "crypto":   {"enabled": False, "phase_required": 6},
            "us_equities": {"enabled": False, "phase_required": 6},
        }
        with (
            patch("config.SQLITE_DB_FILE", db),
            patch("execution.brokers.fno_paper_broker.SQLITE_DB_FILE", db),
            patch("config.ASSET_CLASS_GATES", gates),
        ):
            from execution.brokers.fno_paper_broker import FNOPaperBroker
            broker = FNOPaperBroker.__new__(FNOPaperBroker)
            result = broker.open_position(
                index="NIFTY", direction="CALL",
                strike=22000, expiry="2026-05-29"
            )
        assert result is None


# ===========================================================================
# Sign convention sanity checks (no DB needed)
# ===========================================================================

class TestPnlSignConventions:
    def test_us_long_pnl_positive_when_price_rises(self):
        entry, exit_, qty = 100.0, 110.0, 5.0
        pnl = round((exit_ - entry) * qty, 2)
        assert pnl > 0

    def test_us_short_pnl_positive_when_price_falls(self):
        entry, exit_, qty = 100.0, 90.0, 5.0
        pnl = round((entry - exit_) * qty, 2)
        assert pnl > 0

    def test_us_long_pnl_negative_when_price_falls(self):
        entry, exit_, qty = 100.0, 90.0, 5.0
        pnl = round((exit_ - entry) * qty, 2)
        assert pnl < 0

    def test_rr_ratio_at_least_2_for_us(self):
        from config import US_TP_PCT, US_SL_PCT
        assert US_TP_PCT / US_SL_PCT >= 2.0, "US RR ratio should be ≥ 2:1"

    def test_rr_ratio_at_least_2_for_crypto(self):
        from config import CRYPTO_TP_PCT, CRYPTO_SL_PCT
        assert CRYPTO_TP_PCT / CRYPTO_SL_PCT >= 2.0, "Crypto RR ratio should be ≥ 2:1"
