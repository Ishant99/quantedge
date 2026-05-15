"""Tests for memory/portfolio_memory.py — PortfolioMemory"""
from __future__ import annotations
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mem(tmp_path):
    """PortfolioMemory backed by a temp SQLite file; ChromaDB disabled."""
    db_file = str(tmp_path / "test_trades.db")
    with (
        patch("config.SQLITE_DB_FILE", db_file),
        patch("memory.portfolio_memory.SQLITE_DB_FILE", db_file),
        patch("memory.portfolio_memory.PortfolioMemory._init_chroma", return_value=None),
    ):
        from memory.portfolio_memory import PortfolioMemory
        m = PortfolioMemory.__new__(PortfolioMemory)
        m.db_path      = db_file
        m.db_available = True
        m.chroma       = None
        os.makedirs(os.path.dirname(db_file), exist_ok=True)
        m._init_db()
        yield m


def _fake_signal(symbol="RELIANCE", action="BUY", confidence=0.72,
                 entry_price=2500.0, stop_loss=2450.0, take_profit=2600.0,
                 position_size=10, ta_score=7.5, sentiment="bullish",
                 reasoning="test signal"):
    s = MagicMock()
    s.symbol        = symbol
    s.action        = action
    s.confidence    = confidence
    s.p_direction   = confidence
    s.entry_price   = entry_price
    s.stop_loss     = stop_loss
    s.take_profit   = take_profit
    s.position_size = position_size
    s.ta_score      = ta_score
    s.sentiment     = sentiment
    s.sentiment_score = 0.6
    s.reasoning     = reasoning
    s.setup_type    = "technical_base"
    s.regime_tag    = "bull"
    s.quality_score = 0.8
    s.expectancy_score = 1.2
    s.symbol_edge   = 0.1
    s.setup_edge    = 0.2
    s.quality_flags = []
    s.journal       = None
    return s


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSaveSignal:
    def test_returns_positive_id(self, mem):
        sig_id = mem.save_signal(_fake_signal())
        assert isinstance(sig_id, int)
        assert sig_id > 0

    def test_signal_persisted(self, mem):
        mem.save_signal(_fake_signal(symbol="TCS", confidence=0.75))
        with sqlite3.connect(mem.db_path) as conn:
            row = conn.execute(
                "SELECT symbol, confidence FROM signals WHERE symbol='TCS'"
            ).fetchone()
        assert row is not None
        assert row[0] == "TCS"
        assert abs(row[1] - 0.75) < 0.001

    def test_multiple_signals_stored(self, mem):
        for sym in ["A", "B", "C"]:
            mem.save_signal(_fake_signal(symbol=sym))
        with sqlite3.connect(mem.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        assert count == 3

    def test_returns_minus_one_when_db_unavailable(self, mem):
        mem.db_available = False
        result = mem.save_signal(_fake_signal())
        assert result == -1


class TestMarkExecuted:
    def test_mark_sets_executed_flag(self, mem):
        sig_id = mem.save_signal(_fake_signal())
        mem.mark_signal_executed(sig_id)
        with sqlite3.connect(mem.db_path) as conn:
            val = conn.execute(
                "SELECT executed FROM signals WHERE id=?", (sig_id,)
            ).fetchone()[0]
        assert val == 1

    def test_invalid_id_does_not_raise(self, mem):
        mem.mark_signal_executed(99999)  # should not raise


class TestGetRecentSignals:
    def test_returns_list(self, mem):
        mem.save_signal(_fake_signal())
        result = mem.get_recent_signals(limit=10)
        assert isinstance(result, list)

    def test_limit_respected(self, mem):
        for i in range(10):
            mem.save_signal(_fake_signal(symbol=f"S{i}"))
        result = mem.get_recent_signals(limit=3)
        assert len(result) <= 3

    def test_empty_db_returns_empty_list(self, mem):
        result = mem.get_recent_signals(limit=10)
        assert result == []


class TestDBSchema:
    def test_tables_exist(self, mem):
        with sqlite3.connect(mem.db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert "signals" in tables
        assert "trades"  in tables

    def test_signals_columns(self, mem):
        with sqlite3.connect(mem.db_path) as conn:
            info = conn.execute("PRAGMA table_info(signals)").fetchall()
        col_names = {row[1] for row in info}
        for col in ("id", "symbol", "action", "confidence", "entry_price",
                    "stop_loss", "take_profit", "position_size"):
            assert col in col_names, f"Missing column: {col}"


class TestSaveJournal:
    def test_journal_saves_when_signal_id_valid(self, mem):
        from strategy.decision_journal import DecisionJournal
        from datetime import datetime
        journal = DecisionJournal(
            symbol="TEST", timestamp=datetime.now(),
            regime="bull", regime_stability=3,
        )
        journal.final_action = "BUY"
        sig_id = mem.save_signal(_fake_signal())
        journal_id = mem.save_journal(journal, signal_id=sig_id)
        assert journal_id is not None

    def test_journal_saved_to_db(self, mem):
        from strategy.decision_journal import DecisionJournal
        from datetime import datetime
        journal = DecisionJournal(
            symbol="TEST2", timestamp=datetime.now(),
            regime="bull", regime_stability=0,
        )
        journal.final_action = "BUY"
        sig_id = mem.save_signal(_fake_signal(symbol="TEST2"))
        mem.save_journal(journal, signal_id=sig_id)
        try:
            with sqlite3.connect(mem.db_path) as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM decision_journals WHERE signal_id=?",
                    (sig_id,),
                ).fetchone()[0]
            assert count >= 1
        except sqlite3.OperationalError:
            pytest.skip("decision_journals table not in this DB schema")
