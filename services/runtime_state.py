import atexit
import json
import os
import sqlite3
from datetime import datetime

import pytz

from utils.housekeeping import summarize_runtime_storage


IST = pytz.timezone("Asia/Kolkata")
PID_FILE = os.path.join("logs", "scheduler.pid")
STATUS_FILE = os.path.join("logs", "scheduler_status.json")


def safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def format_age(ts: float) -> str:
    if not ts:
        return "Never"
    delta = datetime.now() - datetime.fromtimestamp(ts)
    if delta.total_seconds() < 60:
        return "Just now"
    if delta.total_seconds() < 3600:
        return f"{int(delta.total_seconds() // 60)}m ago"
    if delta.total_seconds() < 86400:
        return f"{int(delta.total_seconds() // 3600)}h ago"
    return f"{delta.days}d ago"


def pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def release_pid_file(pid_file: str = PID_FILE):
    try:
        if os.path.exists(pid_file):
            with open(pid_file) as f:
                current = int(f.read().strip() or "0")
            if current == os.getpid():
                os.remove(pid_file)
    except Exception:
        pass


def acquire_pid_file(pid_file: str = PID_FILE) -> tuple[bool, str]:
    os.makedirs(os.path.dirname(pid_file) or ".", exist_ok=True)

    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                existing_pid = int(f.read().strip() or "0")
            if existing_pid and existing_pid != os.getpid() and pid_running(existing_pid):
                return False, f"Another scheduler instance is already running with PID {existing_pid}"
            os.remove(pid_file)
            message = "Removed stale scheduler.pid file"
        except Exception as e:
            message = f"Could not validate existing PID file: {e}"
    else:
        message = ""

    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    atexit.register(release_pid_file, pid_file)
    return True, message


def read_scheduler_status(status_file: str = STATUS_FILE) -> dict:
    if os.path.exists(status_file):
        try:
            with open(status_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def write_scheduler_status(job_name: str, state: str, detail: str = "", status_file: str = STATUS_FILE):
    os.makedirs(os.path.dirname(status_file) or ".", exist_ok=True)
    payload = read_scheduler_status(status_file)
    now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    jobs = payload.setdefault("jobs", {})
    job_state = jobs.setdefault(job_name, {})
    job_state["state"] = state
    job_state["timestamp"] = now
    if detail:
        job_state["detail"] = detail[:240]
    elif "detail" in job_state:
        job_state.pop("detail", None)

    payload["heartbeat"] = now
    payload["pid"] = os.getpid()
    payload["updated_by"] = job_name
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def file_freshness_rows() -> list[dict]:
    tracked = [
        ("Market regime", os.path.join("logs", "market_regime.json")),
        ("PCR signal", os.path.join("logs", "pcr_signal.json")),
        ("FII/DII signal", os.path.join("logs", "fii_signal.json")),
        ("Paper portfolio", os.path.join("logs", "virtual_portfolio.json")),
        ("Scheduler status", STATUS_FILE),
    ]
    return [{"Source": label, "Freshness": format_age(safe_mtime(path))} for label, path in tracked]


def get_health_snapshot(cfg_get) -> dict:
    scheduler_pid = 0
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                scheduler_pid = int(f.read().strip() or "0")
        except Exception:
            scheduler_pid = 0

    db_ok = True
    db_error = ""
    db_path = os.path.join("logs", "trades.db")
    try:
        if os.path.exists(db_path):
            with sqlite3.connect(db_path, timeout=2) as conn:
                conn.execute("SELECT 1").fetchone()
    except Exception as e:
        db_ok = False
        db_error = str(e)

    signal_files = [
        os.path.join("logs", "market_regime.json"),
        os.path.join("logs", "pcr_signal.json"),
        os.path.join("logs", "fii_signal.json"),
        os.path.join("logs", "virtual_portfolio.json"),
    ]

    return {
        "scheduler_pid": scheduler_pid,
        "scheduler_running": pid_running(scheduler_pid),
        "db_ok": db_ok,
        "db_error": db_error,
        "telegram_ready": bool(cfg_get("TELEGRAM_BOT_TOKEN")) and bool(cfg_get("TELEGRAM_CHAT_ID")),
        "discord_ready": bool(cfg_get("DISCORD_BOT_TOKEN")) and bool(cfg_get("DISCORD_CHANNEL_ID")),
        "latest_signal_ts": max((safe_mtime(path) for path in signal_files), default=0.0),
        "latest_log_ts": max(
            (safe_mtime(os.path.join("logs", name)) for name in os.listdir("logs") if name.endswith(".log")),
            default=0.0,
        ) if os.path.exists("logs") else 0.0,
        "settings_ts": safe_mtime(os.path.join("logs", "user_settings.json")),
        "storage": summarize_runtime_storage(),
        "scheduler_status": read_scheduler_status(),
    }
