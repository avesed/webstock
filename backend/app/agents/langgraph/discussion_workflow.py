"""LangGraph workflow for multi-agent discussion group.

Graph topology:
    START → fetch_discussion_data
          → fundamental_initial → technical_initial → sentiment_initial → news_initial (sequential)
          → moderator_review
          → should_continue?
              YES → agent_respond → moderator_review (loop)
              NO  → final_synthesis → END
"""

import logging
import re
from typing import Any, AsyncGenerator, Dict

from langgraph.graph import END, StateGraph

from app.agents.langgraph.discussion_state import (
    DiscussionState,
    create_discussion_state,
)
from app.agents.langgraph.nodes.discussion_nodes import (
    agent_respond_node,
    fetch_discussion_data_node,
    final_synthesis_node,
    fundamental_initial_node,
    moderator_review_node,
    news_initial_node,
    sentiment_initial_node,
    technical_initial_node,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Utility: strip JSON blocks from agent/moderator output
# =============================================================================


def strip_json_blocks(text: str) -> str:
    """Remove trailing JSON code blocks from message content for SSE display.

    Agents may output optional JSON metrics and the moderator outputs a required
    JSON control block. These are useful for internal processing but should not
    be rendered in the chat UI.
    """
    if not text:
        return ""
    # Strip ```json {...} ``` code blocks at the end
    cleaned = re.sub(r'\s*```(?:json)?\s*\{.*?\}\s*```\s*$', '', text, flags=re.DOTALL)
    if cleaned != text:
        logger.debug("strip_json_blocks: 移除 %d 字符的JSON代码块", len(text) - len(cleaned.strip()))
        return cleaned.strip()
    # Strip bare JSON at end with known keys
    cleaned = re.sub(r'\s*\{[^{}]*"(?:action|key_metrics|signal)"[^{}]*\}\s*$', '', text, flags=re.DOTALL)
    if cleaned != text:
        logger.debug("strip_json_blocks: 移除 %d 字符的裸JSON", len(text) - len(cleaned.strip()))
    return cleaned.strip()


# =============================================================================
# Conditional edge: should the discussion continue?
# =============================================================================


def _should_continue_discussion(state: DiscussionState) -> str:
    """Decide whether to continue discussion or move to synthesis."""
    should_continue = state.get("should_continue", False)
    current_round = state.get("current_round", 0)
    max_rounds = state.get("max_rounds", 3)

    if should_continue and current_round < max_rounds:
        return "agent_respond"
    return "final_synthesis"


# =============================================================================
# Graph construction
# =============================================================================


def create_discussion_workflow() -> StateGraph:
    """Create the discussion workflow graph.

    The workflow structure:
        fetch_discussion_data
            → 4 sequential initial statements
            → moderator_review
            → should_continue?
                YES → agent_respond → moderator_review (loop)
                NO  → final_synthesis → END

    Returns:
        Compiled StateGraph ready for execution
    """
    workflow = StateGraph(DiscussionState)

    # Add nodes
    workflow.add_node("fetch_discussion_data", fetch_discussion_data_node)
    workflow.add_node("fundamental_initial", fundamental_initial_node)
    workflow.add_node("technical_initial", technical_initial_node)
    workflow.add_node("sentiment_initial", sentiment_initial_node)
    workflow.add_node("news_initial", news_initial_node)
    workflow.add_node("moderator_review", moderator_review_node)
    workflow.add_node("agent_respond", agent_respond_node)
    workflow.add_node("final_synthesis", final_synthesis_node)

    # Start → fetch data
    workflow.add_edge("__start__", "fetch_discussion_data")

    # Fetch data → sequential initial statements → moderator review
    workflow.add_edge("fetch_discussion_data", "fundamental_initial")
    workflow.add_edge("fundamental_initial", "technical_initial")
    workflow.add_edge("technical_initial", "sentiment_initial")
    workflow.add_edge("sentiment_initial", "news_initial")
    workflow.add_edge("news_initial", "moderator_review")

    # Moderator review → conditional: continue or synthesize
    workflow.add_conditional_edges(
        "moderator_review",
        _should_continue_discussion,
        {
            "agent_respond": "agent_respond",
            "final_synthesis": "final_synthesis",
        },
    )

    # Agent respond → moderator review (loop back)
    workflow.add_edge("agent_respond", "moderator_review")

    # Final synthesis → END
    workflow.add_edge("final_synthesis", END)

    return workflow.compile()


# Module-level compiled workflow (singleton)
_compiled_workflow = None


def get_discussion_workflow() -> StateGraph:
    """Get the compiled discussion workflow (singleton)."""
    global _compiled_workflow
    if _compiled_workflow is None:
        _compiled_workflow = create_discussion_workflow()
    return _compiled_workflow


# =============================================================================
# Streaming entry point
# =============================================================================


async def stream_discussion(
    symbol: str,
    market: str,
    language: str = "zh",
    session_id: str = "",
    max_rounds: int = 3,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Run the discussion workflow with streaming output.

    Yields SSE-compatible events as the discussion progresses.
    Uses astream_events(version="v2") for true token-level streaming.

    Yields:
        Dict with "type" and "data" keys for SSE serialization
    """
    workflow = get_discussion_workflow()
    initial_state = create_discussion_state(
        symbol=symbol,
        market=market,
        language=language,
        session_id=session_id,
        max_rounds=max_rounds,
    )

    logger.info("讨论组: 开始流式讨论 %s (%s) max_rounds=%d", symbol, market, max_rounds)

    yield {
        "type": "discussion_start",
        "data": {"symbol": symbol, "market": market, "language": language, "max_rounds": max_rounds},
    }

    final_state = initial_state.copy()
    custom_event_agents: set[str] = set()

    try:
        async for event in workflow.astream_events(initial_state, version="v2"):
            event_type = event.get("event")
            event_name = event.get("name", "")

            # Capture final state
            if event_type == "on_chain_end" and event_name == "LangGraph":
                output = event.get("data", {}).get("output", {})
                if output:
                    final_state.update(output)

            # --- True token-level streaming from LLM calls ---
            # LangChain v1.2+ uses "on_chat_model_stream" instead of "on_llm_stream"
            if event_type in ("on_chat_model_stream", "on_llm_stream"):
                run_name = event.get("name", "")
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    # Identify which agent/phase is streaming
                    if "_initial_llm" in run_name:
                        agent = run_name.replace("_initial_llm", "")
                        yield {
                            "type": "agent_statement_chunk",
                            "data": {"agent_type": agent, "content": chunk.content},
                        }
                    elif "_debate_llm" in run_name:
                        agent = run_name.replace("_debate_llm", "")
                        yield {
                            "type": "agent_response_chunk",
                            "data": {"agent_type": agent, "content": chunk.content},
                        }
                    elif run_name == "moderator_review_llm":
                        yield {
                            "type": "moderator_chunk",
                            "data": {"content": chunk.content},
                        }
                    elif run_name == "synthesis_llm":
                        yield {
                            "type": "synthesis_chunk",
                            "data": {"content": chunk.content},
                        }

            # --- Node lifecycle events ---
            elif event_type == "on_chain_start":
                if event_name == "fetch_discussion_data":
                    yield {"type": "data_fetch_start", "data": {}}

                elif event_name in ("fundamental_initial", "technical_initial",
                                     "sentiment_initial", "news_initial"):
                    agent = event_name.replace("_initial", "")
                    yield {"type": "agent_statement_start", "data": {"agent_type": agent}}

                elif event_name == "moderator_review":
                    yield {"type": "moderator_review_start", "data": {}}

                elif event_name == "agent_respond":
                    yield {"type": "debate_round_start", "data": {}}

                elif event_name == "final_synthesis":
                    yield {"type": "synthesis_start", "data": {}}

            elif event_type == "on_chain_end":
                output = event.get("data", {}).get("output", {})

                if event_name == "fetch_discussion_data":
                    yield {"type": "data_fetch_complete", "data": {}}

                elif event_name in ("fundamental_initial", "technical_initial",
                                     "sentiment_initial", "news_initial"):
                    agent = event_name.replace("_initial", "")
                    new_msgs = output.get("messages", [])
                    content = new_msgs[0].get("content", "") if new_msgs else ""
                    yield {
                        "type": "agent_statement_complete",
                        "data": {
                            "agent_type": agent,
                            "content": strip_json_blocks(content),
                            "round": 0,
                            "latency_ms": new_msgs[0].get("latency_ms", 0) if new_msgs else 0,
                        },
                    }

                elif event_name == "moderator_review":
                    should_continue = output.get("should_continue", False)
                    target_agents = output.get("target_agents", [])
                    focus_topics = output.get("focus_topics", [])
                    new_msgs = output.get("messages", [])
                    content = new_msgs[0].get("content", "") if new_msgs else ""
                    yield {
                        "type": "moderator_guidance",
                        "data": {
                            "content": strip_json_blocks(content),
                            "should_continue": should_continue,
                            "target_agents": target_agents,
                            "focus_topics": focus_topics,
                        },
                    }

                elif event_name == "agent_respond":
                    # Fallback: emit agent_response_complete for any agents
                    # not already signaled via on_custom_event
                    new_msgs = output.get("messages", [])
                    for msg in new_msgs:
                        agent_type = msg.get("agent_type", "")
                        if agent_type and agent_type not in custom_event_agents:
                            logger.warning(
                                "讨论组: 通过on_chain_end回退发送 agent_response_complete: %s",
                                agent_type,
                            )
                            yield {
                                "type": "agent_response_complete",
                                "data": {
                                    "agent_type": agent_type,
                                    "content": strip_json_blocks(msg.get("content", "")),
                                    "round": msg.get("round", 0),
                                    "latency_ms": msg.get("latency_ms", 0),
                                },
                            }
                    custom_event_agents.clear()

                elif event_name == "final_synthesis":
                    report = output.get("synthesis_report", "")
                    compact = output.get("compact_context", "")
                    yield {
                        "type": "synthesis_complete",
                        "data": {"content": report, "compact_context": compact},
                    }

            # --- Custom events from nodes ---
            elif event_type == "on_custom_event":
                event_name = event.get("name", "")
                logger.debug("on_custom_event: name=%s", event_name)
                if event_name == "agent_debate_complete":
                    data = event.get("data", {})
                    custom_event_agents.add(data.get("agent_type", ""))
                    # Strip JSON from content for display
                    if data.get("content"):
                        data["content"] = strip_json_blocks(data["content"])
                    yield {"type": "agent_response_complete", "data": data}
                elif event_name == "agent_debate_start":
                    data = event.get("data", {})
                    yield {"type": "agent_response_start", "data": data}

        # Final completion event
        yield {
            "type": "discussion_complete",
            "data": {
                "symbol": symbol,
                "synthesis_report": final_state.get("synthesis_report", ""),
                "compact_context": final_state.get("compact_context", ""),
                "total_rounds": final_state.get("current_round", 0),
                "total_tokens": final_state.get("total_tokens", 0),
                "errors": final_state.get("errors", []),
            },
        }

    except Exception as e:
        logger.exception("讨论组: 流式讨论错误 %s: %s", symbol, e)
        yield {
            "type": "error",
            "data": {"message": f"Discussion error: {str(e)[:200]}"},
        }
