"""Discussion group nodes for LangGraph workflow.

This module contains the node functions for the multi-agent discussion:
1. fetch_discussion_data_node: Pre-fetch shared data + build summary
2. initial_statement nodes (4): Parallel initial analyses
3. moderator_review_node: Chief Strategist reviews and directs
4. agent_respond_node: Targeted agents respond to moderator
5. final_synthesis_node: Generate final synthesis report + compact context
"""

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from langchain_core.callbacks import adispatch_custom_event
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import merge_configs

from app.agents.langgraph.discussion_state import DiscussionMessage, DiscussionState
from app.agents.langgraph.utils.json_extractor import extract_json_from_response
from app.core.llm.usage_callback import LlmUsageCallbackHandler
from app.prompts.loader import load_template

logger = logging.getLogger(__name__)

# Timeout for LLM calls
LLM_TIMEOUT = 90

# Agent type metadata
AGENT_TYPES = ["fundamental", "technical", "sentiment", "news"]

AGENT_ROLES = {
    "en": {
        "fundamental": "Fundamental Agent",
        "technical": "Technical Agent",
        "sentiment": "Sentiment Agent",
        "news": "News Agent",
        "moderator": "Coordinator Agent",
    },
    "zh": {
        "fundamental": "基本面专家",
        "technical": "技术面专家",
        "sentiment": "情绪面专家",
        "news": "新闻面专家",
        "moderator": "综合专家",
    },
}

AGENT_EXPERTISE = {
    "en": {
        "fundamental": (
            "Financial statement analysis, valuation (DCF, comparables, PEG), "
            "profitability metrics (ROE, ROA, margins), balance sheet health, "
            "and growth sustainability."
        ),
        "technical": (
            "Price action, chart patterns, trend analysis, momentum indicators "
            "(RSI, MACD, Bollinger Bands), support/resistance levels, and volume analysis."
        ),
        "sentiment": (
            "Market psychology, investor sentiment surveys, social media analysis, "
            "insider activity, institutional positioning, and analyst consensus."
        ),
        "news": (
            "Breaking news impact, earnings announcements, regulatory changes, "
            "M&A activity, macro catalysts, and event-driven analysis."
        ),
    },
    "zh": {
        "fundamental": (
            "财务报表分析、估值方法（DCF、可比公司、PEG比率）、"
            "盈利能力指标（ROE、ROA、利润率）、资产负债表健康度、增长可持续性。"
        ),
        "technical": (
            "价格走势分析、图表形态、趋势分析、动量指标"
            "（RSI、MACD、布林带）、支撑/阻力位、成交量分析。"
        ),
        "sentiment": (
            "市场心理分析、投资者情绪调查、社交媒体分析、"
            "内部人士动向、机构持仓变化、分析师共识。"
        ),
        "news": (
            "重要新闻影响、财报发布、监管变化、"
            "并购活动、宏观催化剂、事件驱动分析。"
        ),
    },
}

# =============================================================================
# Helper functions
# =============================================================================


def _build_shared_data_summary(
    shared_data: Dict[str, Any],
    symbol: str,
) -> str:
    """Build a compact text summary of key data points from shared_data."""
    from app.skills.base import SkillResult

    lines = [f"=== Data Summary for {symbol} ===\n"]

    for cache_key, result in shared_data.items():
        if not isinstance(result, SkillResult) or not result.success:
            continue
        data = result.data
        if data is None:
            continue

        skill_name = cache_key.split("|")[0]

        if skill_name == "get_stock_quote" and isinstance(data, dict):
            lines.append(f"[Quote] Price: {data.get('price', 'N/A')}, "
                         f"Change: {data.get('change_percent', 'N/A')}%, "
                         f"Volume: {data.get('volume', 'N/A')}, "
                         f"Market Cap: {data.get('market_cap', 'N/A')}")

        elif skill_name == "get_stock_info" and isinstance(data, dict):
            lines.append(f"[Info] Sector: {data.get('sector', 'N/A')}, "
                         f"Industry: {data.get('industry', 'N/A')}, "
                         f"Employees: {data.get('full_time_employees', 'N/A')}")

        elif skill_name == "get_stock_financials" and isinstance(data, dict):
            metrics = data
            lines.append(f"[Financials] PE: {metrics.get('pe_ratio', 'N/A')}, "
                         f"PB: {metrics.get('price_to_book', 'N/A')}, "
                         f"ROE: {metrics.get('roe', 'N/A')}, "
                         f"D/E: {metrics.get('debt_to_equity', 'N/A')}, "
                         f"Margin: {metrics.get('profit_margin', 'N/A')}")

        elif skill_name == "get_stock_history" and isinstance(data, dict):
            bars = data.get("bars", [])
            if bars:
                latest = bars[-1]
                earliest = bars[0]
                lines.append(f"[History] {len(bars)} trading days ({earliest.get('date', '?')} to {latest.get('date', '?')}), "
                             f"Latest: O={latest.get('open', 'N/A')} H={latest.get('high', 'N/A')} "
                             f"L={latest.get('low', 'N/A')} C={latest.get('close', 'N/A')} V={latest.get('volume', 'N/A')}")

        elif skill_name == "get_analyst_ratings" and isinstance(data, dict):
            lines.append(f"[Analysts] Consensus: {data.get('recommendation', 'N/A')}, "
                         f"Target: {data.get('target_price', 'N/A')}")

        elif skill_name == "get_news" and isinstance(data, list):
            lines.append(f"[News] {len(data)} recent articles")
            for item in data[:5]:
                if isinstance(item, dict):
                    title = item.get("title", "N/A")[:80]
                    date = item.get("date", item.get("published", ""))[:10]
                    sentiment = item.get("sentiment", item.get("sentiment_score", ""))
                    parts = [f"  - {title}"]
                    if date:
                        parts.append(f"({date})")
                    if sentiment:
                        parts.append(f"[{sentiment}]")
                    lines.append(" ".join(parts))

        elif skill_name == "get_institutional_holders" and isinstance(data, dict):
            holders = data.get("holders", [])
            total_pct = data.get("total_institutional_pct", "N/A")
            lines.append(f"[Institutional Holders] {len(holders)} holders, total: {total_pct}%")
            for h in holders[:5]:
                lines.append(f"  - {h.get('holder', 'N/A')}: {h.get('shares', 'N/A')} shares ({h.get('pct_held', 'N/A')}%)")

        elif skill_name == "get_fund_holdings_cn" and isinstance(data, dict):
            holdings = data.get("holdings")
            if isinstance(holdings, dict):
                lines.append(f"[Fund Holdings (CN)] "
                             f"Institutions: {holdings.get('institution_count', 'N/A')}, "
                             f"Holding: {holdings.get('holding_pct', 'N/A')}%, "
                             f"Float: {holdings.get('float_pct', 'N/A')}%")
            elif isinstance(holdings, list):
                lines.append(f"[Fund Holdings (CN)] {len(holdings)} funds")
                for f in holdings[:5]:
                    lines.append(f"  - {f.get('fund_name', f.get('name', 'N/A'))}: {f.get('holding_pct', f.get('pct', 'N/A'))}%")

        elif skill_name == "get_northbound_holding" and isinstance(data, dict):
            latest = data.get("latest_holding")
            if isinstance(latest, dict):
                lines.append(f"[Northbound] Holding: {latest.get('holding_pct', 'N/A')}%, "
                             f"Shares: {latest.get('holding_shares', 'N/A')}, "
                             f"Change: {latest.get('change_shares', 'N/A')} shares")
            else:
                lines.append(f"[Northbound] {data.get('data_cutoff_notice', 'No data')}")

        elif skill_name == "get_sector_industry" and isinstance(data, dict):
            lines.append(f"[Sector/Industry] Sector: {data.get('sector', 'N/A')}, "
                         f"Industry: {data.get('industry', 'N/A')}")

        elif skill_name == "get_market_context" and isinstance(data, dict):
            ctx_parts = ["[Market Context]"]
            for idx_name in ("sp500", "hang_seng", "shanghai_composite", "shenzhen_component"):
                idx = data.get(idx_name)
                if isinstance(idx, dict):
                    ctx_parts.append(f"{idx.get('name', idx_name)}: {idx.get('change_pct', 'N/A')}%")
            nb = data.get("northbound_summary")
            if isinstance(nb, dict) and nb.get("last_5d_net_buy") is not None:
                ctx_parts.append(f"NB 5d net: {nb['last_5d_net_buy']}")
            lines.append(" | ".join(ctx_parts))

        elif skill_name == "qlib_compute_factors" and isinstance(data, dict):
            factors = data.get("top_factors", [])
            lines.append(f"[Qlib Factors] {len(factors)} factors (mode: {data.get('mode', 'N/A')})")
            for f in factors[:5]:
                lines.append(f"  - {f.get('name', 'N/A')}: {f.get('value', 'N/A')} (z={f.get('z_score', 'N/A')})")

        elif skill_name == "__computed_indicators" and isinstance(data, dict):
            lines.append(f"[Technical Indicators] SMA20: {data.get('sma_20', 'N/A')}, "
                         f"SMA50: {data.get('sma_50', 'N/A')}, SMA200: {data.get('sma_200', 'N/A')}, "
                         f"RSI14: {data.get('rsi_14', 'N/A')}, "
                         f"MACD: {data.get('macd', 'N/A')}/{data.get('macd_signal', 'N/A')}, "
                         f"Volume Ratio: {data.get('volume_ratio', 'N/A')}")

        elif skill_name == "__computed_history_summary" and isinstance(data, dict):
            lines.append(f"[Price Summary] 52W High: {data.get('high_52w', 'N/A')}, "
                         f"52W Low: {data.get('low_52w', 'N/A')}, "
                         f"1W: {data.get('change_1w', 'N/A')}%, "
                         f"1M: {data.get('change_1m', 'N/A')}%, "
                         f"3M: {data.get('change_3m', 'N/A')}%")

    return "\n".join(lines) if len(lines) > 1 else f"No pre-fetched data available for {symbol}."


def _format_discussion_thread(
    messages: List[DiscussionMessage],
    language: str,
) -> str:
    """Format the discussion thread for inclusion in prompts."""
    if not messages:
        return "(No messages yet — this is the start of the discussion)"

    roles = AGENT_ROLES.get(language, AGENT_ROLES["en"])
    lines = []
    current_round = -999

    for msg in messages:
        r = msg.get("round", 0)
        if r != current_round:
            current_round = r
            if r == 0:
                label = "Initial Statements" if language == "en" else "初始发言"
            elif r == -1:
                label = "Synthesis" if language == "en" else "综合总结"
            else:
                label = f"Round {r}" if language == "en" else f"第{r}轮"
            lines.append(f"\n--- {label} ---\n")

        agent = msg.get("agent_type", "unknown")
        name = roles.get(agent, agent)
        content = msg.get("content", "")
        lines.append(f"**{name}**:\n{content}\n")

    return "\n".join(lines)


def _extract_json_control(text: str) -> Optional[Dict[str, Any]]:
    """Extract the JSON control block from moderator output.

    Uses the shared json_extractor utility which handles nested braces,
    code blocks, thinking tags, and other common LLM output patterns.
    Validates that the extracted JSON contains an "action" field.
    """
    try:
        result = extract_json_from_response(text)
        if isinstance(result, dict) and "action" in result:
            return result
        logger.debug("_extract_json_control: 提取的JSON缺少 'action' 字段: %s", list(result.keys()) if isinstance(result, dict) else type(result))
        return None
    except (ValueError, json.JSONDecodeError):
        return None


async def _get_discussion_llm(db_session=None):
    """Get the LangChain model for discussion."""
    from app.core.llm import get_discussion_langchain_model
    return await get_discussion_langchain_model(db_session=db_session)


# =============================================================================
# Node 1: Fetch shared data
# =============================================================================


async def fetch_discussion_data_node(state: DiscussionState) -> Dict[str, Any]:
    """Pre-fetch all shared data for discussion agents and build a summary.

    Reuses the same skill infrastructure as analysis_nodes.py.
    """
    from app.agents.langgraph.nodes.analysis_nodes import (
        _compute_shared_skill_plan,
        _build_skill_kwargs,
        _make_cache_key,
    )
    from app.skills.registry import get_skill_registry
    from app.skills.base import SkillResult

    symbol = state["symbol"]
    market = state["market"]

    logger.info("讨论组: 获取共享数据 %s (%s)", symbol, market)
    start_time = time.time()

    registry = get_skill_registry()
    plan = _compute_shared_skill_plan(symbol, market)

    async def _run_skill(item: Dict[str, Any]):
        skill = registry.get(item["name"])
        if skill is None:
            return item["cache_key"], SkillResult(success=False, error=f"Skill {item['name']} not found")
        try:
            result = await skill.safe_execute(timeout=15.0, **item["kwargs"])
            return item["cache_key"], result
        except Exception as e:
            logger.warning("讨论组: 数据获取失败 %s: %s", item["name"], e)
            return item["cache_key"], SkillResult(success=False, error=str(e))

    tasks = [_run_skill(item) for item in plan]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    shared_data: Dict[str, Any] = {}
    succeeded = 0
    for result in results:
        if isinstance(result, Exception):
            logger.warning("讨论组: 技能任务异常: %s", result)
            continue
        cache_key, skill_result = result
        shared_data[cache_key] = skill_result
        if skill_result.success:
            succeeded += 1

    # Compute indicators + summary from history bars (like analysis_nodes does)
    history_key = next((k for k in shared_data if k.startswith("get_stock_history|")), None)
    if history_key:
        hist_result = shared_data[history_key]
        if hist_result.success and hist_result.data:
            bars = hist_result.data.get("bars", [])
            if bars:
                from app.skills.computation.technical_indicators import _calculate_technical_indicators
                from app.skills.computation.history_summary import _calculate_history_summary
                try:
                    indicators = _calculate_technical_indicators(bars)
                    if indicators:
                        shared_data["__computed_indicators"] = SkillResult(success=True, data=indicators)
                except Exception as e:
                    logger.warning("讨论组: 技术指标计算失败: %s", e)
                try:
                    summary_stats = _calculate_history_summary(bars)
                    if summary_stats:
                        shared_data["__computed_history_summary"] = SkillResult(success=True, data=summary_stats)
                except Exception as e:
                    logger.warning("讨论组: 历史摘要计算失败: %s", e)

    summary = _build_shared_data_summary(shared_data, symbol)

    elapsed_ms = int((time.time() - start_time) * 1000)
    logger.info(
        "讨论组: 共享数据获取完成 %s: %d/%d 成功 (%dms)",
        symbol, succeeded, len(plan), elapsed_ms,
    )

    return {
        "shared_data": shared_data,
        "shared_data_summary": summary,
    }


# =============================================================================
# Node 2: Initial statements (one per agent type)
# =============================================================================


async def _make_initial_statement(
    state: DiscussionState,
    agent_type: str,
    config: Optional[RunnableConfig] = None,
) -> tuple:
    """Generate an initial statement for a single agent.

    Returns:
        (DiscussionMessage, int) tuple of the message and tokens consumed.
    """
    symbol = state["symbol"]
    language = state["language"]
    summary = state.get("shared_data_summary", "")

    lang_key = language if language in AGENT_ROLES else "en"
    role_name = AGENT_ROLES[lang_key][agent_type]
    expertise = AGENT_EXPERTISE[lang_key][agent_type]

    # Load and fill template
    suffix = "_zh" if language == "zh" else ""
    template = load_template(
        f"agent_discussion_instructions{suffix}.md",
        subdirectory="templates/discussion",
        variables={
            "agent_type": agent_type.capitalize(),
            "agent_role": role_name,
            "agent_expertise": expertise,
            "symbol": symbol,
            "shared_data_summary": summary,
            "discussion_thread": "(This is the start of the discussion. Present your initial analysis.)",
            "moderator_question": "Please provide your initial analysis and key findings.",
        },
    )

    handler = LlmUsageCallbackHandler(purpose="discussion", metadata={"agent_type": agent_type, "round": 0})
    start = time.time()

    try:
        llm = await _get_discussion_llm()
        llm_config: RunnableConfig = {"run_name": f"{agent_type}_initial_llm", "callbacks": [handler]}
        invoke_config = merge_configs(config, llm_config) if config else llm_config
        result = await asyncio.wait_for(
            llm.ainvoke(
                [SystemMessage(content=template)],
                config=invoke_config,
            ),
            timeout=LLM_TIMEOUT,
        )
        content = result.content if hasattr(result, "content") else str(result)
    except asyncio.TimeoutError:
        logger.warning("讨论组: %s 初始发言超时", agent_type)
        content = f"[{role_name}: Analysis timed out]"
    except Exception as e:
        logger.error("讨论组: %s 初始发言错误: %s", agent_type, e)
        content = f"[{role_name}: Error - {str(e)[:100]}]"

    latency = int((time.time() - start) * 1000)

    msg = DiscussionMessage(
        round=0,
        agent_type=agent_type,
        content=content,
        structured_data=None,
        tool_calls=None,
        token_count=None,
        latency_ms=latency,
    )
    return msg, handler.total_tokens


async def fundamental_initial_node(state: DiscussionState, config: RunnableConfig) -> Dict[str, Any]:
    """Fundamental analyst initial statement."""
    msg, tokens = await _make_initial_statement(state, "fundamental", config)
    return {"messages": [msg], "total_tokens": state.get("total_tokens", 0) + tokens}


async def technical_initial_node(state: DiscussionState, config: RunnableConfig) -> Dict[str, Any]:
    """Technical analyst initial statement."""
    msg, tokens = await _make_initial_statement(state, "technical", config)
    return {"messages": [msg], "total_tokens": state.get("total_tokens", 0) + tokens}


async def sentiment_initial_node(state: DiscussionState, config: RunnableConfig) -> Dict[str, Any]:
    """Sentiment analyst initial statement."""
    msg, tokens = await _make_initial_statement(state, "sentiment", config)
    return {"messages": [msg], "total_tokens": state.get("total_tokens", 0) + tokens}


async def news_initial_node(state: DiscussionState, config: RunnableConfig) -> Dict[str, Any]:
    """News analyst initial statement."""
    msg, tokens = await _make_initial_statement(state, "news", config)
    return {"messages": [msg], "total_tokens": state.get("total_tokens", 0) + tokens}


# =============================================================================
# Node 3: Moderator review
# =============================================================================


async def moderator_review_node(state: DiscussionState, config: RunnableConfig) -> Dict[str, Any]:
    """Chief Strategist reviews the discussion and decides next steps.

    Uses bind_tools() with DispatchRoundSkill to get structured dispatch
    as a native tool call (no regex JSON extraction needed).
    Falls back to _extract_json_control() if tool_calls is empty.
    """
    language = state["language"]
    current_round = state.get("current_round", 0)
    max_rounds = state.get("max_rounds", 3)
    summary = state.get("shared_data_summary", "")
    messages = state.get("messages", [])

    thread = _format_discussion_thread(messages, language)
    suffix = "_zh" if language == "zh" else ""

    template = load_template(
        f"moderator_instructions{suffix}.md",
        subdirectory="templates/discussion",
    )

    prompt = (
        f"{template}\n\n"
        f"## Shared Data\n{summary}\n\n"
        f"## Current Discussion Thread\n{thread}\n\n"
        f"## Status\nCurrent round: {current_round}, Max rounds: {max_rounds}\n"
    )

    handler = LlmUsageCallbackHandler(
        purpose="discussion",
        metadata={"agent_type": "moderator", "round": current_round},
    )
    start = time.time()

    content = ""
    action = "conclude"
    target_agents: List[str] = []
    focus_topics: List[str] = []
    dispatch_data: Optional[Dict[str, Any]] = None

    try:
        llm = await _get_discussion_llm()

        # Get dispatch skill from registry → build LangChain tool schema
        from app.skills.registry import get_skill_registry
        registry = get_skill_registry()
        dispatch_skill = registry.get("dispatch_round")

        if dispatch_skill:
            defn = dispatch_skill.definition()
            tool_schema = {
                "name": defn.name,
                "description": defn.description,
                "parameters": defn.to_json_schema(),
            }
            llm_with_tools = llm.bind_tools([tool_schema], tool_choice="required")
        else:
            logger.warning("讨论组: dispatch_round skill未注册, 回退到JSON解析")
            llm_with_tools = llm

        llm_config: RunnableConfig = {"run_name": "moderator_review_llm", "callbacks": [handler]}
        invoke_config = merge_configs(config, llm_config)

        result = await asyncio.wait_for(
            llm_with_tools.ainvoke(
                [SystemMessage(content=prompt)],
                config=invoke_config,
            ),
            timeout=LLM_TIMEOUT,
        )
        content = result.content if hasattr(result, "content") else ""

        # Extract and execute the tool call
        tool_calls = getattr(result, "tool_calls", [])
        if tool_calls and dispatch_skill:
            tc = tool_calls[0]
            tc_args = tc.get("args") or {}
            logger.info("讨论组: 主持人调用dispatch_round工具: %s", tc_args)
            # Execute the skill with tool args + runtime context
            skill_result = await dispatch_skill.safe_execute(
                timeout=30.0,
                **tc_args,
                symbol=state["symbol"],
                market=state["market"],
                shared_data=state.get("shared_data", {}),
            )
            if skill_result.success:
                dispatch_data = skill_result.data
                action = dispatch_data["action"]
                target_agents = dispatch_data["target_agents"]
                focus_topics = dispatch_data["focus_topics"]
            else:
                logger.warning("讨论组: dispatch_round执行失败: %s", skill_result.error)
        else:
            # Fallback to regex extraction (safety net for models without tool support)
            control = _extract_json_control(content)
            if control:
                logger.info("讨论组: 主持人回退到JSON控制块解析")
                action = control.get("action", "conclude")
                target_agents = control.get("target_agents", [])
                focus_topics = control.get("focus_topics", [])
                dispatch_data = control
            else:
                logger.warning(
                    "讨论组: 主持人未输出工具调用或JSON控制块, 默认conclude. content_len=%d preview='%s'",
                    len(content), content[:200],
                )

    except asyncio.TimeoutError:
        logger.warning("讨论组: 主持人审阅超时 round=%d", current_round)
        content = "Due to time constraints, let's move to synthesis."
        return {
            "should_continue": False,
            "target_agents": [],
            "focus_topics": [],
            "messages": [DiscussionMessage(
                round=current_round,
                agent_type="moderator",
                content=content,
                structured_data=None,
                tool_calls=None,
                token_count=None,
                latency_ms=int((time.time() - start) * 1000),
            )],
            "moderator_guidance": [content],
            "total_tokens": state.get("total_tokens", 0) + handler.total_tokens,
        }
    except Exception as e:
        logger.error("讨论组: 主持人审阅错误: %s", e)
        error_content = "Moderator review encountered an error. Moving to synthesis."
        return {
            "should_continue": False,
            "target_agents": [],
            "focus_topics": [],
            "messages": [DiscussionMessage(
                round=current_round,
                agent_type="moderator",
                content=error_content,
                structured_data=None,
                tool_calls=None,
                token_count=None,
                latency_ms=int((time.time() - start) * 1000),
            )],
            "moderator_guidance": [error_content],
            "total_tokens": state.get("total_tokens", 0) + handler.total_tokens,
            "errors": [f"Moderator review failed: {str(e)[:200]}"],
        }

    latency = int((time.time() - start) * 1000)

    # Force conclude if max rounds reached
    should_continue = (action == "direct_to_agent") and (current_round < max_rounds)

    # Filter to valid agent types
    target_agents = [a for a in target_agents if a in AGENT_TYPES]
    if should_continue and not target_agents:
        target_agents = list(AGENT_TYPES)

    logger.info(
        "讨论组: 主持人决策 round=%d action=%s continue=%s targets=%s topics=%d",
        current_round, action, should_continue, target_agents, len(focus_topics),
    )

    # Strip non-serializable updated_shared_data from structured_data for storage
    display_data: Optional[Dict[str, Any]] = None
    if dispatch_data:
        display_data = {k: v for k, v in dispatch_data.items() if k != "updated_shared_data"}

    moderator_msg = DiscussionMessage(
        round=current_round,
        agent_type="moderator",
        content=content,
        structured_data=display_data,
        tool_calls=None,
        token_count=None,
        latency_ms=latency,
    )

    state_update: Dict[str, Any] = {
        "should_continue": should_continue,
        "target_agents": target_agents,
        "focus_topics": focus_topics,
        "messages": [moderator_msg],
        "moderator_guidance": [content],
        "total_tokens": state.get("total_tokens", 0) + handler.total_tokens,
    }

    # If skill fetched extra data, update shared_data + rebuild summary
    if dispatch_data and dispatch_data.get("updated_shared_data"):
        new_shared = dispatch_data["updated_shared_data"]
        new_summary = _build_shared_data_summary(new_shared, state["symbol"])
        state_update["shared_data"] = new_shared
        state_update["shared_data_summary"] = new_summary
        logger.info("讨论组: 主持人请求额外数据, 获取 %d 项", dispatch_data.get("fetched_count", 0))

    return state_update


# =============================================================================
# Node 4: Agent respond (debate round)
# =============================================================================


async def agent_respond_node(state: DiscussionState, config: RunnableConfig) -> Dict[str, Any]:
    """Targeted agents respond to moderator's questions in a debate round."""
    target_agents = state.get("target_agents", [])
    if not target_agents:
        target_agents = list(AGENT_TYPES)

    language = state["language"]
    symbol = state["symbol"]
    current_round = state.get("current_round", 0) + 1  # increment round
    summary = state.get("shared_data_summary", "")
    messages = state.get("messages", [])
    focus_topics = state.get("focus_topics", [])
    guidance = state.get("moderator_guidance", [])

    thread = _format_discussion_thread(messages, language)
    latest_guidance = guidance[-1] if guidance else "Continue the discussion."

    lang_key = language if language in AGENT_ROLES else "en"
    suffix = "_zh" if language == "zh" else ""

    async def _agent_respond(agent_type: str) -> tuple:
        """Generate a debate response for a single agent.

        Returns:
            (DiscussionMessage, int) tuple of the message and tokens consumed.
        """
        role_name = AGENT_ROLES[lang_key][agent_type]
        expertise = AGENT_EXPERTISE[lang_key][agent_type]

        # Build focus topics string
        topics_str = ", ".join(focus_topics) if focus_topics else "Continue your analysis"

        template = load_template(
            f"agent_discussion_instructions{suffix}.md",
            subdirectory="templates/discussion",
            variables={
                "agent_type": agent_type.capitalize(),
                "agent_role": role_name,
                "agent_expertise": expertise,
                "symbol": symbol,
                "shared_data_summary": summary,
                "discussion_thread": thread,
                "moderator_question": f"{latest_guidance}\n\nFocus topics: {topics_str}",
            },
        )

        handler = LlmUsageCallbackHandler(
            purpose="discussion",
            metadata={"agent_type": agent_type, "round": current_round},
        )
        start = time.time()

        try:
            llm = await _get_discussion_llm()
            llm_config: RunnableConfig = {"run_name": f"{agent_type}_debate_llm", "callbacks": [handler]}
            invoke_config = merge_configs(config, llm_config)
            result = await asyncio.wait_for(
                llm.ainvoke(
                    [SystemMessage(content=template)],
                    config=invoke_config,
                ),
                timeout=LLM_TIMEOUT,
            )
            content = result.content if hasattr(result, "content") else str(result)
        except asyncio.TimeoutError:
            logger.warning("讨论组: %s 辩论超时 round=%d", agent_type, current_round)
            content = f"[{role_name}: Response timed out for this round]"
        except Exception as e:
            logger.error("讨论组: %s 辩论错误: %s", agent_type, e)
            content = f"[{role_name}: Error - {str(e)[:100]}]"

        latency = int((time.time() - start) * 1000)

        msg = DiscussionMessage(
            round=current_round,
            agent_type=agent_type,
            content=content,
            structured_data=None,
            tool_calls=None,
            token_count=None,
            latency_ms=latency,
        )
        return msg, handler.total_tokens

    # NOTE: Agents in the same debate round do not see each other's responses.
    # The thread snapshot is built once before the loop. Sequential execution is
    # for UX streaming (so users see agents respond one at a time), not for
    # inter-agent awareness within a round.
    logger.info("讨论组: 辩论轮开始 round=%d, agents=%s", current_round, target_agents)
    new_messages = []
    round_tokens = 0
    for agent_type in target_agents:
        logger.info("讨论组: 开始 %s 辩论 (round=%d)", agent_type, current_round)
        # Emit start event (best-effort, for frontend streaming UI)
        try:
            await adispatch_custom_event(
                "agent_debate_start",
                {"agent_type": agent_type, "round": current_round},
                config=config,
            )
        except Exception:
            pass  # Non-critical
        try:
            result, tokens = await _agent_respond(agent_type)
            new_messages.append(result)
            round_tokens += tokens
            logger.info("讨论组: 完成 %s 辩论 (%dms)", agent_type, result.get("latency_ms", 0))
        except Exception as e:
            logger.error("讨论组: %s 辩论LLM调用异常: %s", agent_type, e)
            continue
        # Emit completion event (separate try to avoid losing the appended message)
        try:
            await adispatch_custom_event(
                "agent_debate_complete",
                {
                    "agent_type": agent_type,
                    "content": result["content"],
                    "round": current_round,
                    "latency_ms": result.get("latency_ms", 0),
                },
                config=config,
            )
        except Exception as e:
            logger.error("讨论组: %s adispatch_custom_event失败: %s", agent_type, e)

    return {
        "messages": new_messages,
        "current_round": current_round,
        "total_tokens": state.get("total_tokens", 0) + round_tokens,
    }


# =============================================================================
# Node 5: Final synthesis
# =============================================================================


async def final_synthesis_node(state: DiscussionState, config: RunnableConfig) -> Dict[str, Any]:
    """Generate the final synthesis report and compact context."""
    language = state["language"]
    symbol = state["symbol"]
    summary = state.get("shared_data_summary", "")
    messages = state.get("messages", [])

    thread = _format_discussion_thread(messages, language)
    suffix = "_zh" if language == "zh" else ""

    template = load_template(
        f"synthesis_discussion_instructions{suffix}.md",
        subdirectory="templates/discussion",
        variables={
            "symbol": symbol,
            "discussion_thread": thread,
            "shared_data_summary": summary,
        },
    )

    handler = LlmUsageCallbackHandler(
        purpose="discussion",
        metadata={"agent_type": "synthesis", "round": -1},
    )
    start = time.time()

    try:
        llm = await _get_discussion_llm()
        llm_config: RunnableConfig = {"run_name": "synthesis_llm", "callbacks": [handler]}
        invoke_config = merge_configs(config, llm_config)
        result = await asyncio.wait_for(
            llm.ainvoke(
                [SystemMessage(content=template)],
                config=invoke_config,
            ),
            timeout=120,  # synthesis gets more time
        )
        report = result.content if hasattr(result, "content") else str(result)
    except asyncio.TimeoutError:
        logger.error("讨论组: 综合报告生成超时 %s", symbol)
        report = "## Synthesis Error\n\nSynthesis generation timed out. Please review the discussion thread above."
    except Exception as e:
        logger.error("讨论组: 综合报告生成错误 %s: %s", symbol, e)
        report = f"## Synthesis Error\n\nFailed to generate synthesis: {str(e)[:200]}"

    latency = int((time.time() - start) * 1000)

    # Build compact context for chat phase
    # Extract key conflicts from moderator messages
    moderator_msgs = [m for m in messages if m.get("agent_type") == "moderator"]
    conflicts_summary = "\n".join(
        m.get("content", "")[:500] for m in moderator_msgs[-2:]
    ) if moderator_msgs else ""

    compact_context = (
        f"## Discussion Synthesis for {symbol}\n\n"
        f"{report[:3000]}\n\n"
        f"## Key Discussion Points\n{conflicts_summary[:1000]}"
    )

    synthesis_msg = DiscussionMessage(
        round=-1,
        agent_type="synthesis",
        content=report,
        structured_data=None,
        tool_calls=None,
        token_count=None,
        latency_ms=latency,
    )

    return {
        "synthesis_report": report,
        "compact_context": compact_context,
        "messages": [synthesis_msg],
        "total_tokens": state.get("total_tokens", 0) + handler.total_tokens,
    }
