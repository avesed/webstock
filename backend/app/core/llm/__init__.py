"""LLM Gateway — unified internal API for all LLM interactions.

Usage:
    from app.core.llm import get_llm_gateway, ChatRequest, Message, Role

    gateway = get_llm_gateway()
    response = await gateway.chat(ChatRequest(
        model="gpt-4o-mini",
        messages=[Message(role=Role.USER, content="Hello")],
    ))
"""

from app.core.llm.config import ProviderConfig, ProviderType
from app.core.llm.gateway import LLMGateway, get_llm_gateway, reset_llm_gateway
from app.core.llm.langchain_bridge import (
    get_analysis_langchain_model,
    get_chat_model_config,
    get_langchain_model,
    get_synthesis_langchain_model,
)
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
    ToolResult,
    UsageInfo,
)

__all__ = [
    # Gateway
    "LLMGateway",
    "get_llm_gateway",
    "reset_llm_gateway",
    # Types
    "ChatRequest",
    "ChatResponse",
    "ContentDelta",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "FinishEvent",
    "Message",
    "Role",
    "StreamEvent",
    "TokenUsage",
    "ToolCall",
    "ToolCallDelta",
    "ToolDefinition",
    "ToolResult",
    "UsageInfo",
    # Config
    "ProviderConfig",
    "ProviderType",
    # LangChain bridge
    "get_analysis_langchain_model",
    "get_chat_model_config",
    "get_langchain_model",
    "get_synthesis_langchain_model",
]
