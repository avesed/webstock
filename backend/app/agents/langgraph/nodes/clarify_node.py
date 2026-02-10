"""Clarification node for LangGraph workflow.

This module handles the clarification round where the synthesis layer
requests additional information from specific analysis agents.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List

from app.agents.langgraph.state import AnalysisState
from app.agents.langgraph.utils.json_extractor import safe_json_extract
from app.core.llm import get_analysis_langchain_model
from app.prompts.loader import load_instructions
from app.schemas.agent_analysis import (
    AgentAnalysisResult,
    ClarificationRequest,
)
from app.services.token_service import count_tokens

logger = logging.getLogger(__name__)

# Timeout for clarification LLM calls
CLARIFY_TIMEOUT = 45


async def _handle_single_clarification(
    request: ClarificationRequest,
    state: AnalysisState,
    language: str,
) -> AgentAnalysisResult:
    """
    Handle a single clarification request.

    Args:
        request: The clarification request to handle
        state: Current workflow state
        language: Output language

    Returns:
        AgentAnalysisResult with the clarification response
    """
    start_time = time.time()
    symbol = state["symbol"]
    market = state["market"]

    # Determine target agent from context
    context = request.context or {}
    target_agents = context.get("target_agents", [])
    target_agent = target_agents[0] if target_agents else context.get("agent_type", "general")

    # Get original result for context
    original_result = state.get(target_agent)
    original_content = ""
    if original_result and original_result.raw_content:
        original_length = len(original_result.raw_content)
        max_content_length = 1500
        original_content = original_result.raw_content[:max_content_length]
        if original_length > max_content_length:
            logger.debug(
                f"Truncated original content for {target_agent} clarification: "
                f"{original_length} -> {max_content_length} characters"
            )

    logger.info(f"Processing clarification for {target_agent} on {symbol}")

    # Load clarification instructions
    try:
        instruction_file = "clarify_instructions.md" if language == "en" else "clarify_instructions_zh.md"
        instructions = load_instructions(instruction_file, subdirectory="templates/clarification")
    except FileNotFoundError:
        if language == "zh":
            instructions = """你是一位专业分析师，正在回应追问请求。
请基于你之前的分析，提供更详细的解释或补充信息。
保持客观和专业。"""
        else:
            instructions = """You are a professional analyst responding to a clarification request.
Please provide more detailed explanation or additional information based on your previous analysis.
Remain objective and professional."""

    # Build clarification prompt
    question = request.question_zh if language == "zh" and request.question_zh else request.question

    if language == "zh":
        user_prompt = f"""# 追问请求

**股票代码**: {symbol}
**市场**: {market}
**分析类型**: {target_agent}

## 原始分析摘要
{original_content}

## 追问内容
{question}

请针对以上追问，提供补充分析或解释。
"""
    else:
        user_prompt = f"""# Clarification Request

**Stock Symbol**: {symbol}
**Market**: {market}
**Analysis Type**: {target_agent}

## Original Analysis Summary
{original_content}

## Clarification Question
{question}

Please provide additional analysis or explanation addressing the above question.
"""

    # Call LLM
    try:
        llm = await get_analysis_langchain_model()
        messages = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_prompt},
        ]

        response = await asyncio.wait_for(
            llm.ainvoke(messages),
            timeout=CLARIFY_TIMEOUT,
        )
        content = response.content

        latency_ms = int((time.time() - start_time) * 1000)
        tokens_used = count_tokens(instructions + user_prompt + content)

        logger.info(f"Clarification for {target_agent} completed in {latency_ms}ms")

        return AgentAnalysisResult(
            agent_type=f"{target_agent}_clarification",
            symbol=symbol,
            market=market,
            success=True,
            raw_content=content,
            raw_data=safe_json_extract(content, {}),
            latency_ms=latency_ms,
            tokens_used=tokens_used,
        )

    except asyncio.TimeoutError:
        logger.error(f"Clarification timeout for {target_agent}")
        return AgentAnalysisResult(
            agent_type=f"{target_agent}_clarification",
            symbol=symbol,
            market=market,
            success=False,
            error="Clarification timeout",
            latency_ms=int((time.time() - start_time) * 1000),
        )
    except Exception as e:
        logger.error(f"Clarification failed for {target_agent}: {e}")
        return AgentAnalysisResult(
            agent_type=f"{target_agent}_clarification",
            symbol=symbol,
            market=market,
            success=False,
            error=str(e),
            latency_ms=int((time.time() - start_time) * 1000),
        )


async def clarify_node(state: AnalysisState) -> Dict[str, Any]:
    """
    Process clarification requests from the synthesis layer.

    This node handles all pending clarification requests, calling
    the appropriate analysis agents for additional information.

    Args:
        state: Current workflow state with clarification_requests

    Returns:
        Dict with clarification_responses and cleared clarification_requests
    """
    clarification_requests = state.get("clarification_requests", [])
    language = state.get("language", "en")

    if not clarification_requests:
        logger.info("No clarification requests to process")
        return {
            "clarification_responses": [],
            "clarification_requests": [],
        }

    logger.info(f"Processing {len(clarification_requests)} clarification requests")

    # Process all clarification requests in parallel
    tasks = [
        _handle_single_clarification(request, state, language)
        for request in clarification_requests
    ]

    responses = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter successful responses
    valid_responses = []
    errors = []

    for i, response in enumerate(responses):
        if isinstance(response, Exception):
            error_msg = f"Clarification {i} failed: {response}"
            logger.error(error_msg)
            errors.append(error_msg)
        elif isinstance(response, AgentAnalysisResult):
            if response.success:
                valid_responses.append(response)
            else:
                errors.append(f"Clarification {i} failed: {response.error}")

    logger.info(
        f"Clarification complete: {len(valid_responses)} successful, {len(errors)} failed"
    )

    # Add stream chunk for progress indication
    stream_chunks = [
        f"[clarify] Processed {len(valid_responses)} clarification requests"
    ]

    return {
        "clarification_responses": valid_responses,
        "clarification_requests": [],  # Clear the requests
        "stream_chunks": stream_chunks,
        "errors": errors if errors else [],
    }


def should_clarify(state: AnalysisState) -> str:
    """
    Determine if clarification is needed.

    This is used as a conditional edge function in the workflow graph.

    Args:
        state: Current workflow state

    Returns:
        "clarify" if clarification is needed, "end" otherwise
    """
    clarification_round = state.get("clarification_round", 0)
    clarification_requests = state.get("clarification_requests", [])

    # Check if we've exceeded max rounds
    if clarification_round >= 2:
        logger.info("Max clarification rounds reached, proceeding to end")
        return "end"

    # Check if there are pending requests
    if clarification_requests:
        logger.info(f"Found {len(clarification_requests)} clarification requests")
        return "clarify"

    logger.info("No clarification needed, proceeding to end")
    return "end"
