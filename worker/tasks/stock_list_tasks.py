"""Stock list update Celery tasks.

This task downloads the pre-built stock list msgpack from data-service and
writes it to the backend's local data directory for fast in-memory search.

The data-service owns the building and persistence of the stock list.  This
task simply downloads the binary and triggers a backend reload.  If the
download fails (e.g., data-service hasn't built one yet), it falls back to
the legacy approach of calling build_stock_list() and saving locally.
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import msgpack

from worker.celery_app import celery_app

logger = logging.getLogger(__name__)

# Backend's local stock list data directory
_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "stock_list"


@celery_app.task(bind=True, max_retries=3, time_limit=600, soft_time_limit=540)
def update_stock_list(self):
    """
    Update the local stock list from the data-service.

    Primary path: download pre-built msgpack from data-service.
    Fallback path: call build_stock_list() API and save locally.

    Scheduled to run daily at 5:30 AM UTC.
    """
    try:
        logger.info("股票列表：开始更新")
        start_time = datetime.utcnow()

        # Run async data-service call
        from worker.task_helpers import run_async_task
        result = run_async_task(_fetch_and_save_stock_list)

        elapsed = (datetime.utcnow() - start_time).total_seconds()
        if result:
            result["elapsed_seconds"] = elapsed
            by_market = result.get("by_market", {})
            logger.info(
                "股票列表：更新完成 共%d只 US=%d CN=%d HK=%d 耗时%.0f秒",
                result.get("total_stocks", 0),
                by_market.get("us", 0),
                sum(by_market.get(m, 0) for m in ("sh", "sz", "bj")),
                by_market.get("hk", 0),
                elapsed,
            )
        else:
            logger.warning("Stock list update returned no result")
            result = {"status": "no_data", "elapsed_seconds": elapsed}

        return result

    except Exception as e:
        logger.exception(f"Stock list update task failed: {e}")
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


async def _fetch_and_save_stock_list() -> Dict[str, Any]:
    """Download pre-built stock list from data-service, with legacy fallback."""
    from app.services.data_service_client import get_data_service_client

    client = await get_data_service_client()

    # --- Primary path: download pre-built msgpack binary ---
    binary = await client.download_stock_list()
    if binary:
        count = _write_msgpack_binary(binary)
        if count > 0:
            _notify_reload()
            by_market = _count_by_market_from_binary(binary)
            return {
                "status": "success",
                "source": "download",
                "total_stocks": count,
                "by_market": by_market,
            }
        logger.warning("Downloaded stock list is empty or corrupt, falling back")

    # --- Fallback path: call build API and save locally ---
    logger.info("Falling back to legacy stock list build")
    return await _fetch_and_save_stock_list_legacy(client)


def _write_msgpack_binary(binary: bytes) -> int:
    """Write raw msgpack binary to the local data directory.

    Also writes SHA256 checksum and version.json alongside the msgpack file.

    Returns:
        Number of stocks in the file, or 0 on error.
    """
    import hashlib
    import json

    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)

        stocks_file = _DATA_DIR / "stocks.msgpack"
        sha256_file = _DATA_DIR / "stocks.msgpack.sha256"
        version_file = _DATA_DIR / "version.json"

        # Write msgpack binary
        stocks_file.write_bytes(binary)

        # Compute and write SHA256 checksum
        sha256_hash = hashlib.sha256(binary).hexdigest()
        sha256_file.write_text(sha256_hash)

        # Count stocks from the binary for metadata
        try:
            stocks = msgpack.unpackb(binary, raw=False)
            count = len(stocks) if isinstance(stocks, list) else 0
        except Exception:
            count = 0

        # Write version metadata
        version_data = {
            "version": datetime.utcnow().strftime("%Y%m%d%H%M%S"),
            "updated_at": datetime.utcnow().isoformat(),
            "stock_count": count,
        }
        version_file.write_text(json.dumps(version_data, indent=2))

        logger.info("Wrote %d stocks to %s (%d bytes)", count, stocks_file, len(binary))
        return count

    except Exception as e:
        logger.exception("Failed to write stock list binary: %s", e)
        return 0


def _count_by_market_from_binary(binary: bytes) -> Dict[str, int]:
    """Unpack the msgpack binary and count stocks by market."""
    try:
        stocks = msgpack.unpackb(binary, raw=False)
        if not isinstance(stocks, list):
            return {}
        by_market: Dict[str, int] = {}
        for s in stocks:
            m = s.get("market", "unknown") if isinstance(s, dict) else "unknown"
            by_market[m] = by_market.get(m, 0) + 1
        return by_market
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Legacy fallback (kept for backward compatibility)
# ---------------------------------------------------------------------------


async def _fetch_and_save_stock_list_legacy(client) -> Dict[str, Any]:
    """Legacy path: call data-service build API and save locally."""
    data = await client.build_stock_list()

    if not data:
        logger.error("Data-service returned no stock list data")
        return {"status": "error", "reason": "no_data_from_service"}

    stocks = data.get("items", [])
    if not stocks:
        logger.error("Data-service returned empty stock list")
        return {"status": "error", "reason": "empty_stock_list"}

    # Add precious metals (static data from backend)
    metals = _get_precious_metals()
    stocks.extend(metals)

    # Deduplicate by symbol (keep first occurrence)
    seen_symbols: set = set()
    unique_stocks: List[Dict[str, Any]] = []
    for stock in stocks:
        symbol = stock.get("symbol", "")
        if symbol and symbol not in seen_symbols:
            seen_symbols.add(symbol)
            unique_stocks.append(stock)

    # Save to file
    if unique_stocks:
        success = _save_stock_list(unique_stocks)
        if not success:
            raise RuntimeError("Failed to save stock list")

    # Trigger backend reload
    _notify_reload()

    # Count by market
    by_market: Dict[str, int] = {}
    for s in unique_stocks:
        m = s.get("market", "unknown")
        by_market[m] = by_market.get(m, 0) + 1

    return {
        "status": "success",
        "source": "legacy",
        "total_stocks": len(unique_stocks),
        "by_market": by_market,
    }


def _get_precious_metals() -> List[Dict[str, Any]]:
    """Get precious metals data from stock_service.py."""
    from app.services.stock_service import PRECIOUS_METALS

    metals = []
    for symbol, meta in PRECIOUS_METALS.items():
        # Generate pinyin for Chinese name
        pinyin, pinyin_initial = _get_pinyin(meta.get("name_zh", ""))

        metals.append({
            "symbol": symbol,
            "name": meta["name"],
            "name_zh": meta.get("name_zh", ""),
            "exchange": meta["exchange"],
            "market": "metal",
            "pinyin": pinyin,
            "pinyin_initial": pinyin_initial,
        })

    logger.debug(f"Got {len(metals)} precious metals")
    return metals


def _get_pinyin(name_zh: str) -> tuple:
    """Generate pinyin from Chinese name."""
    if not name_zh:
        return "", ""

    try:
        from pypinyin import lazy_pinyin, Style

        full = "".join(lazy_pinyin(name_zh))
        initial = "".join(lazy_pinyin(name_zh, style=Style.FIRST_LETTER))
        return full.upper(), initial.upper()
    except Exception as e:
        logger.warning(f"Failed to generate pinyin for '{name_zh}': {e}")
        return "", ""


def _save_stock_list(stocks: List[Dict[str, Any]]) -> bool:
    """Save stock list to msgpack file."""
    try:
        from app.services.stock_list_service import LocalStock, StockListService

        local_stocks = [LocalStock.from_dict(s) for s in stocks]
        service = StockListService()
        return service.save(local_stocks)

    except Exception as e:
        logger.exception(f"Failed to save stock list: {e}")
        return False


def _notify_reload():
    """Notify backend to reload stock list data."""
    try:
        import redis
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        r = redis.Redis.from_url(redis_url)
        r.publish("stock_list_reload", "reload")
        logger.debug("Published stock list reload notification")
    except Exception as e:
        logger.warning(f"Failed to publish reload notification: {e}")


@celery_app.task(bind=True, max_retries=2)
def update_chinese_names(self, symbols: List[str]):
    """Update Chinese names for specific stocks (placeholder)."""
    try:
        logger.info(f"Updating Chinese names for {len(symbols)} stocks")
        return {"status": "success", "updated": 0}
    except Exception as e:
        logger.exception(f"Failed to update Chinese names: {e}")
        raise self.retry(exc=e, countdown=30)


@celery_app.task
def get_stock_list_stats():
    """Get statistics about the current stock list."""
    import asyncio

    async def _get_stats():
        from app.services.stock_list_service import get_stock_list_service
        service = await get_stock_list_service()
        return service.get_stats()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_get_stats())
    finally:
        loop.close()
