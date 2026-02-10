"""OpenAI and OpenAI-compatible API provider.

Handles all OpenAI-specific concerns internally:
- Reasoning model detection (o1/o3/gpt-5) -> max_completion_tokens
- stream_options: {"include_usage": True} for OpenAI models
- Temperature suppression for reasoning models
- DeepSeek XML tool call detection -> FinishEvent.tools_supported=False
- Index-based streaming tool call delta accumulation
- Tool definition/result format conversion
"""

import json
import logging
import re
from dataclasses import replace
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI

from app.core.llm.providers.base import LLMProvider
from app.core.llm.types import (
    ChatRequest,
    ChatResponse,
    ContentDelta,
    EmbeddingRequest,
    EmbeddingResponse,
    FinishEvent,
    Message,
    Role,
    StreamEvent,
    TokenUsage,
    ToolCall,
    ToolCallDelta,
    ToolDefinition,
    UsageInfo,
)

logger = logging.getLogger(__name__)

# Patterns indicating a model outputs tool calls as text (e.g. DeepSeek)
_TEXT_TOOL_CALL_PATTERNS = re.compile(
    r"<\|?\s*(?:DSML|tool_call|function_call)|<tool_call>|<function_call>",
    re.IGNORECASE,
)

# Model prefixes that are reasoning models (no temperature, use max_completion_tokens)
_REASONING_PREFIXES = ("o1-", "o1", "o3-", "o3", "gpt-5")

# Model prefixes known to be OpenAI (support stream_options)
_OPENAI_MODEL_PATTERNS = (
    "gpt-3.5", "gpt-4", "gpt-5", "o1", "o3", "chatgpt", "davinci", "turbo"
)


class OpenAIProvider(LLMProvider):
    """Provider for OpenAI API and OpenAI-compatible endpoints.

    Supports vLLM, Ollama, LMStudio, DeepSeek, and other OpenAI-compatible APIs.
    """

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        self._api_key = api_key
        self._base_url = base_url
        self._client: Optional[AsyncOpenAI] = None

    @property
    def provider_name(self) -> str:
        return "openai"

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
            )
        return self._client

    # ------------------------------------------------------------------
    # Format conversion: gateway types -> OpenAI API format
    # ------------------------------------------------------------------

    def _convert_messages(self, messages: List[Message]) -> List[Dict[str, Any]]:
        """Convert gateway Messages to OpenAI message format."""
        result = []
        for msg in messages:
            d: Dict[str, Any] = {"role": msg.role.value, "content": msg.content}

            if msg.role == Role.ASSISTANT and msg.tool_calls:
                d["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
                # OpenAI requires content to be null when tool_calls present
                if not msg.content:
                    d["content"] = None

            if msg.role == Role.TOOL:
                d["tool_call_id"] = msg.tool_call_id
                if msg.name:
                    d["name"] = msg.name

            result.append(d)
        return result

    def _convert_tools(self, tools: List[ToolDefinition]) -> List[Dict[str, Any]]:
        """Convert gateway ToolDefinitions to OpenAI function calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    def _build_api_kwargs(self, request: ChatRequest) -> Dict[str, Any]:
        """Build kwargs for client.chat.completions.create()."""
        kwargs: Dict[str, Any] = {
            "model": request.model,
            "messages": self._convert_messages(request.messages),
        }

        model_lower = request.model.lower()
        is_reasoning = any(m in model_lower for m in _REASONING_PREFIXES)
        is_openai = any(m in model_lower for m in _OPENAI_MODEL_PATTERNS)

        # max_tokens handling
        if request.max_tokens is not None:
            if is_reasoning:
                kwargs["max_completion_tokens"] = request.max_tokens
            else:
                kwargs["max_tokens"] = request.max_tokens

        # Temperature (reasoning models don't support it)
        if request.temperature is not None and not is_reasoning:
            kwargs["temperature"] = request.temperature

        # Tools
        if request.tools:
            kwargs["tools"] = self._convert_tools(request.tools)
            if request.tool_choice:
                kwargs["tool_choice"] = request.tool_choice

        # Response format (JSON mode)
        if request.response_format:
            kwargs["response_format"] = request.response_format

        # stream_options for OpenAI models only
        if request.stream and is_openai:
            kwargs["stream_options"] = {"include_usage": True}

        # Timeout
        kwargs["timeout"] = request.timeout

        return kwargs

    # ------------------------------------------------------------------
    # Non-streaming chat
    # ------------------------------------------------------------------

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Non-streaming chat completion.

        Internally uses streaming to support API proxies (e.g. x86a.com)
        that always return SSE format regardless of the stream parameter.
        """
        stream_request = replace(request, stream=True)

        content_parts: List[str] = []
        tool_calls: Optional[List[ToolCall]] = None
        usage: Optional[TokenUsage] = None
        finish_reason: Optional[str] = None

        async for event in self.chat_stream(stream_request):
            if isinstance(event, ContentDelta):
                content_parts.append(event.text)
            elif isinstance(event, ToolCallDelta):
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append(event.tool_call)
            elif isinstance(event, UsageInfo):
                usage = event.usage
            elif isinstance(event, FinishEvent):
                finish_reason = event.reason

        return ChatResponse(
            content="".join(content_parts) if content_parts else None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            model=request.model,
            usage=usage,
        )

    # ------------------------------------------------------------------
    # Streaming chat
    # ------------------------------------------------------------------

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        """Streaming chat completion with tool call accumulation.

        Yields:
            ContentDelta: For text content fragments
            ToolCallDelta: For complete tool calls (accumulated from partial deltas)
            UsageInfo: Token usage (typically at stream end)
            FinishEvent: Stream completion with reason and tools_supported flag
        """
        client = self._get_client()
        kwargs = self._build_api_kwargs(request)
        kwargs["stream"] = True

        try:
            stream = await client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error(
                "OpenAI stream connection failed: model=%s, base_url=%s, error=%s",
                request.model, self._base_url or "default", e,
            )
            raise

        # Accumulate tool call partial deltas by index
        pending_tool_calls: Dict[int, Dict[str, Any]] = {}
        finish_reason = None
        buffered_content: List[str] = []
        has_tools = bool(request.tools)

        try:
            async for chunk in stream:
                choice = chunk.choices[0] if chunk.choices else None

                if choice is None:
                    # Usage-only chunk at end of stream
                    if chunk.usage is not None:
                        yield UsageInfo(
                            usage=TokenUsage(
                                prompt_tokens=chunk.usage.prompt_tokens,
                                completion_tokens=chunk.usage.completion_tokens,
                                total_tokens=chunk.usage.total_tokens,
                            )
                        )
                    continue

                delta = choice.delta

                # Content
                if delta and delta.content:
                    if has_tools:
                        # Buffer content when tools are active (need to check for
                        # DeepSeek XML at the end)
                        buffered_content.append(delta.content)
                    else:
                        yield ContentDelta(text=delta.content)

                # Tool call deltas (accumulate by index)
                if delta and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in pending_tool_calls:
                            pending_tool_calls[idx] = {
                                "id": None,
                                "name": None,
                                "arguments_parts": [],
                            }
                        if tc_delta.id:
                            pending_tool_calls[idx]["id"] = tc_delta.id
                        if tc_delta.function and tc_delta.function.name:
                            pending_tool_calls[idx]["name"] = tc_delta.function.name
                        if tc_delta.function and tc_delta.function.arguments:
                            pending_tool_calls[idx]["arguments_parts"].append(
                                tc_delta.function.arguments
                            )

                # Track finish reason
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                # Usage from final chunk (some providers include it here)
                if chunk.usage is not None:
                    yield UsageInfo(
                        usage=TokenUsage(
                            prompt_tokens=chunk.usage.prompt_tokens,
                            completion_tokens=chunk.usage.completion_tokens,
                            total_tokens=chunk.usage.total_tokens,
                        )
                    )
        except Exception as e:
            logger.error(
                "OpenAI stream iteration failed mid-stream: model=%s, "
                "base_url=%s, error=%s",
                request.model, self._base_url or "default", e,
            )
            yield FinishEvent(reason="error")
            return

        # Post-stream processing

        # Check for DeepSeek XML tool call text
        tools_supported = True
        if has_tools and buffered_content:
            joined = "".join(buffered_content)
            if (
                not pending_tool_calls
                and finish_reason != "tool_calls"
                and _TEXT_TOOL_CALL_PATTERNS.search(joined)
            ):
                logger.warning(
                    "Model output text-based tool calls instead of "
                    "structured tool_calls — flagging tools_supported=False"
                )
                tools_supported = False
                # Don't yield the XML garbage content
                buffered_content.clear()

        # Flush buffered content (if tools were active but no tool calls made)
        if buffered_content:
            if finish_reason != "tool_calls" or not pending_tool_calls:
                for text in buffered_content:
                    yield ContentDelta(text=text)

        # Emit complete tool calls
        if pending_tool_calls:
            for idx in sorted(pending_tool_calls.keys()):
                tc = pending_tool_calls[idx]
                args_str = "".join(tc["arguments_parts"])
                yield ToolCallDelta(
                    tool_call=ToolCall(
                        id=tc["id"] or f"call_{idx}",
                        name=tc["name"] or "unknown",
                        arguments=args_str,
                    )
                )

        # Emit finish event
        normalized_reason = finish_reason or "stop"
        if normalized_reason == "tool_calls":
            normalized_reason = "tool_use"
        yield FinishEvent(
            reason=normalized_reason,
            tools_supported=tools_supported,
        )

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    def supports_embeddings(self) -> bool:
        return True

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Generate embeddings via OpenAI API."""
        client = self._get_client()
        kwargs: Dict[str, Any] = {
            "model": request.model,
            "input": request.input,
        }
        if request.dimensions:
            kwargs["dimensions"] = request.dimensions

        response = await client.embeddings.create(**kwargs)

        usage = None
        if response.usage:
            usage = TokenUsage(
                prompt_tokens=response.usage.prompt_tokens,
                total_tokens=response.usage.total_tokens,
            )

        return EmbeddingResponse(
            embeddings=[item.embedding for item in response.data],
            model=response.model,
            usage=usage,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        if self._client:
            try:
                await self._client.close()
            except Exception as e:
                logger.warning("Error closing OpenAI client: %s", e)
            finally:
                self._client = None

    def reset(self) -> None:
        """Sync reset — discard client without awaiting close."""
        self._client = None
