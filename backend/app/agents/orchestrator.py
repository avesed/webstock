"""Agent orchestrator for coordinating multiple analysis agents."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from app.agents.base import AgentResult, AgentType, BaseAgent, StreamChunk
from app.agents.fundamental import FundamentalAgent
from app.agents.news import NewsAgent
from app.agents.sentiment import SentimentAgent
from app.agents.technical import TechnicalAgent
from app.core.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    get_circuit_breaker,
)
from app.core.token_bucket import TokenBucket, TokenBucketConfig, get_token_bucket

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorResult:
    """Result from orchestrated analysis."""

    symbol: str
    market: str
    results: Dict[str, AgentResult]
    total_tokens: int = 0
    total_latency_ms: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "symbol": self.symbol,
            "market": self.market,
            "results": {
                agent_type: result.to_dict()
                for agent_type, result in self.results.items()
            },
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
            "timestamp": self.timestamp,
            "summary": self._generate_summary(),
        }

    def _generate_summary(self) -> Dict[str, Any]:
        """Generate a summary of all agent results."""
        summary = {
            "successful_agents": [],
            "failed_agents": [],
            "recommendations": {},
        }

        for agent_type, result in self.results.items():
            if result.success:
                summary["successful_agents"].append(agent_type)
                # Extract recommendation from structured data
                if result.structured_data and "recommendation" in result.structured_data:
                    summary["recommendations"][agent_type] = result.structured_data["recommendation"]
            else:
                summary["failed_agents"].append(agent_type)

        return summary


class AgentOrchestrator:
    """
    Orchestrates multiple analysis agents with rate limiting and circuit breaking.

    Features:
    - Parallel execution of multiple agents
    - Shared rate limiting across agents
    - Circuit breaker for fault tolerance
    - Streaming support for SSE
    - Result aggregation

    Usage:
        orchestrator = await create_orchestrator()
        result = await orchestrator.analyze("AAPL", "us")

        # Or with streaming:
        async for chunk in orchestrator.analyze_stream("AAPL", "us"):
            print(chunk)
    """

    def __init__(
        self,
        rate_limiter: TokenBucket,
        circuit_breaker: CircuitBreaker,
        agents: Optional[Dict[AgentType, BaseAgent]] = None,
    ):
        """
        Initialize the orchestrator.

        Args:
            rate_limiter: Shared rate limiter for OpenAI API
            circuit_breaker: Shared circuit breaker for fault tolerance
            agents: Optional pre-configured agents
        """
        self._rate_limiter = rate_limiter
        self._circuit_breaker = circuit_breaker
        self._agents: Dict[AgentType, BaseAgent] = agents or {}

    async def _ensure_agents(self) -> None:
        """Ensure all agents are initialized."""
        if AgentType.FUNDAMENTAL not in self._agents:
            self._agents[AgentType.FUNDAMENTAL] = FundamentalAgent(
                rate_limiter=self._rate_limiter,
                circuit_breaker=self._circuit_breaker,
            )
        if AgentType.TECHNICAL not in self._agents:
            self._agents[AgentType.TECHNICAL] = TechnicalAgent(
                rate_limiter=self._rate_limiter,
                circuit_breaker=self._circuit_breaker,
            )
        if AgentType.SENTIMENT not in self._agents:
            self._agents[AgentType.SENTIMENT] = SentimentAgent(
                rate_limiter=self._rate_limiter,
                circuit_breaker=self._circuit_breaker,
            )
        if AgentType.NEWS not in self._agents:
            self._agents[AgentType.NEWS] = NewsAgent(
                rate_limiter=self._rate_limiter,
                circuit_breaker=self._circuit_breaker,
            )

    async def analyze(
        self,
        symbol: str,
        market: str,
        agent_types: Optional[List[AgentType]] = None,
    ) -> OrchestratorResult:
        """
        Run analysis with multiple agents in parallel.

        Args:
            symbol: Stock symbol
            market: Market identifier (us, hk, sh, sz)
            agent_types: Specific agents to run (default: all)

        Returns:
            OrchestratorResult with all agent results
        """
        await self._ensure_agents()

        start_time = time.time()

        # Determine which agents to run
        if agent_types is None:
            agent_types = [AgentType.FUNDAMENTAL, AgentType.TECHNICAL, AgentType.SENTIMENT, AgentType.NEWS]

        # Run agents in parallel
        tasks = []
        for agent_type in agent_types:
            if agent_type in self._agents:
                agent = self._agents[agent_type]
                tasks.append(agent.analyze(symbol, market))

        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        results: Dict[str, AgentResult] = {}
        total_tokens = 0

        for i, result in enumerate(results_list):
            agent_type = agent_types[i]
            if isinstance(result, Exception):
                logger.error(f"Agent {agent_type.value} failed with exception: {result}")
                # Return generic error message to client, detailed error is logged
                results[agent_type.value] = AgentResult(
                    agent_type=agent_type,
                    symbol=symbol,
                    market=market,
                    success=False,
                    error="Analysis failed. Please try again later.",
                )
            else:
                results[agent_type.value] = result
                total_tokens += result.tokens_used

        total_latency = int((time.time() - start_time) * 1000)

        return OrchestratorResult(
            symbol=symbol,
            market=market,
            results=results,
            total_tokens=total_tokens,
            total_latency_ms=total_latency,
        )

    async def analyze_single(
        self,
        symbol: str,
        market: str,
        agent_type: AgentType,
    ) -> AgentResult:
        """
        Run analysis with a single agent.

        Args:
            symbol: Stock symbol
            market: Market identifier
            agent_type: Type of agent to run

        Returns:
            AgentResult from the specified agent
        """
        await self._ensure_agents()

        if agent_type not in self._agents:
            return AgentResult(
                agent_type=agent_type,
                symbol=symbol,
                market=market,
                success=False,
                error=f"Agent type {agent_type.value} not available",
            )

        agent = self._agents[agent_type]
        return await agent.analyze(symbol, market)

    async def analyze_stream(
        self,
        symbol: str,
        market: str,
        agent_types: Optional[List[AgentType]] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Stream analysis from multiple agents.

        Yields SSE-formatted events with agent progress and results.

        Args:
            symbol: Stock symbol
            market: Market identifier
            agent_types: Specific agents to run (default: all)

        Yields:
            SSE-formatted strings (data: {...}\n\n)
        """
        await self._ensure_agents()

        # Determine which agents to run
        if agent_types is None:
            agent_types = [AgentType.FUNDAMENTAL, AgentType.TECHNICAL, AgentType.SENTIMENT, AgentType.NEWS]

        # Send start event
        yield self._format_sse_event({
            "type": "start",
            "symbol": symbol,
            "market": market,
            "agents": [at.value for at in agent_types],
            "timestamp": time.time(),
        })

        # Track completed agents
        completed: Set[AgentType] = set()
        all_results: Dict[str, AgentResult] = {}
        total_tokens = 0
        start_time = time.time()

        # Create streaming tasks for each agent
        async def stream_agent(agent_type: AgentType):
            """Stream from a single agent and yield formatted events."""
            if agent_type not in self._agents:
                return

            agent = self._agents[agent_type]
            full_content = ""

            async for chunk in agent.analyze_stream(symbol, market):
                if chunk.error:
                    yield self._format_sse_event({
                        "type": "agent_error",
                        "agent": agent_type.value,
                        "error": chunk.error,
                        "timestamp": time.time(),
                    })
                    return

                if not chunk.is_complete:
                    full_content += chunk.content
                    yield self._format_sse_event({
                        "type": "agent_chunk",
                        "agent": agent_type.value,
                        "content": chunk.content,
                        "timestamp": time.time(),
                    })
                else:
                    # Agent complete
                    yield self._format_sse_event({
                        "type": "agent_complete",
                        "agent": agent_type.value,
                        "structured_data": chunk.structured_data,
                        "timestamp": time.time(),
                    })

        # Run agents sequentially for cleaner streaming output
        # (parallel streaming can be complex to manage in SSE)
        for agent_type in agent_types:
            # Announce agent start
            yield self._format_sse_event({
                "type": "agent_start",
                "agent": agent_type.value,
                "timestamp": time.time(),
            })

            # Stream agent output
            try:
                async for event in stream_agent(agent_type):
                    yield event
                completed.add(agent_type)
            except Exception as e:
                logger.error(f"Error streaming {agent_type.value}: {e}")
                # Return generic error message to client, detailed error is logged
                yield self._format_sse_event({
                    "type": "agent_error",
                    "agent": agent_type.value,
                    "error": "Analysis failed. Please try again later.",
                    "timestamp": time.time(),
                })

        # Send completion event
        yield self._format_sse_event({
            "type": "complete",
            "symbol": symbol,
            "market": market,
            "completed_agents": [at.value for at in completed],
            "total_latency_ms": int((time.time() - start_time) * 1000),
            "timestamp": time.time(),
        })

    def _format_sse_event(self, data: Dict[str, Any]) -> str:
        """Format data as SSE event."""
        return f"data: {json.dumps(data)}\n\n"

    async def get_status(self) -> Dict[str, Any]:
        """Get orchestrator status."""
        return {
            "rate_limiter": {
                "available_tokens": await self._rate_limiter.get_available_tokens(),
            },
            "circuit_breaker": self._circuit_breaker.get_status(),
            "agents": {
                agent_type.value: True
                for agent_type in self._agents.keys()
            },
        }


# Global orchestrator instance
_orchestrator: Optional[AgentOrchestrator] = None
_orchestrator_lock = asyncio.Lock()


async def create_orchestrator() -> AgentOrchestrator:
    """
    Create or get the shared orchestrator instance.

    Returns:
        AgentOrchestrator instance with shared rate limiter and circuit breaker
    """
    global _orchestrator

    if _orchestrator is None:
        async with _orchestrator_lock:
            if _orchestrator is None:
                # Get shared rate limiter
                from app.config import settings

                rate_limiter = await get_token_bucket(
                    "openai",
                    TokenBucketConfig(
                        max_tokens=settings.OPENAI_RATE_LIMIT,
                        refill_rate=settings.OPENAI_RATE_LIMIT / 60.0,
                        key_prefix="token_bucket:api",
                    ),
                )

                # Get shared circuit breaker
                circuit_breaker = await get_circuit_breaker(
                    "openai",
                    CircuitBreakerConfig(
                        failure_threshold=5,
                        recovery_timeout=60.0,
                    ),
                )

                _orchestrator = AgentOrchestrator(
                    rate_limiter=rate_limiter,
                    circuit_breaker=circuit_breaker,
                )

                logger.info("Agent orchestrator initialized")

    return _orchestrator


async def cleanup_orchestrator() -> None:
    """Cleanup orchestrator resources."""
    global _orchestrator
    _orchestrator = None
