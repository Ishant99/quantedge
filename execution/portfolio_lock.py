# =============================================================================
# execution/portfolio_lock.py — Cross-platform file locking for portfolio JSON
#
# Prevents data corruption when multiple scheduler jobs (executor, price_monitor,
# trailing_stop, EOD close) read/write virtual_portfolio.json concurrently.
#
# Usage:
#   from execution.portfolio_lock import load_portfolio_locked, save_portfolio_locked
#
#   data = load_portfolio_locked(path)          # read with lock
#   save_portfolio_locked(path, data)           # write with lock
# =============================================================================

import json
import os
import sys
import contextlib

# Platform-specific locking
if sys.platform == "win32":
    import msvcrt

    @contextlib.contextmanager
    def _file_lock(f):
        """Windows file lock using msvcrt."""
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            try:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
else:
    import fcntl

    @contextlib.contextmanager
    def _file_lock(f):
        """Unix file lock using fcntl."""
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load_portfolio_locked(path: str) -> dict:
    """Read portfolio JSON with an exclusive file lock."""
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        with _file_lock(f):
            return json.load(f)


def save_portfolio_locked(path: str, data: dict) -> None:
    """Write portfolio JSON with an exclusive file lock."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        with _file_lock(f):
            json.dump(data, f, indent=2)
