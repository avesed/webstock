"""Service layer for the Discussion Group feature.

Manages discussion sessions, streams discussion workflow, persists results,
and creates post-discussion chat conversations.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.discussion import DiscussionMessage as DiscussionMessageModel
from app.models.discussion import DiscussionSession

logger = logging.getLogger(__name__)


class DiscussionService:
    """Service for managing multi-agent discussion sessions."""

    async def find_active_session(
        self,
        db: AsyncSession,
        user_id: int,
        symbol: str,
    ) -> Optional[DiscussionSession]:
        """Find an existing active (pending/discussing) session for a user+symbol pair.

        Returns the most recent active session if one exists, None otherwise.
        """
        result = await db.execute(
            select(DiscussionSession)
            .where(
                DiscussionSession.user_id == user_id,
                DiscussionSession.symbol == symbol,
                DiscussionSession.status.in_(["pending", "discussing"]),
            )
            .order_by(DiscussionSession.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_active_sessions(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> int:
        """Count active discussion sessions for a user.

        Includes sessions in pending, discussing, and synthesizing statuses.
        """
        result = await db.execute(
            select(func.count())
            .select_from(DiscussionSession)
            .where(
                DiscussionSession.user_id == user_id,
                DiscussionSession.status.in_(["pending", "discussing", "synthesizing"]),
            )
        )
        return result.scalar_one()

    async def start_discussion(
        self,
        db: AsyncSession,
        user_id: int,
        symbol: str,
        market: str,
        language: str = "zh",
    ) -> DiscussionSession:
        """Create a new discussion session.

        Reads max_rounds from system_settings.
        """
        from app.services.settings_service import get_settings_service

        settings_service = get_settings_service()
        system = await settings_service.get_system_settings(db)

        max_rounds = getattr(system, "discussion_max_rounds", 3) or 3

        session = DiscussionSession(
            id=uuid.uuid4(),
            user_id=user_id,
            symbol=symbol,
            market=market,
            language=language,
            status="pending",
            max_rounds=max_rounds,
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)

        logger.info(
            "讨论组: 创建会话 session=%s symbol=%s user=%d max_rounds=%d",
            session.id, symbol, user_id, max_rounds,
        )
        return session

    async def _persist_message(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
        msg_data: Dict[str, Any],
    ) -> None:
        """Persist a single discussion message to DB immediately.

        Uses a shared DB session from the caller to avoid connection pool
        exhaustion (NullPool opens a fresh connection per context manager).
        Each message is committed individually so partial results survive
        client disconnects.
        """
        try:
            msg = DiscussionMessageModel(
                id=uuid.uuid4(),
                session_id=session_id,
                round=msg_data["round"],
                agent_type=msg_data["agent_type"],
                content=msg_data["content"],
                latency_ms=msg_data.get("latency_ms"),
            )
            db.add(msg)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.warning(
                "讨论组: 消息持久化失败 session=%s agent=%s",
                session_id, msg_data.get("agent_type", "unknown"),
                exc_info=True,
            )

    async def stream_discussion(
        self,
        session_id: uuid.UUID,
        user_id: int,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream the discussion workflow, persisting messages to DB.

        Messages are persisted incrementally as each agent completes, so
        that partial results survive client disconnects (e.g. mobile users
        navigating away mid-stream).

        Uses a single shared NullPool DB session for the entire stream to
        avoid connection exhaustion (previously each _persist_message opened
        its own connection, ~20 per discussion).
        """
        from app.agents.langgraph.discussion_workflow import stream_discussion
        from app.db.task_session import get_task_session

        # Single DB session for the entire stream lifecycle
        async with get_task_session() as db:
            # Load session
            result = await db.execute(
                select(DiscussionSession).where(
                    DiscussionSession.id == session_id,
                    DiscussionSession.user_id == user_id,
                )
            )
            session = result.scalar_one_or_none()
            if not session:
                yield {"type": "error", "data": {"message": "Session not found"}}
                return

            symbol = session.symbol
            market = session.market
            language = session.language
            max_rounds = session.max_rounds

            # Update status to discussing
            session.status = "discussing"
            await db.commit()

            total_tokens = 0
            total_latency_ms = 0
            current_round = 0
            compact_context = ""
            message_count = 0

            try:
                async for event in stream_discussion(
                    symbol=symbol,
                    market=market,
                    language=language,
                    session_id=str(session_id),
                    max_rounds=max_rounds,
                ):
                    event_type = event.get("type", "")

                    # Persist completed agent messages immediately
                    if event_type in ("agent_statement_complete", "agent_response_complete"):
                        data = event.get("data", {})
                        msg_round = data.get("round", current_round)
                        if event_type == "agent_statement_complete":
                            msg_round = 0
                        await self._persist_message(db, session_id, {
                            "round": msg_round,
                            "agent_type": data.get("agent_type", "unknown"),
                            "content": data.get("content", ""),
                            "latency_ms": data.get("latency_ms", 0),
                        })
                        message_count += 1
                        total_latency_ms += data.get("latency_ms", 0)

                    elif event_type == "moderator_guidance":
                        data = event.get("data", {})
                        await self._persist_message(db, session_id, {
                            "round": current_round,
                            "agent_type": "moderator",
                            "content": data.get("content", ""),
                        })
                        message_count += 1

                    elif event_type == "debate_round_start":
                        current_round += 1

                    elif event_type == "synthesis_complete":
                        data = event.get("data", {})
                        # Capture compact_context from synthesis node
                        compact = data.get("compact_context", "")
                        if compact:
                            compact_context = compact
                        await self._persist_message(db, session_id, {
                            "round": -1,
                            "agent_type": "synthesis",
                            "content": data.get("content", ""),
                        })
                        message_count += 1

                    elif event_type == "discussion_complete":
                        data = event.get("data", {})
                        current_round = data.get("total_rounds", current_round)
                        total_tokens = data.get("total_tokens", total_tokens)
                        # Use workflow-generated compact_context if available
                        wf_compact = data.get("compact_context", "")
                        if wf_compact:
                            compact_context = wf_compact

                        # Update session metadata (messages already persisted)
                        # Expire cached ORM objects so we get a fresh read
                        db.expire_all()
                        result = await db.execute(
                            select(DiscussionSession).where(
                                DiscussionSession.id == session_id
                            )
                        )
                        sess = result.scalar_one_or_none()
                        if sess:
                            sess.status = "completed"
                            sess.discussion_rounds = current_round
                            sess.synthesis_report = data.get("synthesis_report", "")
                            sess.total_tokens = total_tokens
                            sess.total_latency_ms = total_latency_ms
                            sess.completed_at = datetime.now(timezone.utc)
                            sess.compact_context = compact_context or (
                                data.get("synthesis_report", "")[:4000]
                            )

                        await db.commit()
                        logger.info(
                            "讨论组: 会话完成 session=%s rounds=%d msgs=%d tokens=%d",
                            session_id, current_round, message_count, total_tokens,
                        )

                    elif event_type == "error":
                        # Mark session as failed
                        db.expire_all()
                        result = await db.execute(
                            select(DiscussionSession).where(
                                DiscussionSession.id == session_id
                            )
                        )
                        sess = result.scalar_one_or_none()
                        if sess:
                            sess.status = "failed"
                            sess.error = event.get("data", {}).get("message", "Unknown error")
                            await db.commit()

                    # Yield all events to the API layer
                    yield event

            except Exception as e:
                logger.exception("讨论组: 流式讨论服务错误 session=%s: %s", session_id, e)
                # Mark session as failed using the shared session
                try:
                    await db.rollback()
                    db.expire_all()
                    result = await db.execute(
                        select(DiscussionSession).where(
                            DiscussionSession.id == session_id
                        )
                    )
                    sess = result.scalar_one_or_none()
                    if sess:
                        sess.status = "failed"
                        sess.error = str(e)[:500]
                        await db.commit()
                except Exception:
                    logger.error("讨论组: 无法标记会话失败 session=%s", session_id, exc_info=True)

                yield {"type": "error", "data": {"message": f"Discussion service error: {str(e)[:200]}"}}

    async def mark_orphaned_session(self, session_id: uuid.UUID) -> None:
        """Mark a session as failed if it's still in 'discussing' or 'synthesizing' status.

        Called on client disconnect to prevent permanently stuck sessions.
        """
        from app.db.task_session import get_task_session

        try:
            async with get_task_session() as db:
                result = await db.execute(
                    select(DiscussionSession).where(
                        DiscussionSession.id == session_id,
                        DiscussionSession.status.in_(["discussing", "synthesizing"]),
                    )
                )
                session = result.scalar_one_or_none()
                if session:
                    session.status = "failed"
                    session.error = "Client disconnected"
                    await db.commit()
                    logger.info("讨论组: 标记孤立会话为失败 session=%s", session_id)
        except Exception:
            logger.debug("讨论组: mark_orphaned_session 失败 session=%s", session_id, exc_info=True)

    async def get_session(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
        user_id: int,
    ) -> Optional[DiscussionSession]:
        """Get a session with ownership verification."""
        result = await db.execute(
            select(DiscussionSession).where(
                DiscussionSession.id == session_id,
                DiscussionSession.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_session_detail(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
        user_id: int,
    ) -> Optional[DiscussionSession]:
        """Get a session with all messages loaded."""
        result = await db.execute(
            select(DiscussionSession)
            .options(selectinload(DiscussionSession.messages))
            .where(
                DiscussionSession.id == session_id,
                DiscussionSession.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_sessions(
        self,
        db: AsyncSession,
        user_id: int,
        symbol: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[DiscussionSession]:
        """List discussion sessions for a user, optionally filtered by symbol."""
        query = (
            select(DiscussionSession)
            .where(DiscussionSession.user_id == user_id)
            .order_by(DiscussionSession.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if symbol:
            query = query.where(DiscussionSession.symbol == symbol)

        result = await db.execute(query)
        return list(result.scalars().all())

    async def delete_session(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
        user_id: int,
    ) -> bool:
        """Delete a discussion session. Messages cascade-deleted via FK.

        Returns True if deleted.
        """
        result = await db.execute(
            delete(DiscussionSession).where(
                DiscussionSession.id == session_id,
                DiscussionSession.user_id == user_id,
            )
        )
        await db.flush()
        return result.rowcount > 0

    async def create_discussion_conversation(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
        user_id: int,
    ) -> Optional[uuid.UUID]:
        """Create a chat conversation linked to a completed discussion session.

        Returns the new conversation_id, or None if session not found/not completed.
        """
        from app.models.chat import Conversation

        session = await self.get_session(db, session_id, user_id)
        if not session or session.status != "completed":
            return None

        conversation = Conversation(
            id=uuid.uuid4(),
            user_id=user_id,
            title=f"Discussion: {session.symbol}",
            symbol=session.symbol,
            type="discussion",
            discussion_session_id=session_id,
        )
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)

        logger.info(
            "讨论组: 创建讨论对话 conversation=%s session=%s",
            conversation.id, session_id,
        )
        return conversation.id


# Singleton
_discussion_service: Optional[DiscussionService] = None


def get_discussion_service() -> DiscussionService:
    """Get singleton DiscussionService instance."""
    global _discussion_service
    if _discussion_service is None:
        _discussion_service = DiscussionService()
    return _discussion_service
