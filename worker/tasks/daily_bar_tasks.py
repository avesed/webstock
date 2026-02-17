"""Daily bar collection tasks -- fetch OHLCV data from providers and store in PostgreSQL.

Each market has its own Celery Beat schedule aligned to market close times.
On successful collection, triggers qlib-service sync via chain task.
"""

import json
import logging
from datetime import datetime, timezone

from celery import shared_task

from worker.task_helpers import run_async_task

logger = logging.getLogger(__name__)

# Redis progress key pattern for admin dashboard
_PROGRESS_KEY_TEMPLATE = "kb:daily_bars:{market}:progress"
_PROGRESS_TTL = 600  # 10 minutes — auto-expires if task crashes


def _update_daily_bar_progress_sync(market: str, symbols_done: int, symbols_total: int, new_bars: int):
    """Write daily bar collection progress to Redis (sync, for use in async callback)."""
    import redis as redis_lib
    from app.config import settings

    try:
        r = redis_lib.from_url(str(settings.REDIS_URL), decode_responses=True)
        pct = int(symbols_done * 100 / symbols_total) if symbols_total > 0 else 0
        progress = {
            "symbolsDone": symbols_done,
            "symbolsTotal": symbols_total,
            "newBars": new_bars,
            "percent": pct,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        key = _PROGRESS_KEY_TEMPLATE.format(market=market)
        r.set(key, json.dumps(progress), ex=_PROGRESS_TTL)
    except Exception:
        pass  # Non-critical


def _clear_daily_bar_progress_sync(market: str):
    """Clear the progress key from Redis (sync, called in task finally)."""
    import redis as redis_lib
    from app.config import settings

    try:
        r = redis_lib.from_url(str(settings.REDIS_URL), decode_responses=True)
        r.delete(_PROGRESS_KEY_TEMPLATE.format(market=market))
    except Exception:
        pass


@shared_task(
    bind=True,
    name="worker.tasks.daily_bar_tasks.collect_market_daily_bars",
    max_retries=2,
    default_retry_delay=300,
    time_limit=1800,
    soft_time_limit=1740,
)
def collect_market_daily_bars(self, market: str):
    """Collect daily bars for a single market and write to PostgreSQL.

    On success, chain-triggers Qlib sync for the same market.

    Args:
        market: Market code (us, hk, cn, metal).
    """
    logger.info("Starting daily bar collection for market=%s", market)

    async def _collect():
        from app.db.task_session import get_task_session
        from app.services.daily_bar_service import DailyBarService

        symbols = await _get_symbols_for_market(market)
        if not symbols:
            logger.warning("No symbols found for market=%s, skipping", market)
            return {"symbol_count": 0, "new_bars": 0, "errors": ["No symbols"]}

        logger.info("Resolved %d symbols for market=%s", len(symbols), market)
        _update_daily_bar_progress_sync(market, 0, len(symbols), 0)

        async def _on_progress(completed: int, total: int, with_data: int, errors: int):
            _update_daily_bar_progress_sync(market, completed, total, with_data)

        async with get_task_session() as db:
            service = DailyBarService()
            result = await service.collect_market(
                db, market, symbols, on_progress=_on_progress,
            )
            return result

    try:
        result = run_async_task(_collect)
        logger.info(
            "Daily bar collection result for market=%s: symbols=%d, new_bars=%d, errors=%d",
            market,
            result.get("symbol_count", 0),
            result.get("new_bars", 0),
            len(result.get("errors", [])),
        )

        # Chain-trigger Qlib sync if we have symbols (even if no new bars,
        # qlib-service may need to catch up from its last sync point)
        if result.get("symbol_count", 0) > 0:
            from worker.tasks.qlib_sync import sync_qlib_market

            sync_qlib_market.delay(market)
            logger.info("Triggered Qlib sync for market=%s", market)

        return result
    except Exception as exc:
        logger.error(
            "Daily bar collection failed for market=%s: %s", market, exc
        )
        raise self.retry(exc=exc)
    finally:
        _clear_daily_bar_progress_sync(market)


async def _get_symbols_for_market(market: str) -> list[str]:
    """Get symbol list for a market.

    Uses the same logic as the internal API endpoint.
    """
    import asyncio

    if market == "us":
        try:
            from app.api.v1.internal import _fetch_sp500_symbols

            return await asyncio.to_thread(_fetch_sp500_symbols)
        except Exception as exc:
            logger.warning("Failed to fetch S&P 500, using fallback: %s", exc)
            return [
                "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
                "META", "TSLA", "BRK-B", "JPM", "V",
            ]

    elif market == "hk":
        from app.services.hsi_constituents import get_hsi_constituents

        return await get_hsi_constituents()

    elif market == "cn":
        try:
            from app.api.v1.internal import _fetch_cn_symbols

            return await asyncio.to_thread(_fetch_cn_symbols)
        except Exception as exc:
            logger.warning("Failed to fetch A-share list, using fallback: %s", exc)
            return [
                "600519.SS", "601318.SS", "600036.SS", "000858.SZ", "600276.SS",
                "601166.SS", "000333.SZ", "002415.SZ", "600900.SS", "601888.SS",
            ]

    elif market == "metal":
        return ["GC=F", "SI=F", "PL=F", "PA=F"]

    else:
        logger.error("Unknown market: %s", market)
        return []
