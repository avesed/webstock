"""Anthropic Claude API provider.

Handles all Anthropic-specific concerns internally:
- System message extraction to separate `system` parameter
- Tool definition format: input_schema instead of parameters
- Tool result format: user message with tool_result content blocks
- Content blocks (list of dicts) vs plain string
- max_tokens is required (defaults to 4096)
- Streaming via messages.stream() context manager
"""

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

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


class AnthropicProvider(LLMProvider):
    """Provider for Anthropic Claude API."""

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        self._api_key = api_key
        self._base_url = base_url
        self._client = None  # Lazy init to avoid import at module level

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def _get_client(self):
        if self._client is None:
            import anthropic
            kwargs: Dict[str, Any] = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = anthropic.AsyncAnthropic(**kwargs)
        return self._client

    # ------------------------------------------------------------------
    # Format conversion: gateway types -> Anthropic API format
    # ------------------------------------------------------------------

    def _convert_messages(
        self, messages: List[Message]
    ) -> tuple[Optional[Any], List[Dict[str, Any]]]:
        """Convert gateway messages to Anthropic format.

        Key differences from OpenAI:
        - System prompt is a separate `system` parameter
        - Tool results are user messages with tool_result content blocks
        - Content blocks use list format for tool interactions
        - cache_control is passed through for prompt caching

        Returns:
            Tuple of (system_prompt_or_blocks, anthropic_messages)
            system may be a plain string or a list of content blocks
            (when cache_control is set)
        """
        system_prompt = None
        system_cache_control = None
        anthropic_messages: List[Dict[str, Any]] = []

        for msg in messages:
            if msg.role == Role.SYSTEM:
                # Anthropic uses a separate system parameter
                system_prompt = msg.content
                system_cache_control = getattr(msg, "cache_control", None)
                continue

            if msg.role == Role.TOOL:
                # Anthropic tool results go in a user message with content blocks
                # Check if the last message is already a user message with tool_result blocks
                if (
                    anthropic_messages
                    and anthropic_messages[-1]["role"] == "user"
                    and isinstance(anthropic_messages[-1]["content"], list)
                    and any(
                        b.get("type") == "tool_result"
                        for b in anthropic_messages[-1]["content"]
                    )
                ):
                    # Append to existing tool_result user message
                    anthropic_messages[-1]["content"].append({
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": msg.content or "",
                    })
                else:
                    anthropic_messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id,
                                "content": msg.content or "",
                            }
                        ],
                    })
                continue

            if msg.role == Role.ASSISTANT and msg.tool_calls:
                # Assistant message with tool calls -> content blocks
                content_blocks: List[Dict[str, Any]] = []
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    try:
                        input_data = json.loads(tc.arguments)
                    except (json.JSONDecodeError, TypeError):
                        input_data = {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": input_data,
                    })
                anthropic_messages.append({
                    "role": "assistant",
                    "content": content_blocks,
                })
                continue

            # Regular user/assistant message
            msg_dict: Dict[str, Any] = {
                "role": msg.role.value,
                "content": msg.content or "",
            }
            # Pass cache_control if set (for Anthropic prompt caching)
            cache_ctl = getattr(msg, "cache_control", None)
            if cache_ctl:
                # Anthropic requires content blocks format for cache_control
                msg_dict["content"] = [
                    {
                        "type": "text",
                        "text": msg.content or "",
                        "cache_control": cache_ctl,
                    }
                ]
            anthropic_messages.append(msg_dict)

        # Convert system to content blocks if cache_control is set
        if system_prompt and system_cache_control:
            system_prompt = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": system_cache_control,
                }
            ]

        return system_prompt, anthropic_messages

    def _convert_tools(self, tools: List[ToolDefinition]) -> List[Dict[str, Any]]:
        """Convert gateway ToolDefinitions to Anthropic tool format.

        Anthropic uses 'input_schema' instead of OpenAI's 'parameters'.
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]

    def _build_api_kwargs(self, request: ChatRequest) -> Dict[str, Any]:
        """Build kwargs for client.messages.create()."""
        system_prompt, messages = self._convert_messages(request.messages)

        kwargs: Dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,  # Required for Anthropic
        }

        if system_prompt:
            kwargs["system"] = system_prompt

        if request.temperature is not None:
            kwargs["temperature"] = request.temperature

        if request.tools:
            kwargs["tools"] = self._convert_tools(request.tools)
            if request.tool_choice:
                # Map gateway tool_choice to Anthropic format
                if request.tool_choice == "none":
                    # Anthropic doesn't have "none" — omit tools instead
                    del kwargs["tools"]
                elif request.tool_choice == "required":
                    kwargs["tool_choice"] = {"type": "any"}
                elif request.tool_choice == "auto":
                    kwargs["tool_choice"] = {"type": "auto"}

        return kwargs

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, response) -> ChatResponse:
        """Parse Anthropic response into gateway ChatResponse."""
        content = None
        tool_calls = None

        for block in response.content:
            if block.type == "text":
                content = (content or "") + block.text
            elif block.type == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=json.dumps(block.input, ensure_ascii=False),
                    )
                )

        usage = None
        if response.usage:
            usage = TokenUsage(
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
                total_tokens=(
                    response.usage.input_tokens + response.usage.output_tokens
                ),
            )

        # Normalize stop_reason
        finish = response.stop_reason
        if finish == "end_turn":
            finish = "stop"
        elif finish == "tool_use":
            pass  # Already "tool_use"
        elif finish == "max_tokens":
            finish = "length"

        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish,
            model=response.model,
            usage=usage,
        )

    # ------------------------------------------------------------------
    # Non-streaming chat
    # ------------------------------------------------------------------

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Non-streaming chat completion."""
        client = self._get_client()
        kwargs = self._build_api_kwargs(request)
        try:
            response = await client.messages.create(**kwargs)
        except Exception as e:
            logger.error(
                "Anthropic chat failed: model=%s, base_url=%s, error=%s",
                request.model, self._base_url or "default", e,
            )
            raise
        return self._parse_response(response)

    # ------------------------------------------------------------------
    # Streaming chat
    # ------------------------------------------------------------------

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        """Streaming chat completion.

        Anthropic streaming uses different event types than OpenAI:
        - message_start: contains usage info
        - content_block_start: new content block (text or tool_use)
        - content_block_delta: incremental content
        - content_block_stop: block finished
        - message_delta: contains stop_reason and output token count
        - message_stop: stream complete
        """
        client = self._get_client()
        kwargs = self._build_api_kwargs(request)

        # Track tool calls being accumulated
        current_tool: Optional[Dict[str, Any]] = None
        input_tokens = 0
        output_tokens = 0
        stop_reason = None

        try:
            async with client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    event_type = event.type

                    if event_type == "message_start":
                        if hasattr(event, "message") and event.message.usage:
                            input_tokens = event.message.usage.input_tokens

                    elif event_type == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            current_tool = {
                                "id": block.id,
                                "name": block.name,
                                "input_parts": [],
                            }

                    elif event_type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield ContentDelta(text=delta.text)
                        elif delta.type == "input_json_delta":
                            if current_tool is not None:
                                current_tool["input_parts"].append(
                                    delta.partial_json
                                )

                    elif event_type == "content_block_stop":
                        if current_tool is not None:
                            # Emit complete tool call
                            args_str = "".join(current_tool["input_parts"])
                            yield ToolCallDelta(
                                tool_call=ToolCall(
                                    id=current_tool["id"],
                                    name=current_tool["name"],
                                    arguments=args_str,
                                )
                            )
                            current_tool = None

                    elif event_type == "message_delta":
                        if hasattr(event, "usage") and event.usage:
                            output_tokens = event.usage.output_tokens
                        if hasattr(event, "delta") and event.delta:
                            stop_reason = getattr(event.delta, "stop_reason", None)
        except Exception as e:
            logger.error(
                "Anthropic stream failed: model=%s, base_url=%s, error=%s",
                request.model, self._base_url or "default", e,
            )
            yield FinishEvent(reason="error")
            return

        # Emit usage
        if input_tokens or output_tokens:
            yield UsageInfo(
                usage=TokenUsage(
                    prompt_tokens=input_tokens,
                    completion_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                )
            )

        # Emit finish event
        normalized_reason = stop_reason or "stop"
        if normalized_reason == "end_turn":
            normalized_reason = "stop"
        elif normalized_reason == "max_tokens":
            normalized_reason = "length"
        yield FinishEvent(reason=normalized_reason)

    # ------------------------------------------------------------------
    # Embeddings (not supported)
    # ------------------------------------------------------------------

    def supports_embeddings(self) -> bool:
        return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        if self._client:
            try:
                await self._client.close()
            except Exception as e:
                logger.warning("Error closing Anthropic client: %s", e)
            finally:
                self._client = None

    def reset(self) -> None:
        """Sync reset — discard client without awaiting close."""
        self._client = None
