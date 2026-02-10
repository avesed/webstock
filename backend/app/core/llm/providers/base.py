"""Abstract base class for LLM providers.

Each provider translates gateway types to its native API format and
handles all protocol-specific concerns internally (tool format, streaming
delta accumulation, reasoning model detection, etc.).
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator

from app.core.llm.types import (
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    StreamEvent,
)


class LLMProvider(ABC):
    """Abstract base for LLM API providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique provider identifier, e.g. 'openai', 'anthropic'."""
        ...

    @abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Non-streaming chat completion."""
        ...

    @abstractmethod
    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        """Streaming chat completion. Yields StreamEvent objects."""
        ...

    @abstractmethod
    def supports_embeddings(self) -> bool:
        """Whether this provider supports embedding generation."""
        ...

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Generate embeddings. Override in providers that support it."""
        raise NotImplementedError(
            f"Provider {self.provider_name} does not support embeddings"
        )

    async def close(self) -> None:
        """Clean up resources (close HTTP clients)."""
        pass

    def reset(self) -> None:
        """Sync reset for Celery workers.

        Discards cached clients to avoid holding references to a closed
        event loop's httpx connections.
        """
        pass
