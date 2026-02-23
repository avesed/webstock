"""Stock list persistence service.

Builds the full stock list via stock_list_service.build_stock_list(), saves to
msgpack on disk, and publishes a Redis reload event so the backend can pick up
the new data.

Data directory: /app/data/stock_list/ (inside the data_service_data volume).

File layout:
  stocks.msgpack         -- msgpack-serialised list of stock dicts
  stocks.msgpack.sha256  -- hex SHA256 of stocks.msgpack
  version.json           -- {"version": "20260223053000", "updated_at": "...", "stock_count": 37000}
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import msgpack

from app.core.cache import get_redis

logger = logging.getLogger(__name__)

# Persistence directory (inside data_service_data volume mount)
_DATA_DIR = Path("/app/data/stock_list")
_STOCKS_FILE = _DATA_DIR / "stocks.msgpack"
_SHA256_FILE = _DATA_DIR / "stocks.msgpack.sha256"
_VERSION_FILE = _DATA_DIR / "version.json"

# Redis progress key (for admin UI polling)
_PROGRESS_KEY = "ds:stock_list:progress"
_PROGRESS_TTL = 3600  # 1 hour


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_and_save_stock_list() -> Dict[str, Any]:
    """Build the full stock list and persist to disk as msgpack.

    Steps:
        1. Call existing build_stock_list() to fetch from all markets.
        2. Deduplicate by symbol (already done inside build_stock_list, but
           we guard again here for safety).
        3. Save to msgpack with SHA256 checksum and version.json.
        4. Invalidate symbol_resolver cache for all markets.
        5. Publish ``stock_list_reload`` to Redis so the backend auto-reloads.

    Returns:
        Dict with status, total_stocks, and by_market breakdown.
    """
    t0 = time.monotonic()

    await _set_progress("building", "Fetching stock data from all markets...")

    # 1. Fetch from all markets
    from app.services.stock_list_service import build_stock_list

    all_stocks = await build_stock_list()

    if not all_stocks:
        await _set_progress("failed", "No stock data returned from fetchers")
        return {"status": "error", "reason": "no_data"}

    await _set_progress(
        "saving",
        f"Saving {len(all_stocks)} stocks to disk...",
    )

    # 2. Deduplicate by symbol (safety guard)
    seen: set[str] = set()
    unique: List[Dict[str, Any]] = []
    for stock in all_stocks:
        sym = stock.get("symbol", "")
        if sym and sym not in seen:
            seen.add(sym)
            unique.append(stock)

    # 3. Save to disk
    _save_to_disk(unique)

    # 4. Invalidate symbol_resolver caches
    from app.services import symbol_resolver

    for market in ("us", "hk", "cn"):
        await symbol_resolver.invalidate_cache(market)

    # 5. Publish reload event to Redis
    await _publish_reload()

    elapsed = time.monotonic() - t0

    # Build by-market breakdown
    by_market: Dict[str, int] = {}
    for s in unique:
        m = s.get("market", "unknown")
        by_market[m] = by_market.get(m, 0) + 1

    logger.info(
        "Stock list built and saved: %d stocks in %.1fs -- %s",
        len(unique),
        elapsed,
        by_market,
    )

    await _set_progress(
        "completed",
        f"Saved {len(unique)} stocks in {elapsed:.1f}s",
        extra={"total_stocks": len(unique), "by_market": by_market},
    )

    return {
        "status": "success",
        "total_stocks": len(unique),
        "by_market": by_market,
    }


def get_stock_list_binary() -> Optional[bytes]:
    """Return raw msgpack bytes for download.

    Returns:
        bytes if the file exists, None otherwise.
    """
    if not _STOCKS_FILE.is_file():
        return None
    try:
        return _STOCKS_FILE.read_bytes()
    except Exception as e:
        logger.error("Failed to read stock list binary: %s", e)
        return None


def get_stock_list_metadata() -> Optional[Dict[str, Any]]:
    """Return version info from version.json.

    Returns:
        Dict with stock_count, version, updated_at, or None if not available.
    """
    if not _VERSION_FILE.is_file():
        return None
    try:
        return json.loads(_VERSION_FILE.read_text())
    except Exception as e:
        logger.warning("Failed to read stock list metadata: %s", e)
        return None


async def get_progress() -> Optional[Dict[str, Any]]:
    """Read stock list build progress from Redis (for admin API)."""
    try:
        r = await get_redis()
        data = await r.get(_PROGRESS_KEY)
        if data is not None:
            return json.loads(data)
    except Exception as exc:
        logger.warning("Failed to read stock list progress: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _save_to_disk(stocks: List[Dict[str, Any]]) -> None:
    """Write stocks to msgpack file with checksum and version metadata.

    Uses atomic temp-file + rename to prevent corrupt reads during writes.

    Raises:
        RuntimeError: If the save fails for any reason.
    """
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)

        # Write msgpack atomically (temp + rename)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(_DATA_DIR), suffix=".tmp",
        )
        try:
            with open(tmp_fd, "wb") as f:
                msgpack.pack(stocks, f)
            os.rename(tmp_path, str(_STOCKS_FILE))
        except BaseException:
            os.unlink(tmp_path)
            raise

        # Compute and write SHA256 checksum atomically
        sha256_hash = _compute_sha256(_STOCKS_FILE)
        tmp_fd2, tmp_path2 = tempfile.mkstemp(
            dir=str(_DATA_DIR), suffix=".tmp",
        )
        try:
            with open(tmp_fd2, "w") as f:
                f.write(sha256_hash)
            os.rename(tmp_path2, str(_SHA256_FILE))
        except BaseException:
            os.unlink(tmp_path2)
            raise

        # Write version metadata atomically
        now = datetime.now(timezone.utc)
        version_data = {
            "version": now.strftime("%Y%m%d%H%M%S"),
            "updated_at": now.isoformat(),
            "stock_count": len(stocks),
        }
        tmp_fd3, tmp_path3 = tempfile.mkstemp(
            dir=str(_DATA_DIR), suffix=".tmp",
        )
        try:
            with open(tmp_fd3, "w") as f:
                json.dump(version_data, f, indent=2)
            os.rename(tmp_path3, str(_VERSION_FILE))
        except BaseException:
            os.unlink(tmp_path3)
            raise

        logger.info("Saved %d stocks to %s", len(stocks), _STOCKS_FILE)

    except Exception as e:
        logger.exception("Failed to save stock list to disk: %s", e)
        raise RuntimeError(f"Failed to save stock list: {e}") from e


def _compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hex digest of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


async def _publish_reload() -> None:
    """Publish stock_list_reload event to Redis so the backend reloads."""
    try:
        r = await get_redis()
        await r.publish("stock_list_reload", "reload")
        logger.info("Published stock_list_reload event to Redis")
    except Exception as e:
        logger.warning("Failed to publish stock_list_reload: %s", e)


async def _set_progress(
    status: str,
    message: str,
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Write progress to Redis for admin UI polling."""
    try:
        r = await get_redis()
        progress: Dict[str, Any] = {
            "status": status,
            "message": message,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            progress.update(extra)
        await r.setex(_PROGRESS_KEY, _PROGRESS_TTL, json.dumps(progress))
    except Exception:
        pass  # Non-critical
