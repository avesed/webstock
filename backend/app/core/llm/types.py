"""Provider-agnostic types for the LLM gateway.

All LLM consumers use these types instead of provider-specific ones.
StreamEvent uses a tagged union pattern for type-safe event dispatch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Role(str, Enum):
    """Message role."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


# ---------------------------------------------------------------------------
# Tool types
# ---------------------------------------------------------------------------

@dataclass
class ToolDefinition:
    """Provider-agnostic tool definition (function calling schema)."""
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema


@dataclass
class ToolCall:
    """A complete tool call from the assistant."""
    id: str
    name: str
    arguments: str  # Raw JSON string (let consumer parse)


@dataclass
class ToolResult:
    """Result of executing a tool."""
    tool_call_id: str
    content: str


# ---------------------------------------------------------------------------
# Message types
# ---------------------------------------------------------------------------

@dataclass
class Message:
    """Provider-agnostic chat message.

    content can be:
      - str: Plain text message
      - List[Dict]: Multimodal content parts, e.g.
        [{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "..."}}]
    """
    role: Role
    content: Optional[Union[str, List[Dict[str, Any]]]] = None
    tool_calls: Optional[List[ToolCall]] = None
    # For tool result messages:
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    # Prompt caching hint (provider-specific behavior):
    cache_control: Optional[Dict[str, str]] = None


# ---------------------------------------------------------------------------
# Token usage
# ---------------------------------------------------------------------------

@dataclass
class TokenUsage:
    """Token usage statistics."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0  # Prompt cache hit tokens (OpenAI)


# ---------------------------------------------------------------------------
# Request types
# ---------------------------------------------------------------------------

@dataclass
class ChatRequest:
    """Provider-agnostic chat completion request.

    Provider-specific parameters (reasoning model detection, stream_options,
    max_completion_tokens) are handled internally by each provider.
    """
    messages: List[Message]
    model: str
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    tools: Optional[List[ToolDefinition]] = None
    tool_choice: Optional[str] = None   # "auto", "none", "required"
    stream: bool = False
    response_format: Optional[Dict[str, Any]] = None  # {"type": "json_object"} or {"type": "json_schema", ...}
    timeout: int = 120
    extra: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------

@dataclass
class ChatResponse:
    """Provider-agnostic chat completion response (non-streaming)."""
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    finish_reason: Optional[str] = None  # "stop", "tool_use", "length"
    model: Optional[str] = None
    usage: Optional[TokenUsage] = None


# ---------------------------------------------------------------------------
# Streaming event types (tagged union)
# ---------------------------------------------------------------------------

@dataclass
class ContentDelta:
    """Streamed text content fragment."""
    text: str


@dataclass
class ToolCallDelta:
    """A fully-assembled tool call (provider accumulates partial deltas)."""
    tool_call: ToolCall


@dataclass
class UsageInfo:
    """Token usage information (typically at end of stream)."""
    usage: TokenUsage


@dataclass
class FinishEvent:
    """Stream completion signal."""
    reason: str  # "stop", "tool_use", "length"
    tools_supported: bool = True  # False when DeepSeek XML detection fires


# Tagged union type — consumers dispatch with isinstance() or match
StreamEvent = Union[ContentDelta, ToolCallDelta, UsageInfo, FinishEvent]


# ---------------------------------------------------------------------------
# Embedding types
# ---------------------------------------------------------------------------

@dataclass
class EmbeddingRequest:
    """Provider-agnostic embedding request."""
    input: Union[str, List[str]]
    model: str
    dimensions: Optional[int] = None


@dataclass
class EmbeddingResponse:
    """Provider-agnostic embedding response."""
    embeddings: List[List[float]]
    model: str
    usage: Optional[TokenUsage] = None
