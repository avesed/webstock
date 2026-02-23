"""Service for persisting and querying analysis sessions."""

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis_session import AnalysisSession

logger = logging.getLogger(__name__)


class AnalysisSessionService:
    """CRUD operations for analysis sessions."""

    @staticmethod
    async def create_session(
        db: AsyncSession,
        user_id: int,
        symbol: str,
        market: str,
        language: str,
    ) -> AnalysisSession:
        session = AnalysisSession(
            user_id=user_id,
            symbol=symbol,
            market=market,
            language=language,
            status="running",
        )
        db.add(session)
        await db.flush()
        return session

    @staticmethod
    async def complete_session(
        db: AsyncSession,
        session_id: uuid.UUID,
        agent_results: list,
        synthesis_content: str,
        clarification_rounds: int = 0,
        total_tokens: Optional[int] = None,
        total_latency_ms: Optional[int] = None,
    ) -> None:
        result = await db.execute(
            select(AnalysisSession).where(AnalysisSession.id == session_id)
        )
        session = result.scalar_one_or_none()
        if not session:
            logger.warning("分析会话: 完成时未找到会话 %s", str(session_id)[:8])
            return
        session.status = "completed"
        session.agent_results = agent_results
        session.synthesis_content = synthesis_content
        session.clarification_rounds = clarification_rounds
        session.total_tokens = total_tokens
        session.total_latency_ms = total_latency_ms
        session.completed_at = datetime.now(timezone.utc)

    @staticmethod
    async def fail_session(
        db: AsyncSession,
        session_id: uuid.UUID,
        error: str,
    ) -> None:
        result = await db.execute(
            select(AnalysisSession).where(AnalysisSession.id == session_id)
        )
        session = result.scalar_one_or_none()
        if not session:
            return
        session.status = "failed"
        session.error = error[:2000] if error else None
        session.completed_at = datetime.now(timezone.utc)

    @staticmethod
    async def get_session(
        db: AsyncSession,
        session_id: uuid.UUID,
        user_id: int,
    ) -> Optional[AnalysisSession]:
        result = await db.execute(
            select(AnalysisSession).where(
                AnalysisSession.id == session_id,
                AnalysisSession.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_sessions(
        db: AsyncSession,
        user_id: int,
        symbol: str,
        limit: int = 10,
    ) -> List[AnalysisSession]:
        result = await db.execute(
            select(AnalysisSession)
            .where(
                AnalysisSession.user_id == user_id,
                AnalysisSession.symbol == symbol,
                AnalysisSession.status != "running",
            )
            .order_by(AnalysisSession.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def delete_session(
        db: AsyncSession,
        session_id: uuid.UUID,
        user_id: int,
    ) -> bool:
        """Delete an analysis session. Returns True if deleted."""
        result = await db.execute(
            delete(AnalysisSession).where(
                AnalysisSession.id == session_id,
                AnalysisSession.user_id == user_id,
            )
        )
        await db.flush()
        return result.rowcount > 0
