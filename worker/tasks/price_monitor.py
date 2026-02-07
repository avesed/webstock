"""Price monitoring Celery task for price alerts."""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from worker.celery_app import celery_app

# Use Celery-safe database utilities (avoids event loop conflicts)
from worker.db_utils import get_task_session

logger = logging.getLogger(__name__)


def is_trading_hours(market: str = "US") -> bool:
    """
    Check if current time is within trading hours.

    Args:
        market: Market identifier (US, HK, SH, SZ, METAL)

    Returns:
        True if within trading hours

    Trading hours (local time):
    - US: 9:30 AM - 4:00 PM ET (Mon-Fri)
    - HK: 9:30 AM - 4:00 PM HKT (Mon-Fri)
    - CN: 9:30 AM - 3:00 PM CST (Mon-Fri)
    - METAL: Sun 6:00 PM - Fri 5:00 PM ET (CME Globex, nearly 24/5)
    """
    now = datetime.now(timezone.utc)
    weekday = now.weekday()
    hour = now.hour

    if market == "METAL":
        # Precious metals on CME Globex trade Sun 6pm - Fri 5pm ET (nearly 24/5)
        # In UTC: Sun 23:00 - Fri 22:00 (approximately)
        # Simplified trading schedule:
        # - Saturday: completely closed
        # - Sunday: opens at 6pm ET (23:00 UTC)
        # - Monday-Thursday: 24 hours
        # - Friday: closes at 5pm ET (22:00 UTC)
        if weekday == 5:  # Saturday - completely closed
            logger.debug(f"METAL market closed: Saturday")
            return False
        if weekday == 6 and hour < 23:  # Sunday before 6pm ET (23:00 UTC) - closed
            logger.debug(f"METAL market closed: Sunday before open (hour={hour})")
            return False
        if weekday == 4 and hour >= 22:  # Friday after 5pm ET (22:00 UTC) - closed
            logger.debug(f"METAL market closed: Friday after close (hour={hour})")
            return False
        logger.debug(f"METAL market open: weekday={weekday}, hour={hour}")
        return True

    # Skip weekends for stock markets
    if weekday >= 5:  # Saturday = 5, Sunday = 6
        return False

    if market == "US":
        # US market: 14:30 - 21:00 UTC (9:30 AM - 4:00 PM ET)
        # During daylight saving: 13:30 - 20:00 UTC
        return 13 <= hour <= 21
    elif market == "HK":
        # HK market: 01:30 - 08:00 UTC (9:30 AM - 4:00 PM HKT)
        return 1 <= hour <= 8
    elif market in ("SH", "SZ"):
        # China market: 01:30 - 07:00 UTC (9:30 AM - 3:00 PM CST)
        return 1 <= hour <= 7

    # Default: assume trading
    return True


def detect_market(symbol: str) -> str:
    """
    Detect market from symbol format.

    Args:
        symbol: Stock or commodity symbol

    Returns:
        Market identifier (US, HK, SH, SZ, METAL)
    """
    symbol = symbol.upper()

    # Check for precious metals futures first (they use =F suffix)
    # GC=F (Gold), SI=F (Silver), PL=F (Platinum), PA=F (Palladium)
    if symbol in ("GC=F", "SI=F", "PL=F", "PA=F"):
        logger.debug(f"Detected market METAL for symbol: {symbol}")
        return "METAL"

    if symbol.endswith(".HK"):
        logger.debug(f"Detected market HK for symbol: {symbol}")
        return "HK"
    elif symbol.endswith(".SS"):
        logger.debug(f"Detected market SH for symbol: {symbol}")
        return "SH"
    elif symbol.endswith(".SZ"):
        logger.debug(f"Detected market SZ for symbol: {symbol}")
        return "SZ"

    logger.debug(f"Detected market US for symbol: {symbol}")
    return "US"


@celery_app.task(bind=True, max_retries=3)
def monitor_prices(self):
    """
    Periodic task to check price alerts against current prices.

    Runs every minute during trading hours to:
    1. Get all active, non-triggered alerts
    2. Batch fetch current prices for unique symbols
    3. Check each alert condition
    4. Trigger notifications for matched alerts
    5. Mark alerts as triggered

    This task is registered with Celery Beat schedule.
    """
    import asyncio

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_monitor_prices_async())
            return result
        finally:
            loop.close()
    except Exception as e:
        logger.exception(f"Price monitor task failed: {e}")
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


async def _monitor_prices_async() -> Dict[str, Any]:
    """Async implementation of price monitoring."""
    from app.services.alert_service import (
        check_and_trigger_alerts,
        get_all_active_alert_symbols,
    )
    from app.services.stock_service import get_stock_service

    logger.info("Starting price monitor task")

    stats = {
        "symbols_checked": 0,
        "alerts_checked": 0,
        "alerts_triggered": 0,
        "skipped_markets": [],
    }

    try:
        async with get_task_session() as db:
            # Get all unique symbols with active alerts
            symbols = await get_all_active_alert_symbols(db)

            if not symbols:
                logger.info("No active alerts to monitor")
                return stats

            # Group symbols by market and filter by trading hours
            symbols_to_check = []
            for symbol in symbols:
                market = detect_market(symbol)
                if is_trading_hours(market):
                    symbols_to_check.append(symbol)
                else:
                    if market not in stats["skipped_markets"]:
                        stats["skipped_markets"].append(market)

            if not symbols_to_check:
                logger.info(
                    f"No symbols to check during current trading hours. "
                    f"Skipped markets: {stats['skipped_markets']}"
                )
                return stats

            stats["symbols_checked"] = len(symbols_to_check)
            logger.info(f"Checking prices for {len(symbols_to_check)} symbols")

            # Batch fetch prices
            stock_service = await get_stock_service()
            prices = await stock_service.get_batch_quotes(symbols_to_check)

            # Filter out None prices
            valid_prices = {
                symbol: quote
                for symbol, quote in prices.items()
                if quote is not None
            }

            if not valid_prices:
                logger.warning("No valid prices received")
                return stats

            logger.info(f"Got prices for {len(valid_prices)} symbols")

            # Check alerts and trigger matching ones
            triggered_count = await check_and_trigger_alerts(db, valid_prices)
            stats["alerts_triggered"] = triggered_count

            logger.info(
                f"Price monitor completed: {stats['symbols_checked']} symbols, "
                f"{triggered_count} alerts triggered"
            )

    except Exception as e:
        logger.exception(f"Error in price monitor: {e}")
        raise

    return stats


@celery_app.task
def cleanup_old_triggered_alerts():
    """
    Cleanup task to remove old triggered alerts.

    Removes triggered alerts that are older than 30 days.
    This task should be scheduled to run daily.
    """
    import asyncio

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_cleanup_alerts_async())
            return result
        finally:
            loop.close()
    except Exception as e:
        logger.exception(f"Alert cleanup task failed: {e}")
        raise


async def _cleanup_alerts_async() -> Dict[str, Any]:
    """Async implementation of alert cleanup."""
    from sqlalchemy import delete

    from app.models.alert import PriceAlert

    logger.info("Starting alert cleanup task")

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=30)

    try:
        async with get_task_session() as db:
            # Delete old triggered alerts
            query = delete(PriceAlert).where(
                PriceAlert.is_triggered == True,
                PriceAlert.triggered_at < cutoff_date,
            )
            result = await db.execute(query)
            await db.commit()

            deleted_count = result.rowcount

            logger.info(f"Cleaned up {deleted_count} old triggered alerts")

            return {
                "deleted_count": deleted_count,
                "cutoff_date": cutoff_date.isoformat(),
            }

    except Exception as e:
        logger.exception(f"Error in alert cleanup: {e}")
        raise


@celery_app.task
def cleanup_inactive_subscriptions():
    """
    Cleanup task to remove inactive push subscriptions.

    Removes subscriptions that have been inactive for more than 90 days.
    This task should be scheduled to run weekly.
    """
    import asyncio

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_cleanup_subscriptions_async())
            return result
        finally:
            loop.close()
    except Exception as e:
        logger.exception(f"Subscription cleanup task failed: {e}")
        raise


async def _cleanup_subscriptions_async() -> Dict[str, Any]:
    """Async implementation of subscription cleanup."""
    from sqlalchemy import delete

    from app.models.alert import PushSubscription

    logger.info("Starting subscription cleanup task")

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=90)

    try:
        async with get_task_session() as db:
            # Delete old inactive subscriptions
            query = delete(PushSubscription).where(
                PushSubscription.is_active == False,
                PushSubscription.created_at < cutoff_date,
            )
            result = await db.execute(query)
            await db.commit()

            deleted_count = result.rowcount

            logger.info(f"Cleaned up {deleted_count} inactive subscriptions")

            return {
                "deleted_count": deleted_count,
                "cutoff_date": cutoff_date.isoformat(),
            }

    except Exception as e:
        logger.exception(f"Error in subscription cleanup: {e}")
        raise
