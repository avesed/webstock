"""LangGraph workflow for multi-agent stock analysis.

This module defines the workflow graph that coordinates:
1. Parallel execution of 4 analysis agents
2. Result collection and conflict detection
3. Synthesis with optional clarification rounds
4. Final report generation

The workflow supports streaming output for SSE integration.
"""

import logging
from typing import Any, AsyncGenerator, Dict, Optional

from langgraph.graph import END, StateGraph

from app.agents.langgraph.nodes import (
    clarify_node,
    collect_node,
    fundamental_node,
    news_node,
    sentiment_node,
    synthesize_node,
    technical_node,
)
from app.agents.langgraph.nodes.clarify_node import should_clarify
from app.agents.langgraph.state import (
    AnalysisState,
    create_initial_state,
    get_successful_results,
)

logger = logging.getLogger(__name__)


def create_analysis_workflow() -> StateGraph:
    """
    Create the analysis workflow graph.

    The workflow has the following structure:

    __start__
        |
        +---> fundamental_analysis
        |           |
        +---> technical_analysis
        |           |              ---> collect_results ---> synthesize
        +---> sentiment_analysis   |                            |
        |           |              |                 +----------+----------+
        +---> news_analysis -------+                 |                     |
                                                should_clarify?       should_clarify?
                                                     |                     |
                                                 (clarify)             (end)
                                                     |
                                                synthesize (loop back)

    Returns:
        Compiled StateGraph ready for execution
    """
    # Create the workflow graph
    workflow = StateGraph(AnalysisState)

    # Add analysis nodes
    workflow.add_node("fundamental_analysis", fundamental_node)
    workflow.add_node("technical_analysis", technical_node)
    workflow.add_node("sentiment_analysis", sentiment_node)
    workflow.add_node("news_analysis", news_node)

    # Add collection node (synchronization point)
    workflow.add_node("collect_results", collect_node)

    # Add synthesis and clarification nodes
    workflow.add_node("synthesize", synthesize_node)
    workflow.add_node("clarify", clarify_node)

    # Define edges from start - parallel execution
    # All four analysis nodes run in parallel
    workflow.add_edge("__start__", "fundamental_analysis")
    workflow.add_edge("__start__", "technical_analysis")
    workflow.add_edge("__start__", "sentiment_analysis")
    workflow.add_edge("__start__", "news_analysis")

    # All analysis nodes feed into collect_results
    workflow.add_edge("fundamental_analysis", "collect_results")
    workflow.add_edge("technical_analysis", "collect_results")
    workflow.add_edge("sentiment_analysis", "collect_results")
    workflow.add_edge("news_analysis", "collect_results")

    # Collect -> synthesize
    workflow.add_edge("collect_results", "synthesize")

    # Synthesize -> conditional (clarify or end)
    workflow.add_conditional_edges(
        "synthesize",
        should_clarify,
        {
            "clarify": "clarify",
            "end": END,
        }
    )

    # Clarify -> back to synthesize
    workflow.add_edge("clarify", "synthesize")

    return workflow.compile()


# Module-level compiled workflow (singleton)
_compiled_workflow = None


def get_workflow() -> StateGraph:
    """
    Get the compiled workflow (singleton pattern).

    Returns:
        Compiled StateGraph instance
    """
    global _compiled_workflow
    if _compiled_workflow is None:
        _compiled_workflow = create_analysis_workflow()
    return _compiled_workflow


async def run_analysis(
    symbol: str,
    market: str,
    language: str = "en",
) -> AnalysisState:
    """
    Run the complete analysis workflow.

    This is the main entry point for non-streaming analysis.

    Args:
        symbol: Stock symbol to analyze
        market: Market identifier (US, HK, CN, etc.)
        language: Output language ("en" or "zh")

    Returns:
        Final AnalysisState with all results
    """
    workflow = get_workflow()
    initial_state = create_initial_state(symbol, market, language)

    logger.info(f"Starting analysis workflow for {symbol} ({market})")

    # Run the workflow to completion
    final_state = await workflow.ainvoke(initial_state)

    logger.info(f"Analysis workflow completed for {symbol}")

    return final_state


async def stream_analysis(
    symbol: str,
    market: str,
    language: str = "en",
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Run the analysis workflow with streaming output.

    This is the entry point for SSE streaming. It yields events
    as the workflow progresses.

    Args:
        symbol: Stock symbol to analyze
        market: Market identifier (US, HK, CN, etc.)
        language: Output language ("en" or "zh")

    Yields:
        Dict events with type and data for SSE
    """
    workflow = get_workflow()
    initial_state = create_initial_state(symbol, market, language)

    logger.info(f"Starting streaming analysis for {symbol} ({market})")

    # Yield start event
    yield {
        "type": "start",
        "data": {
            "symbol": symbol,
            "market": market,
            "language": language,
        }
    }

    # Track final state from streaming
    final_state = initial_state.copy()

    try:
        # Stream through the workflow
        # LangGraph's astream_events gives us fine-grained events
        async for event in workflow.astream_events(initial_state, version="v2"):
            event_type = event.get("event")
            event_name = event.get("name", "")

            # Capture final state from the last output
            if event_type == "on_chain_end" and event_name == "LangGraph":
                # This is the final workflow completion event
                output = event.get("data", {}).get("output", {})
                if output:
                    final_state.update(output)

            # Node start events
            if event_type == "on_chain_start":
                if event_name in ["fundamental_analysis", "technical_analysis",
                                  "sentiment_analysis", "news_analysis"]:
                    yield {
                        "type": "agent_start",
                        "data": {
                            "agent": event_name.replace("_analysis", ""),
                        }
                    }
                elif event_name == "synthesize":
                    yield {
                        "type": "synthesis_start",
                        "data": {}
                    }
                elif event_name == "clarify":
                    yield {
                        "type": "clarification_start",
                        "data": {}
                    }

            # Emit analysis_phase_complete when collect_results finishes
            # This signals that all agents are done and synthesis is about to start
            elif event_type == "on_chain_end" and event_name == "collect_results":
                yield {
                    "type": "analysis_phase_complete",
                    "data": {}
                }

            # Node completion events
            elif event_type == "on_chain_end":
                output = event.get("data", {}).get("output", {})

                if event_name in ["fundamental_analysis", "technical_analysis",
                                  "sentiment_analysis", "news_analysis"]:
                    agent_type = event_name.replace("_analysis", "")
                    result = output.get(agent_type)

                    if result:
                        yield {
                            "type": "agent_complete",
                            "data": {
                                "agent": agent_type,
                                "success": result.success,
                                "latency_ms": result.latency_ms,
                                "tokens_used": result.tokens_used,
                                "error": result.error if not result.success else None,
                            }
                        }

                elif event_name == "synthesize":
                    synthesis_output = output.get("synthesis_output", "")
                    clarification_requests = output.get("clarification_requests", [])
                    stream_chunks = output.get("stream_chunks", [])

                    # If we have clarification requests but no synthesis output,
                    # this means we're deferring synthesis until after clarification
                    if clarification_requests and not synthesis_output:
                        # Send placeholder message from stream_chunks
                        for chunk in stream_chunks:
                            yield {
                                "type": "synthesis_pending",
                                "data": {
                                    "message": chunk,
                                }
                            }
                    elif synthesis_output:
                        # Yield synthesis chunks (normal case or after clarification)
                        chunk_size = 100
                        for i in range(0, len(synthesis_output), chunk_size):
                            chunk = synthesis_output[i:i+chunk_size]
                            yield {
                                "type": "synthesis_chunk",
                                "data": {
                                    "content": chunk,
                                }
                            }

                    # Notify about clarification if needed
                    if clarification_requests:
                        yield {
                            "type": "clarification_needed",
                            "data": {
                                "count": len(clarification_requests),
                                "requests": [
                                    {
                                        "type": req.clarification_type.value,
                                        "question": req.question,
                                    }
                                    for req in clarification_requests
                                ],
                            }
                        }

                elif event_name == "clarify":
                    responses = output.get("clarification_responses", [])
                    yield {
                        "type": "clarification_complete",
                        "data": {
                            "count": len(responses),
                        }
                    }

            # Stream chunks from state updates
            elif event_type == "on_chain_stream":
                output = event.get("data", {})
                if "stream_chunks" in output:
                    for chunk in output["stream_chunks"]:
                        yield {
                            "type": "chunk",
                            "data": {"content": chunk}
                        }

        # Yield completion event (final_state captured from on_chain_end event above)
        results = get_successful_results(final_state)
        yield {
            "type": "complete",
            "data": {
                "symbol": symbol,
                "agents_completed": len(results),
                "synthesis_output": final_state.get("synthesis_output", ""),
                "errors": final_state.get("errors", []),
            }
        }

    except Exception as e:
        # Log full error details for debugging, but return generic message to client
        logger.exception(f"Streaming analysis error for {symbol}: {e}")
        yield {
            "type": "error",
            "data": {
                "message": "An error occurred during analysis. Please try again later.",
            }
        }


async def run_single_agent(
    agent_type: str,
    symbol: str,
    market: str,
    language: str = "en",
) -> Dict[str, Any]:
    """
    Run a single analysis agent.

    Useful for testing or when only one type of analysis is needed.

    Args:
        agent_type: Type of agent ("fundamental", "technical", "sentiment", "news")
        symbol: Stock symbol to analyze
        market: Market identifier
        language: Output language

    Returns:
        Agent result dict
    """
    from app.agents.langgraph.nodes import (
        fundamental_node,
        news_node,
        sentiment_node,
        technical_node,
    )

    agents = {
        "fundamental": fundamental_node,
        "technical": technical_node,
        "sentiment": sentiment_node,
        "news": news_node,
    }

    node_fn = agents.get(agent_type)
    if not node_fn:
        raise ValueError(f"Unknown agent type: {agent_type}")

    state = create_initial_state(symbol, market, language)
    result = await node_fn(state)

    return result


def get_workflow_info() -> Dict[str, Any]:
    """
    Get information about the workflow configuration.

    Useful for debugging and monitoring.

    Returns:
        Dict with workflow information
    """
    return {
        "nodes": [
            "fundamental_analysis",
            "technical_analysis",
            "sentiment_analysis",
            "news_analysis",
            "collect_results",
            "synthesize",
            "clarify",
        ],
        "parallel_nodes": [
            "fundamental_analysis",
            "technical_analysis",
            "sentiment_analysis",
            "news_analysis",
        ],
        "max_clarification_rounds": 2,
        "supports_streaming": True,
    }
