"""LangGraph state definitions for multi-agent discussion workflow.

This module defines the state schema used by the Discussion Group workflow
for coordinating multi-round debate between specialist analysts under
the guidance of a Chief Strategist (moderator).
"""

from typing import Annotated, Any, Dict, List, Optional, TypedDict
from operator import add


class DiscussionMessage(TypedDict):
    """A single message in the discussion thread."""
    round: int           # 0=initial, 1+=debate, -1=synthesis
    agent_type: str      # fundamental, technical, sentiment, news, moderator, synthesis
    content: str
    structured_data: Optional[Dict[str, Any]]
    tool_calls: Optional[List[Dict[str, Any]]]
    token_count: Optional[int]
    latency_ms: Optional[int]


class DiscussionState(TypedDict):
    """State for the multi-agent discussion workflow.

    Attributes:
        symbol: Stock symbol being discussed
        market: Market identifier (US, HK, CN, etc.)
        language: Output language ("en" or "zh")
        session_id: UUID of the DiscussionSession row
        max_rounds: Maximum debate rounds (from admin config)
        shared_data: Pre-fetched skill results cache
        shared_data_summary: Compact text summary of key data points
        messages: Discussion thread (Annotated with add reducer for parallel writes)
        current_round: Current debate round number
        moderator_guidance: List of moderator guidance messages
        should_continue: Whether the discussion should continue
        target_agents: Agents to respond in the next round
        focus_topics: Topics for the next round
        synthesis_report: Final Markdown synthesis report
        compact_context: Condensed context for chat phase
        debate_data: Additional data fetched by agents during debate
        stream_chunks: Accumulated stream event markers
        total_tokens: Total tokens consumed
        total_latency_ms: Total latency
        errors: Accumulated error messages
    """

    # Input parameters
    symbol: str
    market: str
    language: str
    session_id: str  # UUID string

    # Configuration
    max_rounds: int

    # Shared data
    shared_data: Dict[str, Any]
    shared_data_summary: str

    # Discussion thread (accumulates via add reducer from parallel nodes)
    messages: Annotated[List[DiscussionMessage], add]

    # Debate control
    current_round: int
    moderator_guidance: Annotated[List[str], add]
    should_continue: bool
    target_agents: List[str]
    focus_topics: List[str]

    # Output
    synthesis_report: str
    compact_context: str

    # Extra data fetched during debate
    debate_data: Dict[str, Any]

    # Streaming markers
    stream_chunks: Annotated[List[str], add]

    # Metrics
    total_tokens: int
    total_latency_ms: int

    # Error tracking
    errors: Annotated[List[str], add]


def create_discussion_state(
    symbol: str,
    market: str,
    language: str,
    session_id: str,
    max_rounds: int = 3,
) -> DiscussionState:
    """Create the initial state for a new discussion workflow.

    Args:
        symbol: Stock symbol to discuss
        market: Market identifier
        language: Output language ("en" or "zh")
        session_id: UUID string of the DB session
        max_rounds: Maximum number of debate rounds

    Returns:
        Initialized DiscussionState dict
    """
    return DiscussionState(
        symbol=symbol,
        market=market,
        language=language,
        session_id=session_id,
        max_rounds=max_rounds,
        shared_data={},
        shared_data_summary="",
        messages=[],
        current_round=0,
        moderator_guidance=[],
        should_continue=True,
        target_agents=[],
        focus_topics=[],
        synthesis_report="",
        compact_context="",
        debate_data={},
        stream_chunks=[],
        total_tokens=0,
        total_latency_ms=0,
        errors=[],
    )
