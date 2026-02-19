"""Unified log format for all WebStock processes.

Reads LOG_TAG env to distinguish source: web, worker, bars, beat.
Format: "HH:MM:SS [tag] L message" (L = single-char level I/W/E/D)
"""
import logging
import os
import sys

_TAG = os.environ.get("LOG_TAG", "app")
LOG_FORMAT = f"%(asctime)s [{_TAG}] %(levelname).1s %(message)s"
LOG_DATEFMT = "%H:%M:%S"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger for Docker stdout output."""
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
    root.addHandler(handler)
    for name in ("httpx", "httpcore", "urllib3", "asyncio", "watchfiles", "multipart"):
        logging.getLogger(name).setLevel(logging.WARNING)
