"""Admin news pipeline statistics, monitoring, and tracing endpoints."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import rate_limit
from app.core.security import require_admin
from app.db.database import get_db
from app.db.redis import get_redis
from app.models.news import News
from app.models.user import User
from app.schemas.admin import (
    ArticleTimelineResponse,
    DailyFilterStatsResponse,
    FilterStatsResponse,
    Layer15CleaningStats,
    Layer15FetchStats,
    Layer15ProviderDistribution,
    Layer15StatsResponse,
    NewsPipelineCacheStats,
    NewsPipelineNodeLatency,
    NewsPipelineRoutingStats,
    NewsPipelineStatsResponse,
    NewsPipelineTokenStats,
    NewsPipelineTokenStage,
    NodeStatsResponse,
    PipelineEventResponse,
    PipelineEventSearchResponse,
    PipelineStatsResponse,
    ScoreDistributionBucket,
    SourceStatsItemResponse,
    SourceStatsResponse,
)
from app.services.pipeline_trace_service import PipelineTraceService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin - News Pipeline"])


# ============== News Filter Statistics Endpoints ==============


@router.get(
    "/news/filter-stats",
    response_model=FilterStatsResponse,
    summary="Get news filter statistics",
    description="Get comprehensive statistics for two-phase news filtering including pass rates and token usage.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_news_filter_stats(
    admin: User = Depends(require_admin),
    days: int = Query(7, ge=1, le=30, description="Number of days to aggregate"),
) -> FilterStatsResponse:
    """
    Get comprehensive news filter statistics.

    Returns:
    - counts: Initial filter (useful/uncertain/skip) and deep filter (keep/delete) counts
    - rates: Pass/skip/delete rates as percentages
    - tokens: Token usage with input/output breakdown and cost estimates
    - alerts: Any threshold violations
    """
    logger.info(f"Admin {admin.id} ({admin.email}) viewing news filter stats for {days} days")

    from app.services.filter_stats_service import get_filter_stats_service

    stats_service = get_filter_stats_service()
    stats = await stats_service.get_comprehensive_stats(days)

    return FilterStatsResponse(**stats)


@router.get(
    "/news/filter-stats/daily",
    response_model=DailyFilterStatsResponse,
    summary="Get daily filter statistics",
    description="Get day-by-day filter statistics for charting.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_daily_filter_stats(
    admin: User = Depends(require_admin),
    days: int = Query(7, ge=1, le=30, description="Number of days to retrieve"),
) -> DailyFilterStatsResponse:
    """
    Get daily filter statistics for time-series charts.

    Returns dict mapping date (YYYYMMDD) to daily stats.
    """
    logger.info(f"Admin {admin.id} ({admin.email}) viewing daily filter stats for {days} days")

    from app.services.filter_stats_service import get_filter_stats_service

    stats_service = get_filter_stats_service()
    daily_stats = await stats_service.get_stats_range(days)

    # Convert to list format with dates for easier frontend consumption
    result = []
    for date, stats in sorted(daily_stats.items(), reverse=True):
        result.append({
            "date": date,
            "initial_useful": stats.get("initial_useful", 0),
            "initial_uncertain": stats.get("initial_uncertain", 0),
            "initial_skip": stats.get("initial_skip", 0),
            "fine_keep": stats.get("fine_keep", 0),
            "fine_delete": stats.get("fine_delete", 0),
            "filter_error": stats.get("filter_error", 0),
            "embedding_success": stats.get("embedding_success", 0),
            "embedding_error": stats.get("embedding_error", 0),
            "initial_input_tokens": stats.get("initial_input_tokens", 0),
            "initial_output_tokens": stats.get("initial_output_tokens", 0),
            "deep_input_tokens": stats.get("deep_input_tokens", 0),
            "deep_output_tokens": stats.get("deep_output_tokens", 0),
        })

    return DailyFilterStatsResponse(days=days, data=result)


@router.post(
    "/news/trigger-monitor",
    summary="Trigger news monitor task",
    description="Manually trigger the news monitoring pipeline (fetch + filter + process).",
)
async def trigger_news_monitor(
    admin: User = Depends(require_admin),
):
    """Manually trigger the news monitor Celery task."""
    logger.info(f"Admin {admin.id} ({admin.email}) manually triggering news monitor")

    from worker.tasks.news_monitor import monitor_news
    task = monitor_news.delay()

    return {"message": "News monitor task triggered", "task_id": str(task.id)}


@router.get(
    "/news/monitor-status",
    summary="Get news monitor status",
    description="Get current news monitor execution status, progress, and schedule info.",
)
async def get_monitor_status(
    admin: User = Depends(require_admin),
):
    """Get news monitor task progress and schedule status."""
    import json
    from datetime import timedelta

    redis = await get_redis()

    # Get current status
    status = await redis.get("news:monitor:status")
    status = status if status else "idle"

    # Get progress (if running)
    progress = None
    progress_raw = await redis.get("news:monitor:progress")
    if progress_raw:
        try:
            progress = json.loads(progress_raw)
        except Exception:
            pass

    # Get last run info
    last_run = None
    last_run_raw = await redis.get("news:monitor:last_run")
    if last_run_raw:
        try:
            last_run = json.loads(last_run_raw)
        except Exception:
            pass

    # Calculate next run time (every 15 minutes from last run)
    next_run_at = None
    if last_run and last_run.get("finished_at"):
        try:
            finished = datetime.fromisoformat(last_run["finished_at"])
            next_run_at = (finished + timedelta(minutes=15)).isoformat()
        except Exception:
            pass

    return {
        "status": status,
        "progress": progress,
        "last_run": last_run,
        "next_run_at": next_run_at,
    }


@router.get(
    "/news/layer15-stats",
    response_model=Layer15StatsResponse,
    summary="Get Layer 1.5 content fetch and cleaning statistics",
    description="Get content fetching, image extraction, and LLM cleaning stats from pipeline events.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_layer15_stats(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=30, description="Number of days to aggregate"),
) -> Layer15StatsResponse:
    """
    Get Layer 1.5 content fetch and cleaning statistics.

    Queries pipeline_events for fetch node and content_cleaning node metrics.
    """
    logger.info("Admin %d viewing Layer 1.5 stats for %d days", admin.id, days)

    stats = await PipelineTraceService.get_layer15_stats(db, days)

    return Layer15StatsResponse(
        period_days=stats["period_days"],
        fetch=Layer15FetchStats(**stats["fetch"]),
        provider_distribution=[
            Layer15ProviderDistribution(**p) for p in stats["provider_distribution"]
        ],
        cleaning=Layer15CleaningStats(**stats["cleaning"]),
    )


@router.get(
    "/news/news-pipeline-stats",
    response_model=NewsPipelineStatsResponse,
    summary="Get news pipeline multi-agent analysis statistics",
    description="Get combined routing, token, scoring, cache, and latency stats for the news pipeline.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_news_pipeline_stats(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=30, description="Number of days to aggregate"),
) -> NewsPipelineStatsResponse:
    """
    Get news pipeline multi-agent analysis statistics.

    Combines:
    - Redis counters: routing decisions + token usage per stage
    - DB pipeline_events: score distribution, cache stats, node latency
    """
    logger.info("Admin %d viewing news pipeline stats for %d days", admin.id, days)

    # 1. Get Redis-based routing/token stats
    from app.services.filter_stats_service import get_filter_stats_service

    stats_service = get_filter_stats_service()
    redis_stats = await stats_service.get_phase2_stats(days)

    routing = NewsPipelineRoutingStats(
        total=redis_stats["routing"]["total"],
        full_analysis=redis_stats["routing"]["full_analysis"],
        lightweight=redis_stats["routing"]["lightweight"],
        critical_events=redis_stats["routing"]["critical_events"],
        scoring_errors=redis_stats["routing"]["scoring_errors"],
    )

    tokens_data = redis_stats["tokens"]
    per_agent_raw = tokens_data.get("per_agent")
    per_agent = (
        {name: NewsPipelineTokenStage(**v) for name, v in per_agent_raw.items()}
        if per_agent_raw else None
    )
    tokens = NewsPipelineTokenStats(
        multi_agent=NewsPipelineTokenStage(**tokens_data["multi_agent"]),
        lightweight=NewsPipelineTokenStage(**tokens_data["lightweight"]),
        total=NewsPipelineTokenStage(**tokens_data["total"]),
        per_agent=per_agent,
    )

    # 2. Get DB-based pipeline event stats
    pipeline_stats = await PipelineTraceService.get_news_pipeline_stats(db, days)

    score_distribution = [
        ScoreDistributionBucket(**b)
        for b in pipeline_stats["score_distribution"]
    ]

    cache_stats = NewsPipelineCacheStats(**pipeline_stats["cache_stats"])

    node_latency = [
        NewsPipelineNodeLatency(**n)
        for n in pipeline_stats["node_latency"]
    ]

    return NewsPipelineStatsResponse(
        period_days=days,
        routing=routing,
        tokens=tokens,
        score_distribution=score_distribution,
        cache_stats=cache_stats,
        node_latency=node_latency,
    )


# ============== Pipeline Tracing Endpoints ==============


@router.get(
    "/pipeline/article/{news_id}",
    response_model=ArticleTimelineResponse,
    summary="Get article pipeline timeline",
    description="Get the full pipeline execution timeline for a specific article.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def get_article_timeline(
    news_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get pipeline timeline for a single article."""
    logger.info("Admin %d viewing pipeline timeline for article %s", admin.id, news_id)

    events = await PipelineTraceService.get_article_timeline(db, news_id)

    # Try to get article title and symbol from News table
    title = None
    symbol = None
    try:
        result = await db.execute(
            select(News.title, News.symbol).where(News.id == news_id)
        )
        row = result.first()
        if row:
            title = row.title
            symbol = row.symbol
    except Exception:
        pass  # News may have been deleted

    # Calculate total duration
    total_duration_ms = None
    if events:
        durations = [e.duration_ms for e in events if e.duration_ms is not None]
        if durations:
            total_duration_ms = round(sum(durations), 1)

    return ArticleTimelineResponse(
        news_id=news_id,
        title=title,
        symbol=symbol,
        events=[
            PipelineEventResponse(
                id=e.id,
                news_id=e.news_id,
                layer=e.layer,
                node=e.node,
                status=e.status,
                duration_ms=e.duration_ms,
                metadata=e.metadata_,
                error=e.error,
                created_at=e.created_at,
            )
            for e in events
        ],
        total_duration_ms=total_duration_ms,
    )


@router.get(
    "/pipeline/stats",
    response_model=PipelineStatsResponse,
    summary="Get pipeline aggregate statistics",
    description="Get aggregate statistics for pipeline nodes over a time period.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_pipeline_stats(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=30, description="Number of days to aggregate"),
):
    """Get aggregate pipeline statistics."""
    logger.info("Admin %d viewing pipeline stats for %d days", admin.id, days)

    stats = await PipelineTraceService.get_aggregate_stats(db, days)

    return PipelineStatsResponse(
        period_days=stats["period_days"],
        nodes=[
            NodeStatsResponse(**node_data)
            for node_data in stats["nodes"]
        ],
    )


@router.get(
    "/pipeline/events",
    response_model=PipelineEventSearchResponse,
    summary="Search pipeline events",
    description="Search and filter pipeline events with pagination.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def search_pipeline_events(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    layer: Optional[str] = Query(None, description="Filter by layer (1, 1.5, 2)"),
    node: Optional[str] = Query(None, description="Filter by node name"),
    status: Optional[str] = Query(None, description="Filter by status (success, error, skip)"),
    days: int = Query(1, ge=1, le=30, description="Time window in days"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """Search pipeline events with optional filters."""
    logger.info(
        "Admin %d searching pipeline events: layer=%s, node=%s, status=%s, days=%d",
        admin.id, layer, node, status, days,
    )

    events, total = await PipelineTraceService.search_events(
        db, layer=layer, node=node, status=status,
        days=days, limit=limit, offset=offset,
    )

    return PipelineEventSearchResponse(
        events=[
            PipelineEventResponse(
                id=e.id,
                news_id=e.news_id,
                layer=e.layer,
                node=e.node,
                status=e.status,
                duration_ms=e.duration_ms,
                metadata=e.metadata_,
                error=e.error,
                created_at=e.created_at,
            )
            for e in events
        ],
        total=total,
    )


# ============== Source Statistics Endpoints ==============


@router.get(
    "/news/source-stats",
    response_model=SourceStatsResponse,
    summary="Get news source quality statistics",
    description="Get per-source article quality metrics aggregated over a time period.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_source_stats(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=30, description="Number of days to aggregate"),
):
    """Get per-source quality statistics from the News table."""
    logger.info("Admin %d viewing source stats for %d days", admin.id, days)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Note: related_entities is JSON (not JSONB) — use json_array_length / json_typeof
    # content_status values are lowercase: pending, fetched, embedded, failed, blocked, deleted
    query = text("""
        SELECT
            source,
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE filter_status = 'useful') AS initial_useful,
            COUNT(*) FILTER (WHERE filter_status = 'uncertain') AS initial_uncertain,
            COUNT(*) FILTER (WHERE filter_status = 'keep') AS fine_keep,
            COUNT(*) FILTER (WHERE filter_status = 'delete') AS fine_delete,
            COUNT(*) FILTER (WHERE content_status = 'embedded') AS embedded,
            COUNT(*) FILTER (WHERE content_status IN ('failed', 'blocked')) AS fetch_failed,
            AVG(
                CASE
                    WHEN related_entities IS NOT NULL
                         AND related_entities::text != 'null'
                         AND json_typeof(related_entities) = 'array'
                    THEN json_array_length(related_entities)
                    ELSE 0
                END
            ) AS avg_entity_count,
            COUNT(*) FILTER (WHERE sentiment_tag = 'bullish') AS bullish,
            COUNT(*) FILTER (WHERE sentiment_tag = 'bearish') AS bearish,
            COUNT(*) FILTER (WHERE sentiment_tag = 'neutral') AS neutral
        FROM news
        WHERE created_at >= :cutoff
        GROUP BY source
        ORDER BY COUNT(*) DESC
    """)

    try:
        result = await db.execute(query, {"cutoff": cutoff})
        rows = result.fetchall()
    except Exception as e:
        logger.error("Failed to query source stats: %s", e)
        return SourceStatsResponse(period_days=days, sources=[], total_sources=0)

    sources = []
    for row in rows:
        total = row.total
        fine_total = row.fine_keep + row.fine_delete

        # Sentiment distribution (only include if any sentiment data exists)
        bullish = row.bullish
        bearish = row.bearish
        neutral = row.neutral
        sentiment_total = bullish + bearish + neutral
        sentiment_dist = (
            {"bullish": bullish, "bearish": bearish, "neutral": neutral}
            if sentiment_total > 0 else None
        )

        sources.append(SourceStatsItemResponse(
            source=row.source,
            total=total,
            initial_useful=row.initial_useful,
            initial_uncertain=row.initial_uncertain,
            fine_keep=row.fine_keep,
            fine_delete=row.fine_delete,
            embedded=row.embedded,
            fetch_failed=row.fetch_failed,
            avg_entity_count=round(row.avg_entity_count, 1) if row.avg_entity_count is not None else None,
            sentiment_distribution=sentiment_dist,
            keep_rate=round(row.embedded / total * 100, 1) if total > 0 else None,
            fetch_rate=round(fine_total / total * 100, 1) if total > 0 else None,
        ))

    return SourceStatsResponse(
        period_days=days,
        sources=sources,
        total_sources=len(sources),
    )
