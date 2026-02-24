"""Stock list update Celery tasks.

Triggers data-service to build the stock list and write it directly to the
shared ``stock_symbols`` PostgreSQL table.  The backend's StockListService
detects the new version via Redis polling and reloads from DB automatically.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List

from worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, time_limit=600, soft_time_limit=540)
def update_stock_list(self):
    """Trigger data-service to rebuild the stock list into PostgreSQL.

    Data-service handles:
    1. Fetching ~37K symbols from Finnhub + AKShare
    2. Writing to ``stock_symbols`` table (TRUNCATE + INSERT)
    3. Setting Redis ``stock_list:version`` for backend polling

    Scheduled to run daily at 5:30 AM UTC (via data-service APScheduler).
    Also callable from admin UI for manual trigger.
    """
    try:
        logger.info("股票列表：开始更新（触发 data-service 构建）")
        start_time = datetime.utcnow()

        from worker.task_helpers import run_async_task
        result = run_async_task(_trigger_build)

        elapsed = (datetime.utcnow() - start_time).total_seconds()
        if result is not None:
            # DataServiceClient._request() unwraps ApiResponse, returning
            # the "data" field: {"items": [], "count": N}
            count = result.get("count", 0)
            logger.info(
                "股票列表：更新完成 共%d只 耗时%.0f秒",
                count, elapsed,
            )
            return {
                "status": "success",
                "total_stocks": count,
                "elapsed_seconds": elapsed,
            }
        else:
            logger.warning("股票列表：更新失败 — data-service 返回 None")
            return {"status": "error", "reason": "no response", "elapsed_seconds": elapsed}

    except Exception as e:
        logger.exception("Stock list update task failed: %s", e)
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


async def _trigger_build():
    """Call data-service POST /v1/reference/stock-list to trigger the build.

    Returns the unwrapped "data" dict (e.g. {"items": [], "count": 35385})
    or None if data-service is unreachable / returns an error.
    """
    from app.services.data_service_client import get_data_service_client

    client = await get_data_service_client()
    return await client.build_stock_list()


@celery_app.task(bind=True, max_retries=2)
def update_chinese_names(self, symbols: List[str]):
    """Update Chinese names for specific stocks (placeholder)."""
    try:
        logger.info("Updating Chinese names for %d stocks", len(symbols))
        return {"status": "success", "updated": 0}
    except Exception as e:
        logger.exception("Failed to update Chinese names: %s", e)
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
