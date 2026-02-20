"""Unified log format for all WebStock processes.

Reads LOG_TAG env to distinguish source: web, worker, bars, beat.
Format: "HH:MM:SS [tag] L [req_id] message" (L = single-char level I/W/E/D)
"""
import logging
import os
import sys

_TAG = os.environ.get("LOG_TAG", "app")
LOG_FORMAT = f"%(asctime)s [{_TAG}] %(levelname).1s [%(request_id)s] %(message)s"
LOG_DATEFMT = "%H:%M:%S"


class RequestIdFilter(logging.Filter):
    """Inject request_id into every log record."""

    def filter(self, record):
        try:
            from app.core.request_id import get_request_id_short
            record.request_id = get_request_id_short()
        except ImportError:
            record.request_id = "-"
        return True


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger for Docker stdout output."""
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
    root.addHandler(handler)
    for name in ("httpx", "httpcore", "urllib3", "asyncio", "watchfiles", "multipart"):
        logging.getLogger(name).setLevel(logging.WARNING)
