"""HTTP client for the AI Gateway process.

The AI Gateway runs as a supervisord process inside the app container,
exposing OpenAI-compatible endpoints on 127.0.0.1:8004. It handles
provider routing (OpenAI/Anthropic) based on X-Provider-Id header.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from app.config import settings
from app.core.llm.types import (
    ChatRequest,
    ChatResponse,
    ContentDelta,
    FinishEvent,
    Message,
    StreamEvent,
    TokenUsage,
    ToolCall,
    ToolCallDelta,
    ToolDefinition,
    UsageInfo,
)
from app.core.request_id import get_request_id

logger = logging.getLogger(__name__)

# Module-level singleton
_client: Optional["AiGatewayClient"] = None
_client_lock = asyncio.Lock()

# Timeout presets — gateway timeout (135s) must exceed the gateway's
# internal provider timeout (120s) to avoid cutting off in-flight responses
_DEFAULT_TIMEOUT = httpx.Timeout(135.0, connect=10.0)
_LONG_TIMEOUT = httpx.Timeout(310.0, connect=10.0)


class AiGatewayClient:
    """Async HTTP client for the AI Gateway."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or settings.AI_GATEWAY_URL
        self._token = settings.INTERNAL_API_TOKEN
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=_DEFAULT_TIMEOUT,
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
            headers={"X-Internal-Token": self._token} if self._token else {},
        )
        logger.info("AiGatewayClient initialized: %s", self.base_url)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
        logger.info("AiGatewayClient closed")

    # ------------------------------------------------------------------
    # Request building
    # ------------------------------------------------------------------

    def _build_headers(self, provider_id: Optional[str] = None) -> Dict[str, str]:
        """Build request headers."""
        headers: Dict[str, str] = {}
        rid = get_request_id()
        if rid:
            headers["X-Request-ID"] = rid
        if provider_id:
            headers["X-Provider-Id"] = provider_id
        return headers

    def _build_chat_body(self, request: ChatRequest) -> Dict[str, Any]:
        """Convert internal ChatRequest to OpenAI-format request body."""
        messages = []
        for msg in request.messages:
            d: Dict[str, Any] = {"role": msg.role.value, "content": msg.content}
            if msg.tool_calls:
                d["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                    for tc in msg.tool_calls
                ]
            if msg.tool_call_id:
                d["tool_call_id"] = msg.tool_call_id
            if msg.name:
                d["name"] = msg.name
            if msg.cache_control:
                d["cache_control"] = msg.cache_control
            messages.append(d)

        body: Dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "stream": request.stream,
            "timeout": request.timeout,
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
        if request.tool_choice:
            body["tool_choice"] = request.tool_choice
        if request.response_format:
            body["response_format"] = request.response_format
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens

        return body

    # ------------------------------------------------------------------
    # Non-streaming chat
    # ------------------------------------------------------------------

    async def chat(
        self,
        request: ChatRequest,
        *,
        provider_id: Optional[str] = None,
    ) -> ChatResponse:
        """Non-streaming chat completion via ai-gateway."""
        body = self._build_chat_body(request)
        body["stream"] = False
        headers = self._build_headers(provider_id)

        resp = await self._client.post(
            "/v1/chat/completions",
            json=body,
            headers=headers,
            timeout=_DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        # Parse OpenAI-format response
        choice = data["choices"][0]
        msg = choice["message"]

        tool_calls = None
        if msg.get("tool_calls"):
            tool_calls = [
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=tc["function"]["arguments"],
                )
                for tc in msg["tool_calls"]
            ]

        usage = None
        if data.get("usage"):
            u = data["usage"]
            cached = 0
            if u.get("prompt_tokens_details"):
                cached = u["prompt_tokens_details"].get("cached_tokens", 0) or 0
            usage = TokenUsage(
                prompt_tokens=u.get("prompt_tokens", 0),
                completion_tokens=u.get("completion_tokens", 0),
                total_tokens=u.get("total_tokens", 0),
                cached_tokens=cached,
            )

        # Map finish_reason back to internal format
        finish_reason = choice.get("finish_reason", "stop")
        if finish_reason == "tool_calls":
            finish_reason = "tool_use"

        return ChatResponse(
            content=msg.get("content"),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            model=data.get("model", request.model),
            usage=usage,
        )

    # ------------------------------------------------------------------
    # Streaming chat
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        request: ChatRequest,
        *,
        provider_id: Optional[str] = None,
    ) -> AsyncIterator[StreamEvent]:
        """Streaming chat completion via ai-gateway. Yields StreamEvent."""
        body = self._build_chat_body(request)
        body["stream"] = True
        headers = self._build_headers(provider_id)

        try:
            async with self._client.stream(
                "POST",
                "/v1/chat/completions",
                json=body,
                headers=headers,
                timeout=_LONG_TIMEOUT,
            ) as resp:
                resp.raise_for_status()

                # Accumulate tool call deltas by index
                pending_tool_calls: Dict[int, Dict[str, Any]] = {}

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # Usage (may appear in final chunk or separately)
                    if chunk.get("usage"):
                        u = chunk["usage"]
                        cached = 0
                        if u.get("prompt_tokens_details"):
                            cached = u["prompt_tokens_details"].get("cached_tokens", 0) or 0
                        yield UsageInfo(
                            usage=TokenUsage(
                                prompt_tokens=u.get("prompt_tokens", 0),
                                completion_tokens=u.get("completion_tokens", 0),
                                total_tokens=u.get("total_tokens", 0),
                                cached_tokens=cached,
                            )
                        )

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue

                    choice = choices[0]
                    delta = choice.get("delta", {})

                    # Content — use `is not None` to preserve empty string ""
                    content = delta.get("content")
                    if content is not None and content != "":
                        yield ContentDelta(text=content)

                    # Tool calls (accumulate partials)
                    if delta.get("tool_calls"):
                        for tc_delta in delta["tool_calls"]:
                            idx = tc_delta.get("index", 0)
                            if idx not in pending_tool_calls:
                                pending_tool_calls[idx] = {
                                    "id": None,
                                    "name": None,
                                    "arguments_parts": [],
                                }
                            if tc_delta.get("id"):
                                pending_tool_calls[idx]["id"] = tc_delta["id"]
                            fn = tc_delta.get("function", {})
                            if fn.get("name"):
                                pending_tool_calls[idx]["name"] = fn["name"]
                            if fn.get("arguments"):
                                pending_tool_calls[idx]["arguments_parts"].append(fn["arguments"])

                    # Finish reason
                    finish_reason = choice.get("finish_reason")
                    if finish_reason:
                        # Emit accumulated tool calls before finish
                        for idx in sorted(pending_tool_calls.keys()):
                            tc = pending_tool_calls[idx]
                            yield ToolCallDelta(
                                tool_call=ToolCall(
                                    id=tc["id"] or f"call_{idx}",
                                    name=tc["name"] or "unknown",
                                    arguments="".join(tc["arguments_parts"]),
                                )
                            )
                        pending_tool_calls.clear()

                        # Map finish reason
                        reason = finish_reason
                        if reason == "tool_calls":
                            reason = "tool_use"

                        # Read tools_supported from SSE chunk data
                        # (gateway embeds it as webstock_tools_supported field)
                        tools_supported = chunk.get("webstock_tools_supported", True)

                        yield FinishEvent(
                            reason=reason,
                            tools_supported=tools_supported,
                        )

        except httpx.HTTPStatusError as e:
            logger.error(
                "AI Gateway HTTP error: %s %s", e.response.status_code, e.response.text[:200],
            )
            raise
        except httpx.HTTPError as e:
            logger.error("AI Gateway connection error: %s", e)
            raise

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    async def embed(
        self,
        model: str,
        input_text,
        *,
        provider_id: Optional[str] = None,
        dimensions: Optional[int] = None,
    ):
        """Generate embeddings via ai-gateway."""
        from app.core.llm.types import EmbeddingResponse

        body: Dict[str, Any] = {"model": model, "input": input_text}
        if dimensions:
            body["dimensions"] = dimensions

        headers = self._build_headers(provider_id)
        resp = await self._client.post(
            "/v1/embeddings",
            json=body,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

        usage = None
        if data.get("usage"):
            u = data["usage"]
            usage = TokenUsage(
                prompt_tokens=u.get("prompt_tokens", 0),
                total_tokens=u.get("total_tokens", 0),
            )

        return EmbeddingResponse(
            embeddings=[item["embedding"] for item in data["data"]],
            model=data.get("model", model),
            usage=usage,
        )


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------


async def get_ai_gateway_client() -> AiGatewayClient:
    """Get or create the singleton AiGatewayClient."""
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = AiGatewayClient()
    return _client


async def close_ai_gateway_client() -> None:
    """Close the singleton client."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def reset_ai_gateway_client() -> None:
    """Sync reset for Celery workers -- discard without awaiting close."""
    global _client
    _client = None
