"""Chat service for AI-powered stock analysis conversations.

Handles conversation lifecycle, message persistence, and streaming responses
via the LLM Gateway with function calling (tool use).  The LLM can invoke
stock data, news, portfolio, and RAG tools during a conversation.

All database operations use async SQLAlchemy ORM to prevent SQL injection.
"""

import asyncio
import json
import logging
import time
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator
from uuid import UUID

from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import AsyncSessionLocal

from app.config import settings
from app.core.llm import (
    get_llm_gateway,
    get_chat_model_config,
    ChatRequest,
    Message,
    Role,
    ToolCall,
    ContentDelta,
    ToolCallDelta,
    UsageInfo,
    FinishEvent,
)
from app.core.user_ai_config import current_user_ai_config
from app.core.token_bucket import get_chat_rate_limiter, get_user_chat_rate_limiter
from app.models.chat import ChatMessage, Conversation
from app.skills.chat_adapter import (
    execute_chat_tool,
    get_chat_tools,
    get_tool_label,
)
from app.prompts import build_chat_system_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum messages to include in the context window sent to the LLM
_MAX_CONTEXT_MESSAGES = 10

# Rough token budget for the context window (used for trimming)
_MAX_CONTEXT_TOKENS = 4000

# Average characters per token (conservative estimate for mixed EN/ZH text)
_CHARS_PER_TOKEN = 3

# Maximum tool call loop iterations to prevent infinite loops
_MAX_TOOL_ITERATIONS = 3

# Wall-clock timeout for the entire tool call loop (seconds)
_TOOL_LOOP_TIMEOUT = 60


class ChatService:
    """Service for managing AI chat conversations and streaming responses."""

    # ------------------------------------------------------------------
    # Conversation CRUD
    # ------------------------------------------------------------------

    async def create_conversation(
        self,
        db: AsyncSession,
        user_id: int,
        title: str | None = None,
        symbol: str | None = None,
    ) -> Conversation:
        """Create a new conversation for *user_id*."""
        conversation = Conversation(
            id=_uuid.uuid4(),
            user_id=user_id,
            title=title,
            symbol=symbol,
        )
        db.add(conversation)
        await db.flush()
        logger.info(
            "Created conversation %s for user %d", conversation.id, user_id
        )
        return conversation

    async def get_conversation(
        self,
        db: AsyncSession,
        conversation_id: UUID,
        user_id: int,
    ) -> Conversation | None:
        """Return a conversation only if it belongs to *user_id*."""
        result = await db.execute(
            select(Conversation).where(
                and_(
                    Conversation.id == conversation_id,
                    Conversation.user_id == user_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def list_conversations(
        self,
        db: AsyncSession,
        user_id: int,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Conversation], int]:
        """Return paginated conversations for *user_id* and the total count."""
        count_result = await db.execute(
            select(func.count()).select_from(Conversation).where(
                Conversation.user_id == user_id
            )
        )
        total = count_result.scalar_one()

        rows_result = await db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(desc(Conversation.updated_at))
            .limit(limit)
            .offset(offset)
        )
        conversations = list(rows_result.scalars().all())

        return conversations, total

    async def update_conversation(
        self,
        db: AsyncSession,
        conversation_id: UUID,
        user_id: int,
        title: str | None = None,
        is_archived: bool | None = None,
    ) -> Conversation:
        """Update mutable fields on a conversation owned by *user_id*."""
        conversation = await self.get_conversation(db, conversation_id, user_id)
        if conversation is None:
            raise ValueError("Conversation not found")

        if title is not None:
            conversation.title = title
        if is_archived is not None:
            conversation.is_archived = is_archived

        conversation.updated_at = datetime.now(timezone.utc)
        await db.flush()
        logger.info("Updated conversation %s", conversation_id)
        return conversation

    async def delete_conversation(
        self,
        db: AsyncSession,
        conversation_id: UUID,
        user_id: int,
    ) -> bool:
        """Delete a conversation and its messages. Returns True on success."""
        conversation = await self.get_conversation(db, conversation_id, user_id)
        if conversation is None:
            return False

        await db.execute(
            delete(ChatMessage).where(
                ChatMessage.conversation_id == conversation_id
            )
        )
        await db.execute(
            delete(Conversation).where(Conversation.id == conversation_id)
        )
        await db.flush()
        logger.info("Deleted conversation %s for user %d", conversation_id, user_id)
        return True

    # ------------------------------------------------------------------
    # Message helpers
    # ------------------------------------------------------------------

    async def add_message(
        self,
        db: AsyncSession,
        conversation_id: UUID,
        role: str,
        content: str,
        token_count: int | None = None,
        model: str | None = None,
        tool_calls: dict | None = None,
        rag_context: list[dict] | None = None,
    ) -> ChatMessage:
        """Persist a single chat message."""
        message = ChatMessage(
            id=_uuid.uuid4(),
            conversation_id=conversation_id,
            role=role,
            content=content,
            token_count=token_count,
            model=model,
            tool_calls=tool_calls,
            rag_context=rag_context,
        )
        db.add(message)
        await db.flush()
        return message

    async def get_messages(
        self,
        db: AsyncSession,
        conversation_id: UUID,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatMessage]:
        """Return messages for a conversation after verifying ownership."""
        conversation = await self.get_conversation(db, conversation_id, user_id)
        if conversation is None:
            return []

        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Streaming chat with tool call loop (core method)
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        db: AsyncSession,
        conversation_id: UUID,
        user_id: int,
        user_message: str,
        symbol: str | None = None,
        language: str = "en",
    ) -> AsyncGenerator[str, None]:
        """Stream an AI response with function calling (tool use).

        Yields Server-Sent Events (SSE) formatted as ``data: <json>\\n\\n``.

        Event types emitted:
        - ``message_start``      -- conversation/message IDs
        - ``tool_call_start``    -- LLM is calling a tool
        - ``tool_call_result``   -- tool execution result
        - ``rag_sources``        -- if knowledge base tool was used
        - ``content_delta``      -- streamed content tokens
        - ``message_end``        -- summary with token count and model
        - ``error``              -- on failure
        """
        assistant_message_id = str(_uuid.uuid4())

        try:
            # 1. Rate-limit check
            chat_limiter = await get_chat_rate_limiter()
            if not await chat_limiter.acquire():
                yield _sse({"type": "error", "error": "Service is busy. Please try again shortly."})
                return

            user_limiter = await get_user_chat_rate_limiter(user_id)
            if not await user_limiter.acquire():
                yield _sse({"type": "error", "error": "You are sending messages too quickly. Please slow down."})
                return

            # 2. Verify conversation ownership
            conversation = await self.get_conversation(db, conversation_id, user_id)
            if conversation is None:
                yield _sse({"type": "error", "error": "Conversation not found."})
                return

            # 3. Build context window BEFORE saving new user message
            history = await self._build_context_window(db, conversation_id)

            # 4. Save user message and commit immediately
            await self.add_message(
                db,
                conversation_id,
                role="user",
                content=user_message,
            )

            # 5. Auto-generate title
            if not conversation.title:
                conversation.title = user_message[:50].strip()
                conversation.updated_at = datetime.now(timezone.utc)

            if symbol and not conversation.symbol:
                conversation.symbol = symbol

            await db.commit()

            # 6. Build gateway messages payload (no inline RAG -- RAG is a tool)
            system_prompt = self._build_system_prompt(language, symbol)
            gateway_messages: list[Message] = [
                Message(role=Role.SYSTEM, content=system_prompt),
            ]
            for h in history:
                gateway_messages.append(
                    Message(role=Role(h["role"]), content=h.get("content") or "")
                )
            gateway_messages.append(
                Message(role=Role.USER, content=user_message)
            )

            # 7. Emit message_start
            yield _sse({
                "type": "message_start",
                "conversationId": str(conversation_id),
                "messageId": assistant_message_id,
            })

            # 8. Tool call loop (using synthesis model from LangGraph config)
            gateway = get_llm_gateway()
            model, provider_kwargs = await get_chat_model_config()

            # Read user-specific overrides from context variable
            user_config = current_user_ai_config.get()
            user_max_tokens = user_config.max_tokens if user_config else None
            user_temperature = user_config.temperature if user_config else None

            full_content: list[str] = []
            total_completion_tokens = 0
            all_tool_calls_meta: list[dict] = []
            rag_sources: list[dict] = []
            loop_start = time.monotonic()

            # Track whether the model supports native function calling.
            # Some providers (e.g. DeepSeek via third-party proxies) output
            # tool calls as XML text instead of structured tool_calls deltas.
            # When detected, the provider sets FinishEvent.tools_supported=False
            # and we disable tools and retry.
            tools_supported = True

            for iteration in range(_MAX_TOOL_ITERATIONS + 1):
                # Wall-clock timeout
                if time.monotonic() - loop_start > _TOOL_LOOP_TIMEOUT:
                    logger.warning("Tool loop wall-clock timeout in conversation %s", conversation_id)
                    break

                # Rate limit check for subsequent iterations
                if iteration > 0:
                    if not await chat_limiter.acquire():
                        logger.warning("Rate limit hit during tool loop iteration %d", iteration)
                        break

                # Build ChatRequest for this iteration
                request = ChatRequest(
                    model=model,
                    messages=gateway_messages,
                    tools=get_chat_tools() if tools_supported and iteration < _MAX_TOOL_ITERATIONS else None,
                    max_tokens=user_max_tokens,
                    temperature=user_temperature,
                    stream=True,
                )

                # Accumulate streamed response
                collected_tool_calls: list[ToolCall] = []
                finish_reason = None
                iteration_tokens = 0

                async for event in gateway.chat_stream(request, **provider_kwargs):
                    if isinstance(event, ContentDelta):
                        full_content.append(event.text)
                        yield _sse({
                            "type": "content_delta",
                            "content": event.text,
                        })

                    elif isinstance(event, ToolCallDelta):
                        collected_tool_calls.append(event.tool_call)

                    elif isinstance(event, UsageInfo):
                        iteration_tokens = event.usage.completion_tokens

                    elif isinstance(event, FinishEvent):
                        finish_reason = event.reason
                        if not event.tools_supported:
                            # DeepSeek XML detected by provider -- disable
                            # tools, discard any content (XML garbage was
                            # already suppressed by the provider), and retry
                            tools_supported = False

                total_completion_tokens += iteration_tokens

                # If tools_supported was just set to False, retry without tools
                if not tools_supported and finish_reason != "tool_use":
                    logger.warning(
                        "Model %s does not support structured tool calls "
                        "-- retrying without tools (conversation=%s)",
                        model, conversation_id,
                    )
                    continue

                # Check if LLM wants to call tools
                if finish_reason == "tool_use" and collected_tool_calls:
                    # Parse and execute tool calls
                    for tc in collected_tool_calls:
                        try:
                            args = json.loads(tc.arguments)
                        except json.JSONDecodeError:
                            args = {}
                            logger.warning(
                                "Malformed tool call args for %s: %s",
                                tc.name, tc.arguments[:100],
                            )

                        # Emit tool_call_start
                        label = get_tool_label(tc.name, args)
                        yield _sse({
                            "type": "tool_call_start",
                            "toolCallId": tc.id,
                            "toolName": tc.name,
                            "toolArguments": args,
                            "toolLabel": label,
                        })

                    # Append assistant message with tool_calls to messages array
                    gateway_messages.append(Message(
                        role=Role.ASSISTANT,
                        content=None,
                        tool_calls=[
                            ToolCall(id=tc.id, name=tc.name, arguments=tc.arguments)
                            for tc in collected_tool_calls
                        ],
                    ))

                    # Execute all tool calls in parallel, each with its own
                    # DB session to avoid concurrent use of a single AsyncSession.
                    async def _run_tool(tc_obj: ToolCall) -> tuple[str, str, dict]:
                        tc_id = tc_obj.id
                        name = tc_obj.name
                        try:
                            args = json.loads(tc_obj.arguments)
                        except json.JSONDecodeError:
                            args = {}
                        async with AsyncSessionLocal() as tool_db:
                            result = await execute_chat_tool(name, args, user_id, tool_db)
                        return tc_id, name, result

                    tasks = [_run_tool(tc) for tc in collected_tool_calls]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for idx, res in enumerate(results):
                        if isinstance(res, Exception):
                            tc = collected_tool_calls[idx]
                            logger.exception(
                                "Tool execution raised for %s (id=%s): %s",
                                tc.name, tc.id, res,
                            )
                            tc_id = tc.id
                            tc_name = tc.name
                            tool_result = {"error": "Tool execution failed"}
                        else:
                            tc_id, tc_name, tool_result = res

                        # Track for metadata
                        all_tool_calls_meta.append({
                            "id": tc_id,
                            "name": tc_name,
                            "result_summary": str(tool_result)[:200],
                        })

                        # Append tool result to messages array
                        result_str = json.dumps(tool_result, ensure_ascii=False, default=str)
                        gateway_messages.append(Message(
                            role=Role.TOOL,
                            content=result_str,
                            tool_call_id=tc_id,
                        ))

                        # Emit tool_call_result
                        success = "error" not in tool_result
                        summary = result_str[:200]
                        yield _sse({
                            "type": "tool_call_result",
                            "toolCallId": tc_id,
                            "toolName": tc_name,
                            "resultSummary": summary,
                            "success": success,
                        })

                        # If knowledge_base tool, also emit rag_sources
                        if tc_name == "search_knowledge_base" and success:
                            raw_sources = tool_result.get("raw_sources")
                            if raw_sources and isinstance(raw_sources, list):
                                rag_sources = raw_sources
                                yield _sse({
                                    "type": "rag_sources",
                                    "sources": raw_sources,
                                })

                    # Continue the loop for next API call
                    continue

                # finish_reason == "stop" or max iterations: done
                break

            # 9. Persist assistant message
            assistant_text = "".join(full_content)
            if not total_completion_tokens:
                total_completion_tokens = max(1, len(assistant_text) // _CHARS_PER_TOKEN)

            tool_calls_json = all_tool_calls_meta if all_tool_calls_meta else None
            rag_context_json = rag_sources if rag_sources else None

            await self.add_message(
                db,
                conversation_id,
                role="assistant",
                content=assistant_text,
                token_count=total_completion_tokens,
                model=model,
                tool_calls=tool_calls_json,
                rag_context=rag_context_json,
            )

            conversation.updated_at = datetime.now(timezone.utc)
            await db.commit()

            # 10. Final event
            yield _sse({
                "type": "message_end",
                "tokenCount": total_completion_tokens,
                "model": model,
            })

        except Exception as exc:
            logger.exception(
                "Unhandled error in chat_stream (conversation=%s, user=%d)",
                conversation_id,
                user_id,
            )
            try:
                await db.rollback()
            except Exception:
                logger.exception("Rollback failed after chat_stream error")

            # Provide specific error messages for common failures
            error_msg = "An unexpected error occurred. Please try again."
            exc_str = str(exc).lower()
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

            yield _sse({
                "type": "error",
                "error": error_msg,
            })

    # ------------------------------------------------------------------
    # System prompt (no RAG injection -- RAG is now a tool)
    # ------------------------------------------------------------------

    def _build_system_prompt(self, language: str = "en", symbol: str | None = None) -> str:
        """Build the system prompt for the function-calling agent.

        Uses user's custom system prompt if set, otherwise uses default
        from the prompts module with optional stock context.

        Args:
            language: Language code ("en" or "zh")
            symbol: Optional stock symbol for context injection
        """
        user_config = current_user_ai_config.get()
        custom_prompt = user_config.system_prompt if user_config else None
        if custom_prompt:
            return custom_prompt

        return build_chat_system_prompt(language, symbol)

    # ------------------------------------------------------------------
    # Context window
    # ------------------------------------------------------------------

    async def _build_context_window(
        self,
        db: AsyncSession,
        conversation_id: UUID,
        max_messages: int = _MAX_CONTEXT_MESSAGES,
    ) -> list[dict]:
        """Fetch recent messages and convert to OpenAI message format.

        Handles both regular messages and tool call messages:
        - ``role="assistant"`` with ``tool_calls`` metadata → reconstructed
        - ``role="tool"`` with ``tool_call_id`` → included correctly
        - Regular ``user``/``assistant`` messages → standard format

        Messages are returned in chronological order. Oldest are dropped
        if estimated token count exceeds ``_MAX_CONTEXT_TOKENS``.
        """
        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(desc(ChatMessage.created_at))
            .limit(max_messages)
        )
        rows = list(result.scalars().all())
        rows.reverse()  # chronological order

        messages: list[dict] = []
        for msg in rows:
            # Only final user/assistant messages are persisted (not intermediate
            # tool-role messages).  The assistant text already contains the
            # summarised tool results, so the model has sufficient context.
            messages.append({"role": msg.role, "content": msg.content or ""})

        # Trim oldest messages when estimated token count is too high
        token_estimate = sum(
            len(m.get("content") or "") // _CHARS_PER_TOKEN for m in messages
        )
        while token_estimate > _MAX_CONTEXT_TOKENS and len(messages) > 1:
            removed = messages.pop(0)
            token_estimate -= len(removed.get("content") or "") // _CHARS_PER_TOKEN

        return messages

    # ------------------------------------------------------------------
    # RAG retrieval (kept for potential direct use)
    # ------------------------------------------------------------------

    async def _retrieve_rag_context(
        self,
        db: AsyncSession,
        query: str,
        symbol: str | None = None,
    ) -> list:
        """Generate an embedding for *query* and search for relevant context."""
        from app.services.embedding_service import get_embedding_service, get_embedding_model_from_db
        from app.services.rag_service import get_rag_service

        embedding_service = get_embedding_service()
        embedding_model = await get_embedding_model_from_db(db)
        embedding = await embedding_service.generate_embedding(query, model=embedding_model)
        if embedding is None:
            logger.warning("Embedding generation returned None; skipping RAG")
            return []

        rag_service = get_rag_service()
        results = await rag_service.search(
            db=db,
            query_embedding=embedding,
            query_text=query,
            symbol=symbol,
            top_k=3,
        )
        logger.info(
            "RAG search returned %d results (symbol=%s)", len(results), symbol
        )
        return results


# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------

def _sse(event: dict) -> str:
    """Format a dictionary as a Server-Sent Event data line."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_chat_service: ChatService | None = None


def get_chat_service() -> ChatService:
    """Get the singleton ChatService instance."""
    global _chat_service
    if _chat_service is None:
        _chat_service = ChatService()
    return _chat_service
