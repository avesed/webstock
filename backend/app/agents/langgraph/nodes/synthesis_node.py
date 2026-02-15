"""Synthesis node for LangGraph workflow.

This module contains the synthesis layer that combines results from all
analysis agents into a coherent, user-facing report.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from app.agents.langgraph.state import AnalysisState, get_successful_results
from app.agents.langgraph.utils.conflict_detection import (
    detect_conflicts,
    detect_low_confidence_results,
    detect_missing_critical_data,
)
from app.agents.langgraph.utils.json_extractor import safe_json_extract
from app.core.llm import get_synthesis_langchain_model
from app.core.llm.usage_callback import LlmUsageCallbackHandler
from app.prompts.loader import load_instructions
from app.schemas.agent_analysis import (
    ActionRecommendation,
    AgentAnalysisResult,
    AnalysisConfidence,
    ClarificationRequest,
    ClarificationType,
    InvestmentRecommendation,
    KeyInsight,
    SynthesisResult,
)
from app.services.token_service import count_tokens

logger = logging.getLogger(__name__)

# Maximum clarification rounds
MAX_CLARIFICATION_ROUNDS = 2

# Confidence threshold for triggering clarification
CLARIFICATION_CONFIDENCE_THRESHOLD = 0.6

# Timeout for synthesis LLM call
SYNTHESIS_TIMEOUT = 90

# Token budget warning threshold (80% of typical context window)
TOKEN_WARNING_THRESHOLD = 100000


async def collect_node(state: AnalysisState) -> Dict[str, Any]:
    """
    Collect results from all analysis nodes.

    This is a synchronization point that waits for all parallel analysis
    nodes to complete. In LangGraph with parallel edges, this node will
    only execute after all upstream nodes have finished.

    Args:
        state: Current workflow state with analysis results

    Returns:
        Empty dict (results are already in state from parallel nodes)
    """
    completed = []
    failed = []

    for agent_type in ["fundamental", "technical", "sentiment", "news"]:
        result = state.get(agent_type)
        if result is not None:
            if result.success:
                completed.append(agent_type)
            else:
                failed.append(agent_type)
        else:
            failed.append(agent_type)

    logger.info(
        f"Collected analysis results: {len(completed)} completed, {len(failed)} failed"
    )

    if failed:
        logger.warning(f"Failed agents: {failed}")

    # Add stream chunk for progress indication
    return {
        "stream_chunks": [f"[collect] Analysis complete: {len(completed)}/4 agents succeeded"],
    }


def _build_synthesis_prompt(
    results: List[AgentAnalysisResult],
    clarification_responses: List[AgentAnalysisResult],
    language: str,
) -> str:
    """
    Build the synthesis prompt from analysis results.

    Args:
        results: List of successful analysis results
        clarification_responses: Additional responses from clarification round
        language: Output language ("en" or "zh")

    Returns:
        Formatted prompt string for synthesis
    """
    sections = []

    for result in results:
        agent_type = result.agent_type
        content = result.raw_content or ""

        # Also include structured data summary if available
        structured_summary = ""
        if result.fundamental:
            valuation = getattr(result.fundamental.valuation, 'value', None) if result.fundamental.valuation else None
            action = getattr(result.fundamental.action, 'value', None) if result.fundamental.action else None
            if valuation and action:
                structured_summary = f"\nValuation: {valuation}, Action: {action}"
        elif result.technical:
            trend = getattr(result.technical.trend, 'value', None) if result.technical.trend else None
            action = getattr(result.technical.action, 'value', None) if result.technical.action else None
            if trend and action:
                structured_summary = f"\nTrend: {trend}, Action: {action}"
        elif result.sentiment:
            sentiment = getattr(result.sentiment.overall_sentiment, 'value', None) if result.sentiment.overall_sentiment else None
            if sentiment:
                structured_summary = f"\nSentiment: {sentiment}"
        elif result.news:
            news_sentiment = getattr(result.news.overall_sentiment, 'value', None) if result.news.overall_sentiment else None
            action_str = f", Action: {result.news.action.value}" if result.news.action else ""
            if news_sentiment:
                structured_summary = f"\nNews Sentiment: {news_sentiment}{action_str}"

        if language == "zh":
            section_title = {
                "fundamental": "基本面分析",
                "technical": "技术面分析",
                "sentiment": "情绪分析",
                "news": "新闻分析",
            }.get(agent_type, agent_type)
        else:
            section_title = {
                "fundamental": "Fundamental Analysis",
                "technical": "Technical Analysis",
                "sentiment": "Sentiment Analysis",
                "news": "News Analysis",
            }.get(agent_type, agent_type.capitalize())

        sections.append(f"""## {section_title}
{structured_summary}

{content[:2000]}
""")

    # Include clarification responses if any
    if clarification_responses:
        if language == "zh":
            sections.append("\n## 追问响应")
        else:
            sections.append("\n## Clarification Responses")

        for response in clarification_responses:
            sections.append(f"### {response.agent_type}\n{response.raw_content or ''}\n")

    return "\n".join(sections)


def _generate_clarification_requests(
    conflicts: List[Dict[str, Any]],
    low_confidence: List[AgentAnalysisResult],
    missing_data: List[str],
    language: str,
) -> List[ClarificationRequest]:
    """
    Generate clarification requests based on detected issues.

    Args:
        conflicts: List of detected conflicts between agents
        low_confidence: List of results with low confidence
        missing_data: List of missing critical data points
        language: Output language

    Returns:
        List of ClarificationRequest objects
    """
    requests = []

    # Handle conflicts
    for conflict in conflicts[:2]:  # Limit to 2 conflict clarifications
        agent1 = conflict.get("agent1", "")
        agent2 = conflict.get("agent2", "")
        description = conflict.get("description", "")

        if language == "zh":
            question = f"关于{agent1}和{agent2}的分析存在分歧：{description}。请补充说明您的判断依据。"
        else:
            question = f"There is a disagreement between {agent1} and {agent2}: {description}. Please provide additional justification for your analysis."

        requests.append(ClarificationRequest(
            clarification_type=ClarificationType.ANALYSIS_SCOPE,
            question=question,
            question_zh=question if language == "zh" else None,
            context={
                "conflict": conflict,
                "target_agents": [agent1, agent2],
            },
        ))

    # Handle low confidence results
    for result in low_confidence[:2]:  # Limit to 2 low confidence clarifications
        agent_type = result.agent_type

        if language == "zh":
            question = f"{agent_type}分析的置信度较低，请说明数据限制对分析结论的影响。"
        else:
            question = f"The {agent_type} analysis has low confidence. Please explain how data limitations affect your conclusions."

        requests.append(ClarificationRequest(
            clarification_type=ClarificationType.MISSING_DATA,
            question=question,
            question_zh=question if language == "zh" else None,
            context={
                "agent_type": agent_type,
                "reason": "low_confidence",
            },
        ))

    return requests


async def synthesize_node(state: AnalysisState) -> Dict[str, Any]:
    """
    Synthesize all analysis results into a user-facing report.

    This node:
    1. Collects all successful analysis results
    2. Detects conflicts and low confidence results
    3. Generates clarification requests if needed (first round only)
    4. Produces a synthesized analysis report

    Args:
        state: Current workflow state

    Returns:
        Dict with synthesis_output, clarification_requests, and clarification_round
    """
    start_time = time.time()
    symbol = state["symbol"]
    market = state["market"]
    language = state.get("language", "en")
    current_round = state.get("clarification_round", 0)

    logger.info(f"Synthesis started for {symbol} (round {current_round})")

    # 1. Collect successful results
    results = get_successful_results(state)

    if not results:
        error_msg = (
            "所有分析代理都失败了，无法生成综合报告。" if language == "zh"
            else "All analysis agents failed. Unable to generate synthesis report."
        )
        return {
            "synthesis_output": error_msg,
            "clarification_requests": [],
            "clarification_round": current_round + 1,
            "errors": ["All analysis agents failed"],
        }

    # 2. Detect issues (only in first round)
    clarification_requests = []
    needs_clarification = False
    if current_round == 0:
        conflicts = detect_conflicts(results)
        low_confidence = detect_low_confidence_results(
            results,
            threshold=CLARIFICATION_CONFIDENCE_THRESHOLD,
        )
        missing_data = detect_missing_critical_data(results)

        if conflicts or low_confidence:
            clarification_requests = _generate_clarification_requests(
                conflicts,
                low_confidence,
                missing_data,
                language,
            )
            needs_clarification = len(clarification_requests) > 0
            logger.info(
                f"Generated {len(clarification_requests)} clarification requests"
            )

    # If clarification is needed in round 0, skip synthesis and wait for clarification
    # This prevents showing incomplete synthesis to user before clarification
    if needs_clarification and current_round == 0:
        logger.info(f"Deferring synthesis for {symbol} until after clarification")
        placeholder_msg = (
            "正在进行深度分析，请稍候..." if language == "zh"
            else "Performing deeper analysis, please wait..."
        )
        return {
            "synthesis_output": "",  # Empty - will be filled after clarification
            "clarification_requests": clarification_requests,
            "clarification_round": current_round + 1,
            "stream_chunks": [placeholder_msg],
        }

    # 3. Load synthesis instructions
    try:
        instruction_file = "synthesis_instructions.md" if language == "en" else "synthesis_instructions_zh.md"
        instructions = load_instructions(instruction_file, subdirectory="templates/synthesis")
    except FileNotFoundError:
        if language == "zh":
            instructions = """你是一位资深投资顾问。
综合分析各方面的研究结果，为投资者提供全面、平衡的投资建议。
请用清晰的结构和专业的语言撰写报告。"""
        else:
            instructions = """You are a senior investment advisor.
Synthesize the research from multiple perspectives to provide comprehensive, balanced investment recommendations.
Write your report with clear structure and professional language."""

    # 4. Build prompt
    analysis_content = _build_synthesis_prompt(
        results,
        state.get("clarification_responses", []),
        language,
    )

    if language == "zh":
        user_prompt = f"""# 综合分析请求

**股票代码**: {symbol}
**市场**: {market}

以下是各分析代理的结果：

{analysis_content}

请综合以上分析，撰写一份全面的投资分析报告。
"""
    else:
        user_prompt = f"""# Synthesis Request

**Stock Symbol**: {symbol}
**Market**: {market}

Below are the results from each analysis agent:

{analysis_content}

Please synthesize the above analyses and write a comprehensive investment analysis report.
"""

    # Log input token count and check against budget
    input_tokens = count_tokens(instructions + user_prompt)
    logger.debug(f"Synthesis input token count: {input_tokens}")

    if input_tokens > TOKEN_WARNING_THRESHOLD:
        logger.warning(
            f"Synthesis input tokens ({input_tokens}) approaching context limit. "
            f"Threshold: {TOKEN_WARNING_THRESHOLD}. Consider reducing input size."
        )

    # 5. Call synthesis LLM
    try:
        llm = await get_synthesis_langchain_model()
        messages = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_prompt},
        ]
        usage_cb = LlmUsageCallbackHandler(
            purpose="synthesis", metadata={"symbol": symbol},
        )
        response = await asyncio.wait_for(
            llm.ainvoke(messages, config={"callbacks": [usage_cb]}),
            timeout=SYNTHESIS_TIMEOUT,
        )
        synthesis_output = response.content

    except asyncio.TimeoutError:
        logger.error(f"Synthesis timeout for {symbol}")
        synthesis_output = (
            "综合分析超时，请稍后重试。" if language == "zh"
            else "Synthesis timed out. Please try again later."
        )
        return {
            "synthesis_output": synthesis_output,
            "clarification_requests": [],
            "clarification_round": current_round + 1,
            "errors": ["Synthesis timeout"],
        }
    except Exception as e:
        logger.error(f"Synthesis LLM call failed for {symbol}: {e}")
        synthesis_output = (
            f"综合分析失败：{e}" if language == "zh"
            else f"Synthesis failed: {e}"
        )
        return {
            "synthesis_output": synthesis_output,
            "clarification_requests": [],
            "clarification_round": current_round + 1,
            "errors": [f"Synthesis error: {e}"],
        }

    latency_ms = int((time.time() - start_time) * 1000)
    tokens_used = count_tokens(instructions + user_prompt + synthesis_output)

    logger.info(f"Synthesis completed for {symbol} in {latency_ms}ms, {tokens_used} tokens")

    # Add stream chunks for the synthesis output
    stream_chunks = []
    # Split synthesis into chunks for streaming (simulate streaming behavior)
    chunk_size = 200
    for i in range(0, len(synthesis_output), chunk_size):
        chunk = synthesis_output[i:i+chunk_size]
        stream_chunks.append(chunk)

    return {
        "synthesis_output": synthesis_output,
        "clarification_requests": clarification_requests,
        "clarification_round": current_round + 1,
        "stream_chunks": stream_chunks,
    }


async def generate_synthesis_result(state: AnalysisState) -> SynthesisResult:
    """
    Generate a structured SynthesisResult from the final state.

    This is called after the workflow completes to create the final
    structured output.

    Args:
        state: Final workflow state

    Returns:
        SynthesisResult with all aggregated information
    """
    results = get_successful_results(state)

    # Calculate totals
    total_tokens = sum(r.tokens_used for r in results)
    total_latency = sum(r.latency_ms for r in results)
    agents_used = [r.agent_type for r in results]

    # Extract summaries from each agent
    fundamental_summary = None
    technical_summary = None
    sentiment_summary = None
    news_summary = None

    for result in results:
        if result.fundamental:
            fundamental_summary = result.fundamental.summary
        elif result.technical:
            technical_summary = result.technical.summary
        elif result.sentiment:
            sentiment_summary = result.sentiment.summary
        elif result.news:
            news_summary = result.news.summary

    # Aggregate key insights
    all_insights = []
    for result in results:
        if result.fundamental:
            all_insights.extend(result.fundamental.key_insights)
        elif result.technical:
            all_insights.extend(result.technical.key_insights)
        elif result.sentiment:
            all_insights.extend(result.sentiment.key_insights)
        elif result.news:
            all_insights.extend(result.news.key_insights)

    # Aggregate strengths, weaknesses, risks
    strengths = []
    weaknesses = []
    risks = []

    for result in results:
        if result.fundamental:
            strengths.extend(result.fundamental.strengths)
            weaknesses.extend(result.fundamental.weaknesses)
            risks.extend(result.fundamental.risks)

    # Determine overall recommendation (simplified logic)
    action = ActionRecommendation.HOLD
    confidence = AnalysisConfidence.MEDIUM

    buy_signals = 0
    sell_signals = 0

    for result in results:
        if result.fundamental:
            if result.fundamental.action in (ActionRecommendation.BUY, ActionRecommendation.STRONG_BUY):
                buy_signals += 1
            elif result.fundamental.action in (ActionRecommendation.SELL, ActionRecommendation.STRONG_SELL):
                sell_signals += 1
        elif result.technical:
            if result.technical.action in (ActionRecommendation.BUY, ActionRecommendation.STRONG_BUY):
                buy_signals += 1
            elif result.technical.action in (ActionRecommendation.SELL, ActionRecommendation.STRONG_SELL):
                sell_signals += 1
        elif result.news:
            if result.news.action is not None:
                if result.news.action in (ActionRecommendation.BUY, ActionRecommendation.STRONG_BUY):
                    buy_signals += 1
                elif result.news.action in (ActionRecommendation.SELL, ActionRecommendation.STRONG_SELL):
                    sell_signals += 1

    if buy_signals >= 2:
        action = ActionRecommendation.BUY
    elif sell_signals >= 2:
        action = ActionRecommendation.SELL

    if len(results) >= 3 and (buy_signals >= 3 or sell_signals >= 3):
        confidence = AnalysisConfidence.HIGH
    elif len(results) < 2:
        confidence = AnalysisConfidence.LOW

    return SynthesisResult(
        symbol=state["symbol"],
        market=state["market"],
        recommendation=InvestmentRecommendation(
            action=action,
            confidence=confidence,
            time_horizon="medium_term",
            risk_level="moderate",
        ),
        key_insights=all_insights[:10],  # Top 10 insights
        strengths=strengths[:5],
        weaknesses=weaknesses[:5],
        risks=risks[:5],
        catalysts=[],  # Would need more analysis
        fundamental_summary=fundamental_summary,
        technical_summary=technical_summary,
        sentiment_summary=sentiment_summary,
        news_summary=news_summary,
        executive_summary=state.get("synthesis_output", ""),
        agents_used=agents_used,
        total_tokens=total_tokens,
        total_latency_ms=total_latency,
    )
