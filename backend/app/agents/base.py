"""Base class for AI analysis agents."""

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Dict, List, Optional

from openai import AsyncOpenAI
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.core.openai_client import get_openai_client, get_openai_model
from app.core.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    get_circuit_breaker,
)
from app.core.token_bucket import TokenBucket, get_analysis_rate_limiter

logger = logging.getLogger(__name__)

# Timeout for streaming response iteration (in seconds)
STREAMING_CHUNK_TIMEOUT = 60  # Maximum time to wait for a single chunk
STREAMING_TOTAL_TIMEOUT = 180  # Maximum total time for streaming (3 minutes)


class AgentType(str, Enum):
    """Types of analysis agents."""

    FUNDAMENTAL = "fundamental"
    TECHNICAL = "technical"
    SENTIMENT = "sentiment"
    NEWS = "news"


@dataclass
class AgentResult:
    """Result from an agent analysis."""

    agent_type: AgentType
    symbol: str
    market: str
    success: bool
    content: Optional[str] = None
    structured_data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    tokens_used: int = 0
    latency_ms: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "agent_type": self.agent_type.value,
            "symbol": self.symbol,
            "market": self.market,
            "success": self.success,
            "content": self.content,
            "structured_data": self.structured_data,
            "error": self.error,
            "tokens_used": self.tokens_used,
            "latency_ms": self.latency_ms,
            "timestamp": self.timestamp,
        }


@dataclass
class StreamChunk:
    """A chunk of streaming response."""

    agent_type: AgentType
    content: str
    is_complete: bool = False
    structured_data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for SSE."""
        return {
            "agent_type": self.agent_type.value,
            "content": self.content,
            "is_complete": self.is_complete,
            "structured_data": self.structured_data,
            "error": self.error,
        }


class BaseAgent(ABC):
    """
    Abstract base class for AI analysis agents.

    Provides:
    - OpenAI API integration with streaming support
    - Token counting for rate limiting
    - Circuit breaker integration
    - Retry logic with exponential backoff
    - Structured error handling

    Subclasses must implement:
    - agent_type property
    - get_system_prompt()
    - build_user_prompt()
    - prepare_data()
    """

    def __init__(
        self,
        rate_limiter: Optional[TokenBucket] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        """
        Initialize the agent.

        Args:
            rate_limiter: Token bucket for rate limiting (shared across agents)
            circuit_breaker: Circuit breaker for fault tolerance
        """
        self._rate_limiter = rate_limiter
        self._circuit_breaker = circuit_breaker

    @property
    @abstractmethod
    def agent_type(self) -> AgentType:
        """Return the type of this agent."""
        pass

    @abstractmethod
    def get_system_prompt(self, market: str) -> str:
        """
        Get the system prompt for this agent.

        Args:
            market: Market identifier (us, hk, sh, sz)

        Returns:
            System prompt string
        """
        pass

    @abstractmethod
    async def build_user_prompt(
        self,
        symbol: str,
        market: str,
        data: Dict[str, Any],
    ) -> str:
        """
        Build the user prompt with stock data.

        Args:
            symbol: Stock symbol
            market: Market identifier
            data: Prepared data for analysis

        Returns:
            User prompt string
        """
        pass

    @abstractmethod
    async def prepare_data(
        self,
        symbol: str,
        market: str,
    ) -> Dict[str, Any]:
        """
        Prepare data for analysis.

        Args:
            symbol: Stock symbol
            market: Market identifier

        Returns:
            Dictionary of prepared data
        """
        pass

    async def _get_client(self) -> AsyncOpenAI:
        """Get OpenAI client via shared manager."""
        return get_openai_client()

    async def _get_rate_limiter(self) -> TokenBucket:
        """Get rate limiter for analysis agents."""
        if self._rate_limiter is None:
            self._rate_limiter = await get_analysis_rate_limiter()
        return self._rate_limiter

    async def _get_circuit_breaker(self) -> CircuitBreaker:
        """Get circuit breaker."""
        if self._circuit_breaker is None:
            self._circuit_breaker = await get_circuit_breaker(
                "openai",
                CircuitBreakerConfig(
                    failure_threshold=5,
                    recovery_timeout=60.0,
                ),
            )
        return self._circuit_breaker

    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for text.

        Simple estimation: ~4 characters per token for English,
        ~2 characters per token for Chinese.
        """
        # Count Chinese characters
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other_chars = len(text) - chinese_chars

        # Estimate tokens
        return (chinese_chars // 2) + (other_chars // 4) + 1

    @retry(
        retry=retry_if_exception_type((
            TimeoutError,
            ConnectionError,
            RateLimitError,
            APIConnectionError,
            APITimeoutError,
            # Retry on 5xx server errors but not on 4xx client errors
        )),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _call_openai(
        self,
        system_prompt: str,
        user_prompt: str,
        stream: bool = False,
    ) -> Any:
        """
        Call OpenAI API with retry logic.

        Args:
            system_prompt: System message
            user_prompt: User message
            stream: Whether to stream the response

        Returns:
            API response or async generator for streaming
        """
        client = await self._get_client()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        model = get_openai_model()

        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=settings.OPENAI_MAX_TOKENS,
            temperature=0.7,
            stream=stream,
        )

        return response

    async def analyze(
        self,
        symbol: str,
        market: str,
    ) -> AgentResult:
        """
        Perform analysis on a stock.

        Args:
            symbol: Stock symbol
            market: Market identifier (us, hk, sh, sz)

        Returns:
            AgentResult with analysis
        """
        start_time = time.time()

        try:
            # Check rate limit
            rate_limiter = await self._get_rate_limiter()
            if not await rate_limiter.acquire():
                return AgentResult(
                    agent_type=self.agent_type,
                    symbol=symbol,
                    market=market,
                    success=False,
                    error="Rate limit exceeded. Please try again later.",
                )

            # Check circuit breaker
            circuit_breaker = await self._get_circuit_breaker()

            # Prepare data
            data = await self.prepare_data(symbol, market)

            # Build prompts
            system_prompt = self.get_system_prompt(market)
            user_prompt = await self.build_user_prompt(symbol, market, data)

            # Estimate tokens for logging
            prompt_tokens = self._estimate_tokens(system_prompt + user_prompt)
            logger.info(
                f"Agent {self.agent_type.value}: analyzing {symbol} "
                f"(estimated prompt tokens: {prompt_tokens})"
            )

            # Call OpenAI through circuit breaker
            response = await circuit_breaker.call(
                self._call_openai,
                system_prompt,
                user_prompt,
                stream=False,
            )

            # Extract response
            content = response.choices[0].message.content
            tokens_used = response.usage.total_tokens if response.usage else 0

            # Try to parse structured data
            structured_data = self._parse_structured_response(content)

            latency_ms = int((time.time() - start_time) * 1000)

            return AgentResult(
                agent_type=self.agent_type,
                symbol=symbol,
                market=market,
                success=True,
                content=content,
                structured_data=structured_data,
                tokens_used=tokens_used,
                latency_ms=latency_ms,
            )

        except CircuitBreakerOpenError as e:
            logger.error(f"Circuit breaker open for {self.agent_type.value}: {e}")
            return AgentResult(
                agent_type=self.agent_type,
                symbol=symbol,
                market=market,
                success=False,
                error="Service temporarily unavailable. Please try again later.",
                latency_ms=int((time.time() - start_time) * 1000),
            )

        except Exception as e:
            logger.exception(f"Agent {self.agent_type.value} error for {symbol}: {e}")
            # Return generic error message to client, detailed error is logged
            return AgentResult(
                agent_type=self.agent_type,
                symbol=symbol,
                market=market,
                success=False,
                error="Analysis failed. Please try again later.",
                latency_ms=int((time.time() - start_time) * 1000),
            )

    async def analyze_stream(
        self,
        symbol: str,
        market: str,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        Perform streaming analysis on a stock.

        Args:
            symbol: Stock symbol
            market: Market identifier (us, hk, sh, sz)

        Yields:
            StreamChunk objects with partial results
        """
        try:
            # Check rate limit
            rate_limiter = await self._get_rate_limiter()
            if not await rate_limiter.acquire():
                yield StreamChunk(
                    agent_type=self.agent_type,
                    content="",
                    is_complete=True,
                    error="Rate limit exceeded. Please try again later.",
                )
                return

            # Check circuit breaker
            circuit_breaker = await self._get_circuit_breaker()
            if circuit_breaker.is_open:
                yield StreamChunk(
                    agent_type=self.agent_type,
                    content="",
                    is_complete=True,
                    error="Service temporarily unavailable. Please try again later.",
                )
                return

            # Prepare data
            data = await self.prepare_data(symbol, market)

            # Build prompts
            system_prompt = self.get_system_prompt(market)
            user_prompt = await self.build_user_prompt(symbol, market, data)

            logger.info(f"Agent {self.agent_type.value}: streaming analysis for {symbol}")

            # Stream from OpenAI with timeout protection
            try:
                response = await circuit_breaker.call(
                    self._call_openai,
                    system_prompt,
                    user_prompt,
                    stream=True,
                )

                full_content = ""
                start_time = time.time()

                # Iterate with timeout protection
                try:
                    async with asyncio.timeout(STREAMING_TOTAL_TIMEOUT):
                        async for chunk in response:
                            # Check total timeout
                            if time.time() - start_time > STREAMING_TOTAL_TIMEOUT:
                                logger.warning(
                                    f"Streaming total timeout reached for {self.agent_type.value}"
                                )
                                yield StreamChunk(
                                    agent_type=self.agent_type,
                                    content="",
                                    is_complete=True,
                                    error="Analysis timeout. Please try again.",
                                )
                                return

                            if chunk.choices and chunk.choices[0].delta.content:
                                content = chunk.choices[0].delta.content
                                full_content += content
                                yield StreamChunk(
                                    agent_type=self.agent_type,
                                    content=content,
                                    is_complete=False,
                                )

                except asyncio.TimeoutError:
                    logger.warning(
                        f"Streaming timeout for {self.agent_type.value} on {symbol}"
                    )
                    yield StreamChunk(
                        agent_type=self.agent_type,
                        content="",
                        is_complete=True,
                        error="Analysis timeout. Please try again.",
                    )
                    return

                # Parse structured data from complete response
                structured_data = self._parse_structured_response(full_content)

                yield StreamChunk(
                    agent_type=self.agent_type,
                    content="",
                    is_complete=True,
                    structured_data=structured_data,
                )

            except CircuitBreakerOpenError:
                yield StreamChunk(
                    agent_type=self.agent_type,
                    content="",
                    is_complete=True,
                    error="Service temporarily unavailable. Please try again later.",
                )

        except Exception as e:
            logger.exception(f"Agent {self.agent_type.value} streaming error: {e}")
            # Return generic error message to client, detailed error is logged
            yield StreamChunk(
                agent_type=self.agent_type,
                content="",
                is_complete=True,
                error="Analysis failed. Please try again later.",
            )

    def _parse_structured_response(
        self,
        content: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Try to parse structured JSON from response.

        Args:
            content: Response content

        Returns:
            Parsed dictionary or None
        """
        try:
            # Try to find JSON in the response
            # Look for JSON block markers
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                if end > start:
                    json_str = content[start:end].strip()
                    return json.loads(json_str)

            # Try to find JSON object directly
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = content[start:end]
                return json.loads(json_str)

        except json.JSONDecodeError:
            logger.debug(f"Could not parse JSON from response for {self.agent_type.value}")

        return None
