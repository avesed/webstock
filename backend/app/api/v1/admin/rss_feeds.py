"""Admin RSS feed management endpoints."""

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import rate_limit
from app.core.security import require_admin
from app.db.database import get_db
from app.models.rss_feed import RssFeed
from app.models.user import User
from app.schemas.rss_feed import (
    RssFeedCreate,
    RssFeedListResponse,
    RssFeedResponse,
    RssFeedStatsResponse,
    RssFeedStatsItem,
    RssFeedTestRequest,
    RssFeedTestResponse,
    RssFeedTestArticle,
    RssFeedUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin - RSS Feeds"])


# ============== RSS Feed Helper ==============


def _feed_to_response(feed: RssFeed) -> RssFeedResponse:
    """Convert RssFeed model to response schema."""
    return RssFeedResponse(
        id=str(feed.id),
        name=feed.name,
        rsshub_route=feed.rsshub_route,
        description=feed.description,
        category=feed.category,
        symbol=feed.symbol,
        market=feed.market,
        poll_interval_minutes=feed.poll_interval_minutes,
        fulltext_mode=feed.fulltext_mode,
        is_enabled=feed.is_enabled,
        last_polled_at=feed.last_polled_at,
        last_error=feed.last_error,
        consecutive_errors=feed.consecutive_errors,
        article_count=feed.article_count,
        created_at=feed.created_at,
        updated_at=feed.updated_at,
    )


# ============== RSS Feed Management Endpoints ==============


@router.get(
    "/rss-feeds",
    response_model=RssFeedListResponse,
    summary="List all RSS feeds",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def list_rss_feeds(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    category: Optional[str] = Query(None, description="Filter by category: media, exchange, social"),
    is_enabled: Optional[bool] = Query(None, description="Filter by enabled status"),
):
    """Get all configured RSS feeds."""
    logger.info("Admin %d listing RSS feeds (category=%s, enabled=%s)", admin.id, category, is_enabled)

    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    feeds, total = await rss_service.list_feeds(db, category=category, is_enabled=is_enabled)

    return RssFeedListResponse(
        feeds=[_feed_to_response(f) for f in feeds],
        total=total,
    )


@router.post(
    "/rss-feeds",
    response_model=RssFeedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new RSS feed",
    dependencies=[Depends(rate_limit(max_requests=20, window_seconds=60))],
)
async def create_rss_feed(
    data: RssFeedCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new RSS feed configuration."""
    logger.info(
        "Admin %d creating RSS feed: name=%s, route=%s",
        admin.id, data.name, data.rsshub_route,
    )

    # Check unique route
    existing = await db.execute(
        select(RssFeed).where(RssFeed.rsshub_route == data.rsshub_route)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Feed with route '{data.rsshub_route}' already exists",
        )

    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    feed = await rss_service.create_feed(db, data.model_dump(by_alias=False))

    logger.info(
        "[AUDIT] Admin %d created RSS feed %s (%s)",
        admin.id, feed.id, feed.name,
    )
    return _feed_to_response(feed)


@router.get(
    "/rss-feeds/stats",
    response_model=RssFeedStatsResponse,
    summary="Get RSS feed statistics",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_rss_feed_stats(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get per-feed article statistics."""
    logger.info("Admin %d viewing RSS feed stats", admin.id)

    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    stats = await rss_service.get_feed_stats(db)

    return RssFeedStatsResponse(
        total_feeds=stats["total_feeds"],
        enabled_feeds=stats["enabled_feeds"],
        total_articles=stats["total_articles"],
        feeds=[RssFeedStatsItem(**f) for f in stats["feeds"]],
    )


@router.post(
    "/rss-feeds/test",
    response_model=RssFeedTestResponse,
    summary="Test an RSSHub route",
    dependencies=[Depends(rate_limit(max_requests=10, window_seconds=60))],
)
async def test_rss_feed(
    data: RssFeedTestRequest,
    admin: User = Depends(require_admin),
):
    """Test an RSSHub route without saving to the database."""
    logger.info(
        "Admin %d testing RSS route: %s (fulltext=%s)",
        admin.id, data.rsshub_route, data.fulltext_mode,
    )

    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    result = await rss_service.test_feed(data.rsshub_route, fulltext=data.fulltext_mode)

    return RssFeedTestResponse(
        route=result["route"],
        article_count=result["article_count"],
        articles=[RssFeedTestArticle(**a) for a in result["articles"]],
        error=result["error"],
    )


@router.post(
    "/rss-feeds/trigger",
    summary="Manually trigger RSS monitor task",
    dependencies=[Depends(rate_limit(max_requests=5, window_seconds=60))],
)
async def trigger_rss_monitor(
    admin: User = Depends(require_admin),
):
    """Manually trigger the RSS monitor Celery task."""
    logger.info("Admin %d manually triggering RSS monitor", admin.id)

    from worker.tasks.rss_monitor import monitor_rss_feeds
    task = monitor_rss_feeds.delay()

    return {"message": "RSS monitor task triggered", "task_id": str(task.id)}


@router.get(
    "/rss-feeds/{feed_id}",
    response_model=RssFeedResponse,
    summary="Get RSS feed details",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def get_rss_feed(
    feed_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get a single RSS feed by ID."""
    logger.info("Admin %d viewing RSS feed %s", admin.id, feed_id)

    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    feed = await rss_service.get_feed(db, feed_id)

    if not feed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RSS feed not found",
        )

    return _feed_to_response(feed)


@router.put(
    "/rss-feeds/{feed_id}",
    response_model=RssFeedResponse,
    summary="Update an RSS feed",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def update_rss_feed(
    feed_id: UUID,
    data: RssFeedUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing RSS feed configuration."""
    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    feed = await rss_service.get_feed(db, feed_id)

    if not feed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RSS feed not found",
        )

    # by_alias=False: CamelModel defaults to camelCase keys due to
    # serialize_by_alias=True, but SQLAlchemy uses snake_case columns.
    update_fields = data.model_dump(exclude_none=True, by_alias=False)
    logger.info(
        "Admin %d updating RSS feed %s: %s",
        admin.id, feed_id, list(update_fields.keys()),
    )

    # Check unique route if changing
    if data.rsshub_route is not None and data.rsshub_route != feed.rsshub_route:
        existing = await db.execute(
            select(RssFeed).where(
                RssFeed.rsshub_route == data.rsshub_route,
                RssFeed.id != feed_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Feed with route '{data.rsshub_route}' already exists",
            )

    feed = await rss_service.update_feed(db, feed, update_fields)

    logger.info(
        "[AUDIT] Admin %d updated RSS feed %s (%s)",
        admin.id, feed.id, feed.name,
    )
    return _feed_to_response(feed)


@router.delete(
    "/rss-feeds/{feed_id}",
    summary="Delete an RSS feed",
    dependencies=[Depends(rate_limit(max_requests=10, window_seconds=60))],
)
async def delete_rss_feed(
    feed_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete an RSS feed. Associated news articles retain their rss_feed_id (SET NULL)."""
    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    feed = await rss_service.get_feed(db, feed_id)

    if not feed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RSS feed not found",
        )

    logger.info(
        "[AUDIT] Admin %d deleting RSS feed %s (%s)",
        admin.id, feed.id, feed.name,
    )
    await rss_service.delete_feed(db, feed)

    return {"message": f"RSS feed '{feed.name}' deleted"}


@router.post(
    "/rss-feeds/{feed_id}/toggle",
    response_model=RssFeedResponse,
    summary="Toggle RSS feed enabled status",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def toggle_rss_feed(
    feed_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Quick toggle enable/disable for an RSS feed."""
    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    feed = await rss_service.get_feed(db, feed_id)

    if not feed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RSS feed not found",
        )

    new_status = not feed.is_enabled
    feed = await rss_service.update_feed(db, feed, {"is_enabled": new_status})

    logger.info(
        "[AUDIT] Admin %d toggled RSS feed %s (%s) to enabled=%s",
        admin.id, feed.id, feed.name, new_status,
    )
    return _feed_to_response(feed)
