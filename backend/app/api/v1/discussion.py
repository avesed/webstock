"""Discussion Group API endpoints.

Provides REST and SSE streaming endpoints for the multi-agent discussion feature.
"""

import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.analysis import apply_user_ai_config
from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user
from app.core.user_ai_config import current_user_ai_config
from app.db.database import get_db
from app.models.user import User
from app.schemas.discussion import (
    CreateDiscussionChatResponse,
    DiscussionMessageResponse,
    DiscussionSessionDetailResponse,
    DiscussionSessionResponse,
    StartDiscussionRequest,
)
from app.services.discussion_service import get_discussion_service
from app.services.stock_types import detect_market
from app.utils.symbol_validation import validate_symbol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/discussion", tags=["Discussion"])

# SSE configuration
STREAMING_TIMEOUT_SECONDS = 600  # 10 minutes for full discussion


async def discussion_stream_rate_limit(
    request: Request,
    last_event_id: str = Query("0-0", alias="lastEventId"),
):
    """Only rate-limit genuinely new discussion stream requests, not SSE reconnections.

    When a client reconnects after a network hiccup it sends the last
    received Redis Stream ID via ``lastEventId``.  A non-default value
    (anything other than "0-0") means the client is merely resuming an
    existing stream and should not consume a rate-limit token.
    """
    if last_event_id == "0-0":
        limiter = rate_limit(max_requests=5, window_seconds=60)
        await limiter(request)


@router.post(
    "/{symbol}/start",
    response_model=DiscussionSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new discussion",
    description="Start a new multi-agent discussion session for a stock symbol.",
    dependencies=[Depends(rate_limit(max_requests=5, window_seconds=60))],
)
async def start_discussion(
    symbol: str,
    body: StartDiscussionRequest,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start a new discussion session."""
    # Check if discussion feature is enabled
    from app.services.settings_service import get_settings_service

    settings = get_settings_service()
    system = await settings.get_system_settings(db)
    if not getattr(system, "discussion_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Discussion feature is not enabled. Please contact admin.",
        )

    symbol = validate_symbol(symbol)
    market = detect_market(symbol)

    language = body.language or "zh"
    if language not in ("en", "zh"):
        language = "zh"

    service = get_discussion_service()

    # M1: Reuse existing active session for the same (user, symbol)
    existing = await service.find_active_session(db, current_user.id, symbol)
    if existing:
        logger.info(
            "讨论组: 复用已有会话 session=%s symbol=%s user=%d status=%s",
            existing.id, symbol, current_user.id, existing.status,
        )
        response.status_code = status.HTTP_200_OK
        return DiscussionSessionResponse.model_validate(existing)

    # M11: Enforce concurrent discussion limit per user
    active_count = await service.count_active_sessions(db, current_user.id)
    if active_count >= 3:
        raise HTTPException(
            status_code=429,
            detail="Maximum 3 concurrent discussions allowed",
        )

    session = await service.start_discussion(
        db=db,
        user_id=current_user.id,
        symbol=symbol,
        market=market.value if hasattr(market, "value") else str(market),
        language=language,
    )

    return DiscussionSessionResponse.model_validate(session)


@router.get(
    "/{session_id}/stream",
    summary="Stream discussion events",
    description="SSE stream of discussion events with background execution and reconnection.",
)
async def stream_discussion(
    session_id: uuid.UUID,
    last_event_id: str = Query("0-0", alias="lastEventId"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _ai_config: None = Depends(apply_user_ai_config),
    _rate_limit: None = Depends(discussion_stream_rate_limit),
):
    """Stream the discussion workflow as SSE events.

    The workflow runs as a background task and publishes events to Redis
    Streams. Clients can reconnect via `lastEventId` to replay missed events.
    """
    # Check feature gate
    from app.services.settings_service import get_settings_service
    from app.services.sse_helpers import reconnectable_sse_generator
    from app.services.task_manager import get_task_manager

    settings_svc = get_settings_service()
    system = await settings_svc.get_system_settings(db)
    if not getattr(system, "discussion_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Discussion feature is not enabled.",
        )

    service = get_discussion_service()

    # Load session to get symbol + check status
    session = await service.get_session(db, session_id, current_user.id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discussion session not found",
        )

    # Don't re-stream completed or failed sessions
    if session.status in ("completed", "failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session already {session.status}",
        )

    symbol = session.symbol
    market = session.market
    language = session.language

    logger.info(
        "讨论组: 流式请求 session=%s symbol=%s user=%d lastEventId=%s",
        str(session_id)[:8], symbol, current_user.id, last_event_id,
    )

    # Capture ContextVar before background task creation
    captured_config = current_user_ai_config.get()

    async def workflow_factory():
        """Discussion workflow generator that runs in a background task."""
        token = None
        if captured_config:
            token = current_user_ai_config.set(captured_config)
        try:
            async for event in service.stream_discussion(
                session_id=session_id,
                user_id=current_user.id,
            ):
                event_type = event.get("type", "unknown")
                event_data = event.get("data", {})
                yield {
                    "type": event_type,
                    "timestamp": time.time(),
                    **event_data,
                }
        finally:
            if token is not None:
                current_user_ai_config.reset(token)

    task_manager = get_task_manager()
    task_id, _is_new = await task_manager.get_or_create_task(
        task_type="discussion",
        user_id=current_user.id,
        symbol=symbol,
        market=market,
        language=language,
        workflow_factory=workflow_factory,
        session_id=str(session_id),
    )

    return StreamingResponse(
        reconnectable_sse_generator(
            task_id, last_event_id,
            timeout_seconds=STREAMING_TIMEOUT_SECONDS,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/sessions",
    response_model=list[DiscussionSessionResponse],
    summary="List discussion sessions",
    description="List discussion sessions for the current user.",
)
async def list_sessions(
    symbol: Optional[str] = Query(None, description="Filter by stock symbol"),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List discussion sessions."""
    service = get_discussion_service()
    sessions = await service.list_sessions(
        db=db,
        user_id=current_user.id,
        symbol=symbol,
        limit=limit,
        offset=offset,
    )
    return [DiscussionSessionResponse.model_validate(s) for s in sessions]


@router.get(
    "/{session_id}",
    response_model=DiscussionSessionDetailResponse,
    summary="Get discussion session detail",
    description="Get a discussion session with all messages.",
)
async def get_session_detail(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get discussion session with all messages."""
    service = get_discussion_service()
    session = await service.get_session_detail(db, session_id, current_user.id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discussion session not found",
        )

    messages = [
        DiscussionMessageResponse.model_validate(m)
        for m in sorted(session.messages, key=lambda m: m.created_at)
    ]

    return DiscussionSessionDetailResponse(
        id=session.id,
        symbol=session.symbol,
        market=session.market,
        language=session.language,
        status=session.status,
        synthesis_report=session.synthesis_report,
        compact_context=session.compact_context,
        discussion_rounds=session.discussion_rounds,
        max_rounds=session.max_rounds,
        total_tokens=session.total_tokens,
        total_latency_ms=session.total_latency_ms,
        error=session.error,
        created_at=session.created_at,
        completed_at=session.completed_at,
        messages=messages,
    )


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete discussion session",
    description="Delete a past discussion session and all its messages.",
)
async def delete_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a discussion session owned by the current user."""
    service = get_discussion_service()
    deleted = await service.delete_session(db, session_id, current_user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discussion session not found",
        )
    await db.commit()
    return None


@router.post(
    "/{session_id}/chat",
    response_model=CreateDiscussionChatResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create post-discussion chat",
    description="Create a chat conversation linked to a completed discussion session.",
)
async def create_discussion_chat(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a chat conversation for post-discussion follow-up."""
    service = get_discussion_service()
    conversation_id = await service.create_discussion_conversation(
        db=db,
        session_id=session_id,
        user_id=current_user.id,
    )
    if not conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discussion session not found or not completed",
        )
    return CreateDiscussionChatResponse(conversation_id=conversation_id)
