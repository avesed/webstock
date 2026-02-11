"""Pipeline tracing service for news processing observability.

Provides event recording, querying, and aggregate statistics for
the 3-layer news processing pipeline.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import select, delete, func, and_
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
