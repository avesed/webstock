"""LangGraph state definitions for multi-agent analysis workflow.

This module defines the state schema used by the LangGraph workflow
for coordinating multiple analysis agents and synthesis.
"""

from typing import Annotated, Any, Dict, List, Optional, TypedDict
from operator import add

from app.schemas.agent_analysis import (
    AgentAnalysisResult,
    ClarificationRequest,
)


class AnalysisState(TypedDict):
    """
    State for the multi-agent analysis workflow.

    This state is passed between nodes in the LangGraph workflow,
    accumulating results from each analysis agent and tracking
    the synthesis process.

    Attributes:
        symbol: Stock symbol being analyzed
        market: Market identifier (US, HK, CN, etc.)
        language: Output language ("en" or "zh")
        fundamental: Result from fundamental analysis agent
        technical: Result from technical analysis agent
        sentiment: Result from sentiment analysis agent
        news: Result from news analysis agent
        synthesis_output: Final synthesized analysis text
        clarification_requests: Requests for additional info from agents
        clarification_responses: Responses to clarification requests
        clarification_round: Current clarification round (max 2)
        stream_chunks: Accumulated stream chunks for SSE
        errors: Accumulated error messages
    """

    # Input parameters
    symbol: str
    market: str
    language: str  # "en" or "zh"

    # Analysis results from each agent
    fundamental: Optional[AgentAnalysisResult]
    technical: Optional[AgentAnalysisResult]
    sentiment: Optional[AgentAnalysisResult]
    news: Optional[AgentAnalysisResult]

    # Synthesis state
    synthesis_output: str
    clarification_requests: List[ClarificationRequest]
    clarification_responses: List[AgentAnalysisResult]
    clarification_round: int  # Max 2 rounds

    # Stream chunks for SSE (uses add reducer for accumulation)
    stream_chunks: Annotated[List[str], add]

    # Error tracking (uses add reducer for accumulation)
    errors: Annotated[List[str], add]


def create_initial_state(
    symbol: str,
    market: str,
    language: str = "en",
) -> AnalysisState:
    """
    Create the initial state for a new analysis workflow.

    Args:
        symbol: Stock symbol to analyze
        market: Market identifier (US, HK, CN, etc.)
        language: Output language ("en" or "zh")

    Returns:
        Initialized AnalysisState dict
    """
    return AnalysisState(
        symbol=symbol,
        market=market,
        language=language,
        fundamental=None,
        technical=None,
        sentiment=None,
        news=None,
        synthesis_output="",
        clarification_requests=[],
        clarification_responses=[],
        clarification_round=0,
        stream_chunks=[],
        errors=[],
    )


def get_completed_agents(state: AnalysisState) -> List[str]:
    """
    Get list of agents that have completed their analysis.

    Args:
        state: Current workflow state

    Returns:
        List of agent type names that have results
    """
    completed = []
    if state.get("fundamental") is not None:
        completed.append("fundamental")
    if state.get("technical") is not None:
        completed.append("technical")
    if state.get("sentiment") is not None:
        completed.append("sentiment")
    if state.get("news") is not None:
        completed.append("news")
    return completed


def get_successful_results(state: AnalysisState) -> List[AgentAnalysisResult]:
    """
    Get list of successful analysis results.

    Args:
        state: Current workflow state

    Returns:
        List of AgentAnalysisResult that succeeded
    """
    results = []
    for agent_type in ["fundamental", "technical", "sentiment", "news"]:
        result = state.get(agent_type)
        if result is not None and result.success:
            results.append(result)
    return results


def has_errors(state: AnalysisState) -> bool:
    """
    Check if the workflow has accumulated any errors.

    Args:
        state: Current workflow state

    Returns:
        True if there are errors
    """
    return len(state.get("errors", [])) > 0


def all_agents_failed(state: AnalysisState) -> bool:
    """
    Check if all agents failed to produce results.

    Args:
        state: Current workflow state

    Returns:
        True if all agents failed
    """
    for agent_type in ["fundamental", "technical", "sentiment", "news"]:
        result = state.get(agent_type)
        if result is not None and result.success:
            return False
    return True


# ---------------------------------------------------------------------------
# News Processing Pipeline State
# ---------------------------------------------------------------------------


class NewsProcessingState(TypedDict):
    """State for the per-article news processing pipeline (Layer 3).

    Content is pre-fetched by Layer 2 (batch_fetch_content) and saved
    to file storage. Scoring is done in Layer 1 (monitor tasks) and
    content cleaning in Layer 2. This Layer 3 pipeline reads the file,
    routes by processing_path, runs analysis or lightweight extraction,
    embeds, and updates the DB.
    """

    # Input parameters (set by caller / Celery task args)
    news_id: str
    url: str
    market: str
    symbol: str
    title: str
    summary: str
    published_at: Optional[str]  # ISO 8601
    source: Optional[str]  # News source name (e.g. 'reuters', 'eastmoney')
    file_path: Optional[str]  # Path to pre-fetched content JSON file

    # Scoring results (from Layer 1 scoring, passed via Celery task args)
    content_score: Optional[int]          # 0-300 content value score (from Layer 1, 3 agents x 0-100)
    processing_path: Optional[str]        # 'full_analysis' or 'lightweight' (from Layer 1)
    score_details: Optional[Dict[str, Any]]  # Dimension scores, reasoning, critical flag (from Layer 1)

    # Content read results (populated by read_file_node)
    full_text: Optional[str]
    word_count: int
    language: Optional[str]
    authors: Optional[List[str]]
    keywords: Optional[List[str]]

    # Image insights (read from JSON file, pre-extracted in Layer 2)
    image_insights: Optional[str]         # Multimodal LLM insights from images
    has_visual_data: bool                 # Whether article contains valuable visuals

    # Filter results
    filter_decision: str  # 'keep', 'delete', 'pending', 'skip'
    entities: Optional[List[Dict[str, Any]]]
    industry_tags: Optional[List[str]]
    event_tags: Optional[List[str]]
    sentiment_tag: Optional[str]
    investment_summary: Optional[str]
    detailed_summary: Optional[str]   # Complete summary preserving all key details
    analysis_report: Optional[str]    # Markdown-formatted professional analysis report

    # Embedding results
    chunks_total: int
    chunks_stored: int

    # Final status
    final_status: str  # 'embedded', 'deleted', 'failed', 'pending'
    error: Optional[str]

    # Cache statistics
    cache_metadata: Optional[Dict[str, Any]]  # Prompt cache hit/miss stats

    # Pipeline trace events (accumulated across nodes, flushed in update_db_node)
    trace_events: Annotated[List[Dict[str, Any]], add]


def create_news_processing_state(
    news_id: str,
    url: str,
    market: str = "US",
    symbol: str = "",
    title: str = "",
    summary: str = "",
    published_at: Optional[str] = None,
    source: str = "",
    file_path: Optional[str] = None,
    content_score: Optional[int] = None,
    processing_path: Optional[str] = None,
    score_details: Optional[dict] = None,
) -> NewsProcessingState:
    """Create initial state for the news processing pipeline.

    Args:
        content_score: 0-300 content score from Layer 1 scoring (3 agents x 0-100).
        processing_path: 'full_analysis' or 'lightweight' from Layer 1 scoring.
        score_details: Dimension scores, reasoning, critical flag from Layer 1.
    """
    return NewsProcessingState(
        news_id=news_id,
        url=url,
        market=market,
        symbol=symbol,
        title=title,
        summary=summary,
        published_at=published_at,
        source=source,
        file_path=file_path,
        content_score=content_score,
        processing_path=processing_path,
        score_details=score_details,
        full_text=None,
        word_count=0,
        language=None,
        authors=None,
        keywords=None,
        image_insights=None,
        has_visual_data=False,
        filter_decision="pending",
        entities=None,
        industry_tags=None,
        event_tags=None,
        sentiment_tag=None,
        investment_summary=None,
        detailed_summary=None,
        analysis_report=None,
        chunks_total=0,
        chunks_stored=0,
        final_status="pending",
        error=None,
        cache_metadata=None,
        trace_events=[],
    )
