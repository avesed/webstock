"""AI Chat API endpoints."""

import asyncio
import json
import logging
import time
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user
from app.core.user_ai_config import UserAIConfig, current_user_ai_config
from app.db.database import get_db
from app.models.user import User
from app.models.user_settings import UserSettings
from app.schemas.chat import (
    ChatMessageResponse,
    ConversationListResponse,
    ConversationResponse,
    CreateConversationRequest,
    SendMessageRequest,
    UpdateConversationRequest,
)
from app.models.chat import ChatMessage
from app.services.chat_service import get_chat_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])

# Rate limiting: 20 requests per minute for AI chat per user
AI_CHAT_RATE_LIMIT = rate_limit(
    max_requests=20,
    window_seconds=60,
    key_prefix="ai_chat",
)

# Streaming configuration
STREAMING_TIMEOUT_SECONDS = 300  # 5 minutes max for streaming
HEARTBEAT_INTERVAL_SECONDS = 15


async def apply_user_ai_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Load AI settings using SettingsService and set them in the request context.

    Priority: user settings (if permitted) > system settings > env variables.
    """
    from app.services.settings_service import get_settings_service

    try:
        settings_service = get_settings_service()
        ai_config = await settings_service.get_user_ai_config(db, current_user.id)

        # Set resolved config in context for OpenAI client to use
        current_user_ai_config.set(
            UserAIConfig(
                api_key=ai_config.api_key,
                base_url=ai_config.base_url,
                model=ai_config.model,
                max_tokens=ai_config.max_tokens,
                temperature=ai_config.temperature,
                system_prompt=ai_config.system_prompt,
            )
        )
    except Exception as e:
        logger.warning(f"Failed to load user AI config: {e}")


async def _build_conversation_response(
    conversation,
    db: AsyncSession,
) -> ConversationResponse:
    """Build a ConversationResponse from a Conversation model instance.

    Queries message count and last message directly from the database
    instead of relying on eager-loaded relationships.
    """
    # Count messages for this conversation
    count_result = await db.execute(
        select(func.count()).select_from(ChatMessage).where(
            ChatMessage.conversation_id == conversation.id
        )
    )
    message_count = count_result.scalar_one()

    # Get the most recent message content
    last_msg_result = await db.execute(
        select(ChatMessage.content)
        .where(ChatMessage.conversation_id == conversation.id)
        .order_by(desc(ChatMessage.created_at))
        .limit(1)
    )
    last_row = last_msg_result.first()
    last_message = last_row[0] if last_row else None

    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        symbol=conversation.symbol,
        is_archived=conversation.is_archived,
        last_message=last_message,
        message_count=message_count,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create conversation",
    description="Create a new chat conversation.",
)
async def create_conversation(
    body: CreateConversationRequest,
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(AI_CHAT_RATE_LIMIT),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new chat conversation.

    Optionally provide a title and/or stock symbol to associate with the conversation.

    **Rate Limit**: 20 requests per minute per user
    """
    logger.info(f"Creating conversation for user {current_user.id}")

    try:
        chat_service = get_chat_service()
        conversation = await chat_service.create_conversation(
            db=db,
            user_id=current_user.id,
            title=body.title,
            symbol=body.symbol,
        )
        await db.commit()

        return await _build_conversation_response(conversation, db)

    except Exception as e:
        logger.exception(f"Failed to create conversation for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create conversation. Please try again later.",
        )


@router.get(
    "/conversations",
    response_model=ConversationListResponse,
    summary="List conversations",
    description="List the current user's chat conversations.",
)
async def list_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=50, description="Number of conversations to return"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
):
    """
    List conversations for the authenticated user.

    Returns a paginated list of conversations ordered by most recent activity.
    """
    try:
        chat_service = get_chat_service()
        conversations, total = await chat_service.list_conversations(
            db=db,
            user_id=current_user.id,
            limit=limit,
            offset=offset,
        )

        conv_responses = []
        for c in conversations:
            conv_responses.append(await _build_conversation_response(c, db))

        return ConversationListResponse(
            conversations=conv_responses,
            total=total,
        )

    except Exception as e:
        logger.exception(f"Failed to list conversations for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve conversations. Please try again later.",
        )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="Get conversation",
    description="Get a specific conversation by ID.",
)
async def get_conversation(
    conversation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get conversation details by ID.

    Returns 404 if the conversation does not exist or is not owned by the current user.
    """
    chat_service = get_chat_service()
    conversation = await chat_service.get_conversation(
        db=db,
        conversation_id=conversation_id,
        user_id=current_user.id,
    )

    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    return await _build_conversation_response(conversation, db)


@router.put(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="Update conversation",
    description="Update a conversation's title or archive status.",
)
async def update_conversation(
    conversation_id: UUID,
    body: UpdateConversationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update a conversation.

    Allows updating the title and/or archive status of a conversation.
    Returns 404 if the conversation does not exist or is not owned by the current user.
    """
    chat_service = get_chat_service()

    # Verify the conversation exists and belongs to the user
    existing = await chat_service.get_conversation(
        db=db,
        conversation_id=conversation_id,
        user_id=current_user.id,
    )
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    try:
        conversation = await chat_service.update_conversation(
            db=db,
            conversation_id=conversation_id,
            user_id=current_user.id,
            title=body.title,
            is_archived=body.is_archived,
        )
        await db.commit()

        return await _build_conversation_response(conversation, db)

    except Exception as e:
        logger.exception(
            f"Failed to update conversation {conversation_id} for user {current_user.id}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update conversation. Please try again later.",
        )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete conversation",
    description="Delete a conversation and all its messages.",
)
async def delete_conversation(
    conversation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a conversation.

    Permanently removes the conversation and all associated messages.
    Returns 404 if the conversation does not exist or is not owned by the current user.
    """
    chat_service = get_chat_service()
    deleted = await chat_service.delete_conversation(
        db=db,
        conversation_id=conversation_id,
        user_id=current_user.id,
    )

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    await db.commit()
    return None


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[ChatMessageResponse],
    summary="Get messages",
    description="Get messages for a conversation.",
)
async def get_messages(
    conversation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100, description="Number of messages to return"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
):
    """
    Get messages for a conversation.

    Returns a paginated list of messages ordered by creation time.
    Returns 404 if the conversation does not exist or is not owned by the current user.
    """
    chat_service = get_chat_service()

    # Verify the conversation exists and belongs to the user
    conversation = await chat_service.get_conversation(
        db=db,
        conversation_id=conversation_id,
        user_id=current_user.id,
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    try:
        messages = await chat_service.get_messages(
            db=db,
            conversation_id=conversation_id,
            user_id=current_user.id,
            limit=limit,
            offset=offset,
        )

        return [
            ChatMessageResponse(
                id=msg.id,
                conversation_id=msg.conversation_id,
                role=msg.role,
                content=msg.content,
                token_count=msg.token_count,
                model=msg.model,
                tool_calls=msg.tool_calls,
                rag_context=msg.rag_context,
                created_at=msg.created_at,
            )
            for msg in messages
        ]

    except Exception as e:
        logger.exception(
            f"Failed to get messages for conversation {conversation_id}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve messages. Please try again later.",
        )


@router.post(
    "/conversations/{conversation_id}/messages/stream",
    summary="Send message (streaming)",
    description="Send a message and receive the AI response as a Server-Sent Events stream.",
)
async def send_message_stream(
    request: Request,
    conversation_id: UUID,
    body: SendMessageRequest,
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(AI_CHAT_RATE_LIMIT),
    _ai_config: None = Depends(apply_user_ai_config),
    db: AsyncSession = Depends(get_db),
):
    """
    Send a message in a conversation and stream the AI response via SSE.

    Returns a stream of Server-Sent Events as the AI generates a response:
    - `message_start`: AI response generation started
    - `content_chunk`: Partial content from the AI
    - `message_complete`: AI response finished
    - `error`: An error occurred
    - `heartbeat`: Keep-alive signal

    **Rate Limit**: 20 requests per minute per user

    **Event Format**:
    ```
    data: {"type": "content_chunk", "content": "...", "timestamp": 1234567890}

    ```
    """
    chat_service = get_chat_service()

    # Verify the conversation exists and belongs to the user
    conversation = await chat_service.get_conversation(
        db=db,
        conversation_id=conversation_id,
        user_id=current_user.id,
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    logger.info(
        f"Chat message sent in conversation {conversation_id} by user {current_user.id}"
    )

    async def event_generator():
        """Generate SSE events with heartbeat and proper cleanup."""
        start_time = time.time()
        last_event_time = time.time()
        heartbeat_task = None
        stream_complete = False

        # Queue for heartbeat events
        heartbeat_queue: asyncio.Queue = asyncio.Queue()

        async def heartbeat_sender():
            """Send periodic heartbeat events."""
            nonlocal last_event_time
            try:
                while not stream_complete:
                    await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                    if not stream_complete:
                        if time.time() - last_event_time >= HEARTBEAT_INTERVAL_SECONDS:
                            await heartbeat_queue.put(
                                f'data: {json.dumps({"type": "heartbeat", "timestamp": time.time()})}\n\n'
                            )
            except asyncio.CancelledError:
                pass

        try:
            # Start heartbeat task
            heartbeat_task = asyncio.create_task(heartbeat_sender())

            # Stream chat response events with timeout
            try:
                # Normalize language: 'zh-CN', 'zh-TW' etc. → 'zh', others → 'en'
                lang = "en"
                if body.language:
                    lang = "zh" if body.language.lower().startswith("zh") else "en"

                async with asyncio.timeout(STREAMING_TIMEOUT_SECONDS):
                    async for event in chat_service.chat_stream(
                        db=db,
                        conversation_id=conversation_id,
                        user_id=current_user.id,
                        user_message=body.content,
                        symbol=body.symbol,
                        language=lang,
                    ):
                        # Check for client disconnect
                        if await request.is_disconnected():
                            logger.info(
                                f"Client disconnected during chat stream "
                                f"for conversation {conversation_id}"
                            )
                            break

                        # Check for overall timeout
                        if time.time() - start_time > STREAMING_TIMEOUT_SECONDS:
                            logger.warning(
                                f"Chat stream timeout reached for conversation {conversation_id}"
                            )
                            yield (
                                f'data: {json.dumps({"type": "timeout", "message": "Chat response timeout reached", "timestamp": time.time()})}\n\n'
                            )
                            break

                        # Yield the chat event
                        yield event
                        last_event_time = time.time()

                        # Also yield any pending heartbeat events
                        while not heartbeat_queue.empty():
                            try:
                                hb_event = heartbeat_queue.get_nowait()
                                yield hb_event
                                last_event_time = time.time()
                            except asyncio.QueueEmpty:
                                break

            except asyncio.TimeoutError:
                logger.warning(
                    f"Chat stream timeout for conversation {conversation_id}"
                )
                yield (
                    f'data: {json.dumps({"type": "timeout", "message": "Chat response timeout reached", "timestamp": time.time()})}\n\n'
                )

        except Exception as e:
            logger.exception(
                f"Chat stream error for conversation {conversation_id}: {e}"
            )
            # Provide specific error messages for common failures
            error_msg = "An unexpected error occurred. Please try again."
            exc_str = str(e).lower()
            if "api key" in exc_str or "authentication" in exc_str or "401" in exc_str:
                error_msg = "AI服务认证失败，请检查 Settings 中的 API Key 是否正确。"
            elif "model" in exc_str and ("not found" in exc_str or "does not exist" in exc_str or "404" in exc_str):
                error_msg = "模型不存在，请检查 Settings 中的模型名称是否正确。"
            elif "unsupported" in exc_str or "invalid" in exc_str or "400" in exc_str:
                error_msg = "AI服务参数错误，可能是模型不支持当前配置。"
            elif "rate limit" in exc_str or "429" in exc_str:
                error_msg = "AI服务请求过于频繁，请稍后再试。"
            elif "timeout" in exc_str or "timed out" in exc_str:
                error_msg = "AI服务响应超时，请稍后再试。"
            elif "connection" in exc_str or "network" in exc_str:
                error_msg = "无法连接到AI服务，请检查网络或 API Base URL 设置。"
            yield (
                f'data: {json.dumps({"type": "error", "error": error_msg, "timestamp": time.time()})}\n\n'
            )

        finally:
            stream_complete = True
            if heartbeat_task and not heartbeat_task.done():
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            logger.debug(
                f"Chat stream cleanup completed for conversation {conversation_id}"
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
