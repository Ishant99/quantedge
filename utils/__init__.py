import logging
import os
import sys

os.makedirs("logs", exist_ok=True)

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler — force UTF-8 on Windows to handle Rs and >= symbols
    import io
    if hasattr(sys.stdout, "buffer"):
        stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    else:
        stream = sys.stdout
    ch = logging.StreamHandler(stream)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Rotating file handler — 50 MB per file, 7 backups kept
    from logging.handlers import RotatingFileHandler
    from datetime import datetime
    log_file = f"logs/agent_{datetime.now().strftime('%Y%m%d')}.log"
    fh = RotatingFileHandler(
        log_file, maxBytes=50 * 1024 * 1024, backupCount=7, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger
