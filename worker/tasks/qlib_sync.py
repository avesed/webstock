"""Qlib market data sync task -- triggers qlib-service to pull data from backend.

Usually chain-triggered after daily bar collection completes.
Can also be triggered manually.
"""

import logging

from celery import shared_task

from worker.task_helpers import run_async_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="worker.tasks.qlib_sync.sync_qlib_market",
    max_retries=2,
    default_retry_delay=60,
    time_limit=600,
    soft_time_limit=540,
)
def sync_qlib_market(self, market: str):
    """Trigger qlib-service to sync data for a single market.

    Args:
        market: Market code (us, hk, cn, metal).
    """
    logger.info("Qlib同步：开始%s市场", market)

    async def _sync():
        from app.services.qlib_client import get_qlib_client

        client = await get_qlib_client()
        logger.debug("Calling qlib-service sync_market: market=%s, update_only=True", market)
        return await client.sync_market(market=market, update_only=True)

    try:
        result = run_async_task(_sync)
        logger.info(
            "Qlib同步：%s市场完成 %d股票 %d错误 %.1f秒",
            market,
            result.get("symbol_count", 0) if isinstance(result, dict) else 0,
            len(result.get("errors", [])) if isinstance(result, dict) else 0,
            result.get("duration_s", 0) if isinstance(result, dict) else 0,
        )
        return result
    except Exception as exc:
        logger.exception("Qlib sync failed for market=%s: %s", market, exc)
        raise self.retry(exc=exc)
