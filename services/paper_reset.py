import csv
import json
import os
import shutil
import sqlite3
from datetime import datetime

from config import SQLITE_DB_FILE, VIRTUAL_CAPITAL, VIRTUAL_PORTFOLIO_FILE
from utils import get_logger


logger = get_logger("PaperReset")
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGS_DIR     = os.path.join(_PROJECT_ROOT, "logs")


def _archive_dir() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(_LOGS_DIR, "archive", f"reset_{stamp}")
    os.makedirs(path, exist_ok=True)
    return path


def archive_and_reset_paper_state() -> dict:
    os.makedirs(_LOGS_DIR, exist_ok=True)
    archive_dir = _archive_dir()
    moved = []

    for rel_path in [
        "virtual_portfolio.json",
        "paper_trades.csv",
        "trades.db",
        "unified_state.json",
        "agent_review_report.json",
        "agent_review_report.md",
        "paper_treasury.json",
    ]:
        src = os.path.join(_LOGS_DIR, rel_path)
        if os.path.exists(src):
            dst = os.path.join(archive_dir, rel_path)
            shutil.move(src, dst)
            moved.append(rel_path)

    with open(VIRTUAL_PORTFOLIO_FILE, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "cash": VIRTUAL_CAPITAL,
                "positions": {},
                "total_trades": 0,
                "wins": 0,
                "created": datetime.now().isoformat(),
            },
            handle,
            indent=2,
        )

    with open(os.path.join(_LOGS_DIR, "paper_trades.csv"), "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "symbol", "trade_type", "action", "qty", "entry_price", "exit_price", "pnl", "pnl_pct"])

    if os.path.exists(SQLITE_DB_FILE):
        os.remove(SQLITE_DB_FILE)

    try:
        with sqlite3.connect(SQLITE_DB_FILE) as conn:
            conn.execute("VACUUM")
    except Exception:
        pass

    logger.info(f"Paper state archived to {archive_dir} and reset")
    return {"archive_dir": archive_dir, "moved_files": moved}
