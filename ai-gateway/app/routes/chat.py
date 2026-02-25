"""POST /v1/chat/completions — OpenAI-compatible chat completion endpoint."""

import json
import logging
import time
import uuid as uuid_mod
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.provider_cache import provider_cache, ProviderRow
from app.providers.base import LLMProvider
from app.providers.openai_provider import OpenAIProvider
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.types import (
    ChatRequest,
    ContentDelta,
    FinishEvent,
    Message,
    Role,
    StreamEvent,
    ToolCall,
    ToolCallDelta,
    ToolDefinition,
    UsageInfo,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Provider instance cache keyed by provider_id (UUID string).
# Each provider_id maps to a unique (api_key, base_url) pair from the DB,
# so this is both safe and avoids leaking API key fragments in cache keys.
# Includes a fingerprint (api_key[:8] + base_url) to detect credential rotation.
_provider_instances: Dict[str, LLMProvider] = {}
_provider_fingerprints: Dict[str, str] = {}  # provider_id -> "key_prefix:base_url"


# ---------------------------------------------------------------------------
# Request/Response models (OpenAI-compatible)
# ---------------------------------------------------------------------------


class FunctionCall(BaseModel):
    name: str
    arguments: str


class ToolCallRequest(BaseModel):
    id: Optional[str] = None
    type: str = "function"
    function: FunctionCall


class MessageRequest(BaseModel):
    role: str
    content: Optional[Any] = None  # str or list of content parts
    tool_calls: Optional[List[ToolCallRequest]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    cache_control: Optional[Dict[str, str]] = None


class ToolFunction(BaseModel):
    name: str
    description: str = ""
    parameters: Dict[str, Any] = Field(default_factory=dict)


class ToolRequest(BaseModel):
    type: str = "function"
    function: ToolFunction


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[MessageRequest]
    stream: bool = False
    tools: Optional[List[ToolRequest]] = None
    tool_choice: Optional[Any] = None  # "auto", "none", "required", or dict
    response_format: Optional[Dict[str, Any]] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout: int = 120


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_provider_id(request: Request) -> UUID:
    """Extract X-Provider-Id header."""
    raw = request.headers.get("X-Provider-Id")
    if not raw:
        raise HTTPException(400, "Missing X-Provider-Id header")
    try:
        return UUID(raw)
    except ValueError:
        raise HTTPException(400, f"Invalid X-Provider-Id: {raw}")


def _get_or_create_provider(row: ProviderRow) -> LLMProvider:
    """Get or create a provider instance for the given config.

    Keyed by provider_id (not API key fragment) — safe, stable, no leakage.
    Invalidates the cached SDK instance when the provider's credentials
    change (e.g. admin rotates API key), detected via fingerprint comparison.
    """
    cache_key = str(row.id)
    fingerprint = f"{row.api_key[:8]}:{row.base_url or 'default'}"

    # Check if cached instance has stale credentials
    if cache_key in _provider_instances:
        if _provider_fingerprints.get(cache_key) != fingerprint:
            logger.info(
                "Provider '%s' credentials changed — recreating SDK instance",
                row.name,
            )
            del _provider_instances[cache_key]

    if cache_key not in _provider_instances:
        if row.provider_type == "anthropic":
            _provider_instances[cache_key] = AnthropicProvider(
                api_key=row.api_key, base_url=row.base_url
            )
        else:
            _provider_instances[cache_key] = OpenAIProvider(
                api_key=row.api_key, base_url=row.base_url
            )
        _provider_fingerprints[cache_key] = fingerprint
        logger.info(
            "Created %s provider instance for '%s' (base_url=%s)",
            row.provider_type,
            row.name,
            row.base_url or "default",
        )
    return _provider_instances[cache_key]


def _convert_request(body: ChatCompletionRequest) -> ChatRequest:
    """Convert OpenAI-format request to internal ChatRequest."""
    messages = []
    for msg in body.messages:
        tool_calls = None
        if msg.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id or f"call_{i}",
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                )
                for i, tc in enumerate(msg.tool_calls)
            ]
        messages.append(
            Message(
                role=Role(msg.role),
                content=msg.content,
                tool_calls=tool_calls,
                tool_call_id=msg.tool_call_id,
                name=msg.name,
                cache_control=msg.cache_control,
            )
        )

    tools = None
    if body.tools:
        tools = [
            ToolDefinition(
                name=t.function.name,
                description=t.function.description,
                parameters=t.function.parameters,
            )
            for t in body.tools
        ]

    return ChatRequest(
        model=body.model,
        messages=messages,
        stream=body.stream,
        tools=tools,
        tool_choice=body.tool_choice,
        response_format=body.response_format,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        timeout=body.timeout,
    )


# ---------------------------------------------------------------------------
# Streaming SSE output (OpenAI chunk format)
# ---------------------------------------------------------------------------


def _make_chunk(chunk_id: str, model: str, **kwargs) -> str:
    """Create an OpenAI-format SSE chunk."""
    chunk = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
    }

    delta = kwargs.get("delta", {})
    if delta:
        chunk["choices"][0]["delta"] = delta

    if "finish_reason" in kwargs:
        chunk["choices"][0]["finish_reason"] = kwargs["finish_reason"]

    if "usage" in kwargs:
        chunk["usage"] = kwargs["usage"]

    # Extension fields (tools_supported flag from DeepSeek XML detection)
    if "webstock_tools_supported" in kwargs:
        chunk["webstock_tools_supported"] = kwargs["webstock_tools_supported"]

    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


async def _stream_response(
    provider: LLMProvider, internal_req: ChatRequest, model: str
):
    """Generate SSE chunks from internal StreamEvent iterator.

    Wrapped in try/except to emit an error chunk + [DONE] on failure,
    rather than silently dropping the connection.
    """
    chunk_id = f"chatcmpl-{uuid_mod.uuid4().hex[:24]}"
    tool_call_index = 0  # Incrementing index for multi-tool-call support

    # First chunk with role
    yield _make_chunk(chunk_id, model, delta={"role": "assistant"})

    try:
        async for event in provider.chat_stream(internal_req):
            if isinstance(event, ContentDelta):
                yield _make_chunk(chunk_id, model, delta={"content": event.text})

            elif isinstance(event, ToolCallDelta):
                tc = event.tool_call
                tool_call_chunk = {
                    "tool_calls": [
                        {
                            "index": tool_call_index,
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": tc.arguments,
                            },
                        }
                    ]
                }
                tool_call_index += 1
                yield _make_chunk(chunk_id, model, delta=tool_call_chunk)

            elif isinstance(event, UsageInfo):
                usage = {
                    "prompt_tokens": event.usage.prompt_tokens,
                    "completion_tokens": event.usage.completion_tokens,
                    "total_tokens": event.usage.total_tokens,
                }
                if event.usage.cached_tokens:
                    usage["prompt_tokens_details"] = {
                        "cached_tokens": event.usage.cached_tokens
                    }
                yield _make_chunk(chunk_id, model, usage=usage)

            elif isinstance(event, FinishEvent):
                # Map back to OpenAI finish reasons
                reason = event.reason
                if reason == "tool_use":
                    reason = "tool_calls"
                # Include tools_supported flag in the final chunk
                # so the client can detect DeepSeek XML tool call issues
                extra = {}
                if not event.tools_supported:
                    extra["webstock_tools_supported"] = False
                yield _make_chunk(
                    chunk_id, model, finish_reason=reason, **extra
                )

    except Exception as e:
        logger.error(
            "Streaming error mid-stream: model=%s, error=%s", model, e,
            exc_info=True,
        )
        # Emit an error chunk so the client knows something went wrong
        error_chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "error",
                }
            ],
            "error": {"message": str(e), "type": "stream_error"},
        }
        yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"

    yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------


@router.post("/v1/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request):
    """OpenAI-compatible chat completion endpoint."""
    t0 = time.monotonic()
    provider_id = _get_provider_id(request)

    # Look up provider
    row = await provider_cache.get_provider(provider_id)
    if not row:
        raise HTTPException(404, f"Provider {provider_id} not found or disabled")

    provider = _get_or_create_provider(row)
    internal_req = _convert_request(body)

    logger.info(
        "chat_completions: model=%s, provider=%s(%s), stream=%s, tools=%d",
        body.model,
        row.name,
        row.provider_type,
        body.stream,
        len(body.tools or []),
    )

    if body.stream:
        async def generate():
            async for chunk in _stream_response(provider, internal_req, body.model):
                yield chunk

        headers = {
            "X-Request-ID": request.headers.get("X-Request-ID", ""),
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Prevent nginx/reverse proxy buffering
        }
        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers=headers,
        )
    else:
        # Non-streaming
        response = await provider.chat(internal_req)

        # Build OpenAI-format response
        result: Dict[str, Any] = {
            "id": f"chatcmpl-{uuid_mod.uuid4().hex[:24]}",
            "object": "chat.completion",
            "model": body.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response.content,
                    },
                    "finish_reason": response.finish_reason or "stop",
                }
            ],
        }

        # Tool calls
        if response.tool_calls:
            result["choices"][0]["message"]["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments,
                    },
                }
                for tc in response.tool_calls
            ]
            if result["choices"][0]["finish_reason"] == "tool_use":
                result["choices"][0]["finish_reason"] = "tool_calls"

        # Usage
        if response.usage:
            result["usage"] = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
            if response.usage.cached_tokens:
                result["usage"]["prompt_tokens_details"] = {
                    "cached_tokens": response.usage.cached_tokens
                }

        elapsed = time.monotonic() - t0
        logger.info("chat_completions completed: %.2fs", elapsed)

        return result
