"""Pipeline tracing service for news processing observability.

Provides event recording, querying, and aggregate statistics for
the 3-layer news processing pipeline.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import select, delete, func, and_, text, case, cast, Float
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_event import PipelineEvent

logger = logging.getLogger(__name__)


class PipelineTraceService:
    """
    Service for recording and querying pipeline execution events.

    Supports two usage patterns:
    - Direct recording (Layer 1 / 1.5): call record_event() with caller's session
    - Batch recording (Layer 2 / LangGraph): accumulate dicts in state,
      then flush via record_events_batch() in update_db_node
    """

    @staticmethod
    def make_event(
        news_id: str,
        layer: str,
        node: str,
        status: str,
        duration_ms: Optional[float] = None,
        metadata: Optional[dict] = None,
        error: Optional[str] = None,
        cache_metadata: Optional[dict] = None,
    ) -> dict:
        """
        Build event dict for LangGraph state accumulation.

        Returns a plain dict suitable for appending to state['trace_events'].
        The dict is later consumed by record_events_batch().
        """
        return {
            "id": str(uuid4()),
            "news_id": news_id,
            "layer": layer,
            "node": node,
            "status": status,
            "duration_ms": duration_ms,
            "metadata_": metadata,
            "error": error,
            "cache_metadata": cache_metadata,
            "created_at": datetime.now(timezone.utc),
        }

    @staticmethod
    async def record_event(
        db: AsyncSession,
        news_id: str,
        layer: str,
        node: str,
        status: str,
        duration_ms: Optional[float] = None,
        metadata: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> None:
        """
        Record a single pipeline event using the caller's session.

        Intended for Layer 1 and Layer 1.5 where each task has its own
        DB session. The caller is responsible for committing.
        """
        event = PipelineEvent(
            news_id=news_id,
            layer=layer,
            node=node,
            status=status,
            duration_ms=duration_ms,
            metadata_=metadata,
            error=error,
        )
        db.add(event)

    @staticmethod
    async def record_events_batch(
        db: AsyncSession,
        events: List[dict],
    ) -> None:
        """
        Batch insert pipeline events from LangGraph state.

        Intended for Layer 2 update_db_node, which flushes all accumulated
        trace events in a single transaction.
        """
        for event_data in events:
            event = PipelineEvent(
                id=event_data.get("id", str(uuid4())),
                news_id=event_data["news_id"],
                layer=event_data["layer"],
                node=event_data["node"],
                status=event_data["status"],
                duration_ms=event_data.get("duration_ms"),
                metadata_=event_data.get("metadata_"),
                error=event_data.get("error"),
                cache_metadata=event_data.get("cache_metadata"),
                created_at=event_data.get("created_at", datetime.now(timezone.utc)),
            )
            db.add(event)

    @staticmethod
    async def get_article_timeline(
        db: AsyncSession,
        news_id: str,
    ) -> List[PipelineEvent]:
        """
        Get all pipeline events for a single article, ordered chronologically.

        Returns the full execution trace from discovery through embedding.
        """
        result = await db.execute(
            select(PipelineEvent)
            .where(PipelineEvent.news_id == news_id)
            .order_by(PipelineEvent.created_at.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_aggregate_stats(
        db: AsyncSession,
        days: int = 7,
    ) -> Dict:
        """
        Compute per-layer/node aggregate statistics over a time window.

        Uses PostgreSQL percentile_cont() for p50/p95 latency calculations.

        Returns:
            {
                "period_days": int,
                "nodes": [
                    {
                        "layer": str,
                        "node": str,
                        "count": int,
                        "success_count": int,
                        "error_count": int,
                        "avg_ms": float | None,
                        "p50_ms": float | None,
                        "p95_ms": float | None,
                        "max_ms": float | None,
                    }
                ]
            }
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)

        stmt = (
            select(
                PipelineEvent.layer,
                PipelineEvent.node,
                func.count().label("count"),
                func.count()
                .filter(PipelineEvent.status == "success")
                .label("success_count"),
                func.count()
                .filter(PipelineEvent.status == "error")
                .label("error_count"),
                func.avg(PipelineEvent.duration_ms).label("avg_ms"),
                func.percentile_cont(0.5)
                .within_group(PipelineEvent.duration_ms)
                .label("p50_ms"),
                func.percentile_cont(0.95)
                .within_group(PipelineEvent.duration_ms)
                .label("p95_ms"),
                func.max(PipelineEvent.duration_ms).label("max_ms"),
            )
            .where(PipelineEvent.created_at >= since)
            .group_by(PipelineEvent.layer, PipelineEvent.node)
            .order_by(PipelineEvent.layer, PipelineEvent.node)
        )

        result = await db.execute(stmt)
        rows = result.all()

        nodes = []
        for row in rows:
            nodes.append({
                "layer": row.layer,
                "node": row.node,
                "count": row.count,
                "success_count": row.success_count,
                "error_count": row.error_count,
                "avg_ms": round(row.avg_ms, 1) if row.avg_ms is not None else None,
                "p50_ms": round(row.p50_ms, 1) if row.p50_ms is not None else None,
                "p95_ms": round(row.p95_ms, 1) if row.p95_ms is not None else None,
                "max_ms": round(row.max_ms, 1) if row.max_ms is not None else None,
            })

        return {
            "period_days": days,
            "nodes": nodes,
        }

    @staticmethod
    async def get_layer15_stats(
        db: AsyncSession,
        days: int = 7,
    ) -> Dict:
        """
        Get Layer 1.5 aggregate stats from pipeline_events.

        Returns fetch overview, image extraction stats, content cleaning stats,
        and provider distribution.
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)

        # --- Fetch node stats ---
        fetch_result = await db.execute(
            text("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status = 'success') AS success,
                    COUNT(*) FILTER (WHERE status = 'error') AS errors,
                    AVG(duration_ms) AS avg_ms,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
                    AVG(
                        CASE WHEN metadata->>'images_found' IS NOT NULL
                             THEN (metadata->>'images_found')::int ELSE 0 END
                    ) FILTER (WHERE status = 'success') AS avg_images_found,
                    AVG(
                        CASE WHEN metadata->>'images_downloaded' IS NOT NULL
                             THEN (metadata->>'images_downloaded')::int ELSE 0 END
                    ) FILTER (WHERE status = 'success') AS avg_images_downloaded,
                    COUNT(*) FILTER (
                        WHERE status = 'success'
                        AND metadata->>'images_downloaded' IS NOT NULL
                        AND (metadata->>'images_downloaded')::int > 0
                    ) AS articles_with_images
                FROM pipeline_events
                WHERE layer = '1.5' AND node = 'fetch'
                    AND created_at >= :since
            """),
            {"since": since},
        )
        fetch_row = fetch_result.first()

        fetch_stats = {
            "total": fetch_row.total if fetch_row else 0,
            "success": fetch_row.success if fetch_row else 0,
            "errors": fetch_row.errors if fetch_row else 0,
            "avg_ms": round(fetch_row.avg_ms, 1) if fetch_row and fetch_row.avg_ms else None,
            "p50_ms": round(fetch_row.p50_ms, 1) if fetch_row and fetch_row.p50_ms else None,
            "p95_ms": round(fetch_row.p95_ms, 1) if fetch_row and fetch_row.p95_ms else None,
            "avg_images_found": (
                round(fetch_row.avg_images_found, 1)
                if fetch_row and fetch_row.avg_images_found else 0
            ),
            "avg_images_downloaded": (
                round(fetch_row.avg_images_downloaded, 1)
                if fetch_row and fetch_row.avg_images_downloaded else 0
            ),
            "articles_with_images": fetch_row.articles_with_images if fetch_row else 0,
        }

        # --- Provider distribution ---
        provider_result = await db.execute(
            text("""
                SELECT
                    COALESCE(metadata->>'provider', 'unknown') AS provider,
                    COUNT(*) AS count
                FROM pipeline_events
                WHERE layer = '1.5' AND node = 'fetch'
                    AND status = 'success'
                    AND created_at >= :since
                GROUP BY provider
                ORDER BY count DESC
            """),
            {"since": since},
        )
        provider_distribution = [
            {"provider": row.provider, "count": row.count}
            for row in provider_result.all()
        ]

        # --- Content cleaning stats ---
        cleaning_result = await db.execute(
            text("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status = 'success') AS success,
                    COUNT(*) FILTER (WHERE status = 'error') AS errors,
                    AVG(duration_ms) AS avg_ms,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
                    AVG(
                        CASE WHEN metadata->>'original_length' IS NOT NULL
                             AND (metadata->>'original_length')::int > 0
                             THEN (metadata->>'cleaned_length')::float
                                  / (metadata->>'original_length')::float
                             ELSE NULL END
                    ) FILTER (WHERE status = 'success') AS avg_retention_rate,
                    COUNT(*) FILTER (
                        WHERE status = 'success'
                        AND (metadata->>'has_visual_data')::boolean = true
                    ) AS articles_with_visual_data,
                    AVG(
                        CASE WHEN metadata->>'image_count' IS NOT NULL
                             THEN (metadata->>'image_count')::int ELSE 0 END
                    ) FILTER (WHERE status = 'success') AS avg_image_count,
                    AVG(
                        CASE WHEN metadata->>'image_insights_length' IS NOT NULL
                             THEN (metadata->>'image_insights_length')::int ELSE 0 END
                    ) FILTER (WHERE status = 'success') AS avg_insights_length
                FROM pipeline_events
                WHERE layer = '1.5' AND node = 'content_cleaning'
                    AND created_at >= :since
            """),
            {"since": since},
        )
        cleaning_row = cleaning_result.first()

        cleaning_stats = {
            "total": cleaning_row.total if cleaning_row else 0,
            "success": cleaning_row.success if cleaning_row else 0,
            "errors": cleaning_row.errors if cleaning_row else 0,
            "avg_ms": round(cleaning_row.avg_ms, 1) if cleaning_row and cleaning_row.avg_ms else None,
            "p50_ms": round(cleaning_row.p50_ms, 1) if cleaning_row and cleaning_row.p50_ms else None,
            "p95_ms": round(cleaning_row.p95_ms, 1) if cleaning_row and cleaning_row.p95_ms else None,
            "avg_retention_rate": (
                round(cleaning_row.avg_retention_rate, 3)
                if cleaning_row and cleaning_row.avg_retention_rate is not None
                else None
            ),
            "articles_with_visual_data": cleaning_row.articles_with_visual_data if cleaning_row else 0,
            "avg_image_count": (
                round(cleaning_row.avg_image_count, 1)
                if cleaning_row and cleaning_row.avg_image_count else 0
            ),
            "avg_insights_length": (
                round(cleaning_row.avg_insights_length, 0)
                if cleaning_row and cleaning_row.avg_insights_length else 0
            ),
        }

        return {
            "period_days": days,
            "fetch": fetch_stats,
            "provider_distribution": provider_distribution,
            "cleaning": cleaning_stats,
        }

    @staticmethod
    async def get_news_pipeline_stats(
        db: AsyncSession,
        days: int = 7,
    ) -> Dict:
        """
        Get news pipeline stats from pipeline_events metadata.

        Returns score distribution, cache stats, and per-node latency
        for pipeline nodes (score_and_route, multi_agent_analysis, lightweight_filter).
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)

        # Score distribution: support both old Phase 2 (layer=2, score 0-100)
        # and new Layer 1 (layer=1, score 0-300) events
        # Check which source has data and use appropriate buckets
        layer1_count_result = await db.execute(
            text("""
                SELECT COUNT(*) AS cnt FROM pipeline_events
                WHERE layer = '1' AND node = 'layer1_scoring'
                    AND status = 'success'
                    AND created_at >= :since
                    AND metadata IS NOT NULL
                    AND metadata->>'score' IS NOT NULL
            """),
            {"since": since},
        )
        has_layer1_events = (layer1_count_result.scalar() or 0) > 0

        if has_layer1_events:
            # New Layer 1 scoring: 0-300 buckets aligned with thresholds
            # decision field: full_analysis / lightweight / discard
            score_result = await db.execute(
                text("""
                    SELECT
                        CASE
                            WHEN (metadata->>'score')::int < 60 THEN '0-59'
                            WHEN (metadata->>'score')::int < 105 THEN '60-104'
                            WHEN (metadata->>'score')::int < 150 THEN '105-149'
                            WHEN (metadata->>'score')::int < 195 THEN '150-194'
                            ELSE '195-300'
                        END AS bucket,
                        COUNT(*) AS count,
                        COUNT(*) FILTER (
                            WHERE metadata->>'decision' = 'full_analysis'
                        ) AS full_analysis,
                        COUNT(*) FILTER (
                            WHERE metadata->>'decision' = 'lightweight'
                        ) AS lightweight,
                        COUNT(*) FILTER (
                            WHERE metadata->>'decision' = 'critical_event'
                        ) AS critical
                    FROM pipeline_events
                    WHERE layer = '1' AND node = 'layer1_scoring'
                        AND status = 'success'
                        AND created_at >= :since
                        AND metadata IS NOT NULL
                        AND metadata->>'score' IS NOT NULL
                    GROUP BY bucket
                    ORDER BY MIN((metadata->>'score')::int)
                """),
                {"since": since},
            )
        else:
            # Legacy Phase 2 scoring: 0-100 buckets
            score_result = await db.execute(
                text("""
                    SELECT
                        CASE
                            WHEN (metadata->>'score')::int < 20 THEN '0-19'
                            WHEN (metadata->>'score')::int < 40 THEN '20-39'
                            WHEN (metadata->>'score')::int < 60 THEN '40-59'
                            WHEN (metadata->>'score')::int < 80 THEN '60-79'
                            ELSE '80-100'
                        END AS bucket,
                        COUNT(*) AS count,
                        COUNT(*) FILTER (
                            WHERE metadata->>'processing_path' = 'full_analysis'
                        ) AS full_analysis,
                        COUNT(*) FILTER (
                            WHERE metadata->>'processing_path' = 'lightweight'
                        ) AS lightweight,
                        COUNT(*) FILTER (
                            WHERE (metadata->>'is_critical_event')::boolean = true
                        ) AS critical
                    FROM pipeline_events
                    WHERE layer = '2' AND node = 'score_and_route'
                        AND status = 'success'
                        AND created_at >= :since
                        AND metadata IS NOT NULL
                        AND metadata->>'score' IS NOT NULL
                    GROUP BY bucket
                    ORDER BY MIN((metadata->>'score')::int)
                """),
                {"since": since},
            )

        score_distribution = [
            {
                "bucket": row.bucket,
                "count": row.count,
                "full_analysis": row.full_analysis,
                "lightweight": row.lightweight,
                "critical": row.critical,
            }
            for row in score_result.all()
        ]

        # Cache stats from multi_agent_analysis events
        cache_result = await db.execute(
            text("""
                SELECT
                    COUNT(*) AS total,
                    AVG(
                        CASE WHEN cache_metadata->>'cache_hit_rate' IS NOT NULL
                             THEN (cache_metadata->>'cache_hit_rate')::float
                             ELSE NULL END
                    ) AS avg_cache_hit_rate,
                    SUM(
                        CASE WHEN cache_metadata IS NOT NULL
                             AND (cache_metadata->>'cache_hit_rate')::float > 0
                             THEN 1 ELSE 0 END
                    ) AS cache_hits,
                    SUM(
                        CASE WHEN cache_metadata->>'cached_tokens' IS NOT NULL
                             THEN (cache_metadata->>'cached_tokens')::int
                             ELSE 0 END
                    ) AS total_cached_tokens,
                    SUM(
                        CASE WHEN cache_metadata->>'prompt_tokens' IS NOT NULL
                             THEN (cache_metadata->>'prompt_tokens')::int
                             ELSE 0 END
                    ) AS total_prompt_tokens
                FROM pipeline_events
                WHERE layer = '2' AND node = 'multi_agent_analysis'
                    AND status = 'success'
                    AND created_at >= :since
            """),
            {"since": since},
        )
        cache_row = cache_result.first()
        cache_stats = {
            "total": cache_row.total if cache_row else 0,
            "avg_cache_hit_rate": (
                round(cache_row.avg_cache_hit_rate, 3)
                if cache_row and cache_row.avg_cache_hit_rate is not None
                else None
            ),
            "cache_hits": cache_row.cache_hits if cache_row else 0,
            "total_cached_tokens": cache_row.total_cached_tokens if cache_row else 0,
            "total_prompt_tokens": cache_row.total_prompt_tokens if cache_row else 0,
        }

        # Per-node latency for Phase 2 nodes
        phase2_nodes = ("score_and_route", "multi_agent_analysis", "lightweight_filter")
        latency_stmt = (
            select(
                PipelineEvent.node,
                func.count().label("count"),
                func.count()
                .filter(PipelineEvent.status == "success")
                .label("success"),
                func.count()
                .filter(PipelineEvent.status == "error")
                .label("errors"),
                func.avg(PipelineEvent.duration_ms).label("avg_ms"),
                func.percentile_cont(0.5)
                .within_group(PipelineEvent.duration_ms)
                .label("p50_ms"),
                func.percentile_cont(0.95)
                .within_group(PipelineEvent.duration_ms)
                .label("p95_ms"),
            )
            .where(
                PipelineEvent.created_at >= since,
                PipelineEvent.layer == "2",
                PipelineEvent.node.in_(phase2_nodes),
            )
            .group_by(PipelineEvent.node)
            .order_by(PipelineEvent.node)
        )
        latency_result = await db.execute(latency_stmt)
        node_latency = [
            {
                "node": row.node,
                "count": row.count,
                "success": row.success,
                "errors": row.errors,
                "avg_ms": round(row.avg_ms, 1) if row.avg_ms else None,
                "p50_ms": round(row.p50_ms, 1) if row.p50_ms else None,
                "p95_ms": round(row.p95_ms, 1) if row.p95_ms else None,
            }
            for row in latency_result.all()
        ]

        return {
            "score_distribution": score_distribution,
            "cache_stats": cache_stats,
            "node_latency": node_latency,
        }

    @staticmethod
    async def search_events(
        db: AsyncSession,
        layer: Optional[str] = None,
        node: Optional[str] = None,
        status: Optional[str] = None,
        days: int = 1,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[PipelineEvent], int]:
        """
        Search pipeline events with optional filters.

        Args:
            layer: Filter by pipeline layer (1, 1.5, 2)
            node: Filter by node name
            status: Filter by status (success, error, skip)
            days: Time window in days (default 1)
            limit: Max results to return
            offset: Pagination offset

        Returns:
            Tuple of (events list, total count matching filters)
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)
        conditions = [PipelineEvent.created_at >= since]

        if layer is not None:
            conditions.append(PipelineEvent.layer == layer)
        if node is not None:
            conditions.append(PipelineEvent.node == node)
        if status is not None:
            conditions.append(PipelineEvent.status == status)

        where_clause = and_(*conditions)

        # Total count
        count_result = await db.execute(
            select(func.count()).select_from(PipelineEvent).where(where_clause)
        )
        total = count_result.scalar_one()

        # Paginated results
        result = await db.execute(
            select(PipelineEvent)
            .where(where_clause)
            .order_by(PipelineEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        events = list(result.scalars().all())

        return events, total

    @staticmethod
    async def cleanup_old_events(
        db: AsyncSession,
        retention_days: int = 7,
    ) -> int:
        """
        Delete pipeline events older than the retention period.

        Args:
            retention_days: Number of days to retain (default 7)

        Returns:
            Number of deleted events
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

        result = await db.execute(
            delete(PipelineEvent).where(PipelineEvent.created_at < cutoff)
        )
        deleted = result.rowcount

        if deleted > 0:
            logger.info(
                "Cleaned up %d pipeline events older than %d days",
                deleted,
                retention_days,
            )

        return deleted
