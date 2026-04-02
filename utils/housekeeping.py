import os
from datetime import datetime, timedelta


LOGS_DIR = "logs"
MARKET_DATA_DIR = os.path.join(LOGS_DIR, "market_data")
BACKTEST_DIR = os.path.join(LOGS_DIR, "backtest_results")


def _file_info(path: str) -> dict:
    try:
        stat = os.stat(path)
        return {
            "path": path,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        }
    except OSError:
        return {"path": path, "size": 0, "mtime": 0.0}


def _walk_files(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    files: list[dict] = []
    for root, _, names in os.walk(path):
        for name in names:
            files.append(_file_info(os.path.join(root, name)))
    return files


def _bytes_to_mb(size: int) -> float:
    return round(size / (1024 * 1024), 2)


def summarize_runtime_storage() -> dict:
    os.makedirs(LOGS_DIR, exist_ok=True)

    log_files = []
    for name in os.listdir(LOGS_DIR):
        if name.startswith("agent_") and name.endswith(".log"):
            log_files.append(_file_info(os.path.join(LOGS_DIR, name)))

    cache_files = _walk_files(MARKET_DATA_DIR)
    backtest_files = _walk_files(BACKTEST_DIR)
    chroma_files = _walk_files(os.path.join(LOGS_DIR, "chromadb"))

    trades_db = _file_info(os.path.join(LOGS_DIR, "trades.db"))
    trades_journal = _file_info(os.path.join(LOGS_DIR, "trades.db-journal"))

    return {
        "log_files": len(log_files),
        "log_size_mb": _bytes_to_mb(sum(f["size"] for f in log_files)),
        "oldest_log_days": round(
            max((datetime.now() - datetime.fromtimestamp(f["mtime"])).days for f in log_files),
            1,
        ) if log_files else 0,
        "cache_files": len(cache_files),
        "cache_size_mb": _bytes_to_mb(sum(f["size"] for f in cache_files)),
        "backtest_files": len(backtest_files),
        "backtest_size_mb": _bytes_to_mb(sum(f["size"] for f in backtest_files)),
        "chroma_size_mb": _bytes_to_mb(sum(f["size"] for f in chroma_files)),
        "db_size_mb": _bytes_to_mb(trades_db["size"]),
        "db_journal_present": trades_journal["size"] > 0,
    }


def cleanup_runtime_artifacts(
    keep_log_days: int = 14,
    max_log_files: int = 20,
    keep_market_data_days: int = 7,
    keep_backtest_days: int = 30,
) -> dict:
    os.makedirs(LOGS_DIR, exist_ok=True)

    removed: list[str] = []
    now = datetime.now()

    log_files = []
    for name in os.listdir(LOGS_DIR):
        if name.startswith("agent_") and name.endswith(".log"):
            log_files.append(_file_info(os.path.join(LOGS_DIR, name)))

    log_files = sorted(log_files, key=lambda item: item["mtime"], reverse=True)
    log_cutoff = now - timedelta(days=keep_log_days)

    for idx, info in enumerate(log_files):
        too_old = info["mtime"] and datetime.fromtimestamp(info["mtime"]) < log_cutoff
        too_many = idx >= max_log_files
        if too_old or too_many:
            try:
                os.remove(info["path"])
                removed.append(info["path"])
            except OSError:
                pass

    for base_dir, keep_days in (
        (MARKET_DATA_DIR, keep_market_data_days),
        (BACKTEST_DIR, keep_backtest_days),
    ):
        cutoff = now - timedelta(days=keep_days)
        for info in _walk_files(base_dir):
            if info["mtime"] and datetime.fromtimestamp(info["mtime"]) < cutoff:
                try:
                    os.remove(info["path"])
                    removed.append(info["path"])
                except OSError:
                    pass

    summary = summarize_runtime_storage()
    summary["removed_files"] = len(removed)
    summary["removed_paths"] = removed[:25]
    return summary
