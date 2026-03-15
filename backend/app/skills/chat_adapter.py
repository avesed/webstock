"""Adapter that bridges the Skills system to the Chat service.

Converts SkillDefinitions to ToolDefinitions for LLM function calling,
and routes tool call execution through the skill registry with user context
injection and result truncation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm.types import ToolDefinition
from app.skills.base import BaseSkill, SkillResult

logger = logging.getLogger(__name__)

# Maximum characters per tool result to prevent context explosion
MAX_TOOL_RESULT_CHARS = 2000

# Per-tool execution timeout in seconds
TOOL_TIMEOUT_SECONDS = 15

# Extended timeouts for long-running ML skills (profile builds feature
# matrices ~30-120s, validation runs multi-day inference ~60-300s)
_SKILL_TIMEOUTS: dict[str, int] = {
    "ml_profile_data": 600,
    "ml_submit_training": 60,
    "ml_get_training_status": 30,
    "ml_run_validation": 660,
    "ml_run_rolling_backtest": 60,  # Async submit — returns task_id immediately
    "ml_deploy_config": 60,
}

# Skills exposed to the chat function-calling agent
CHAT_SKILL_NAMES = [
    "get_stock_quote",
    "get_stock_history",
    "get_stock_info",
    "get_stock_financials",
    "search_stocks",
    "search_related_stocks",
    "get_news",
    "get_portfolio",
    "get_watchlist",
    "search_knowledge_base",
    "qlib_compute_factors",
    "qlib_evaluate_expression",
    "qlib_create_backtest",
    "qlib_get_backtest",
    "optimize_portfolio",
    "get_analyst_ratings",
    "get_market_context",
]

# Discussion expert skills (only exposed in discussion conversations)
DISCUSSION_SKILL_NAMES = [
    "ask_fundamental_expert",
    "ask_technical_expert",
    "ask_sentiment_expert",
    "ask_news_expert",
]

# Admin-only ML skills (only exposed when is_admin=True)
ADMIN_SKILL_NAMES = [
    "ml_profile_data",
    "ml_submit_training",
    "ml_get_training_status",
    "ml_run_validation",
    "ml_run_rolling_backtest",
    "ml_deploy_config",
]

# Friendly display labels for tool calls (used by SSE events)
TOOL_LABELS = {
    "get_stock_quote": "获取实时报价",
    "get_stock_history": "获取历史数据",
    "get_stock_info": "获取公司信息",
    "get_stock_financials": "获取财务数据",
    "search_stocks": "搜索股票",
    "search_related_stocks": "搜索相关股票",
    "get_news": "获取新闻",
    "get_portfolio": "查看投资组合",
    "get_watchlist": "查看关注列表",
    "search_knowledge_base": "搜索知识库",
    "qlib_compute_factors": "计算量化因子",
    "qlib_evaluate_expression": "计算量化表达式",
    "qlib_create_backtest": "创建量化回测",
    "qlib_get_backtest": "获取回测结果",
    "optimize_portfolio": "优化投资组合",
    "get_analyst_ratings": "获取分析师评级",
    "get_market_context": "获取市场概况",
    "ask_fundamental_expert": "咨询基本面专家",
    "ask_technical_expert": "咨询技术面专家",
    "ask_sentiment_expert": "咨询情绪面专家",
    "ask_news_expert": "咨询新闻面专家",
    "ml_profile_data": "分析数据特征",
    "ml_submit_training": "提交模型训练",
    "ml_get_training_status": "查询训练状态",
    "ml_run_validation": "运行模型验证",
    "ml_run_rolling_backtest": "提交滚动回测",
    "ml_deploy_config": "部署训练配置",
}

# Skills that need user_id and db injection
_USER_SCOPED_SKILLS = {"get_portfolio", "get_watchlist", "qlib_create_backtest", "qlib_get_backtest", "ask_fundamental_expert", "ask_technical_expert", "ask_sentiment_expert", "ask_news_expert", "ml_profile_data", "ml_submit_training", "ml_get_training_status", "ml_run_validation", "ml_run_rolling_backtest", "ml_deploy_config"}
_DB_SCOPED_SKILLS = {"get_portfolio", "get_watchlist", "search_knowledge_base", "search_related_stocks", "get_news", "qlib_create_backtest", "qlib_get_backtest", "ask_fundamental_expert", "ask_technical_expert", "ask_sentiment_expert", "ask_news_expert"}
_DISCUSSION_SCOPED_SKILLS = {"ask_fundamental_expert", "ask_technical_expert", "ask_sentiment_expert", "ask_news_expert"}


def skill_to_tool_definition(skill: BaseSkill) -> ToolDefinition:
    """Convert a Skill to a ToolDefinition for LLM function calling."""
    defn = skill.definition()
    return ToolDefinition(
        name=defn.name,
        description=defn.description,
        parameters=defn.to_json_schema(),
    )


def get_chat_tools(include_discussion: bool = False, is_admin: bool = False) -> List[ToolDefinition]:
    """Get ToolDefinition list for chat function calling.

    Args:
        include_discussion: If True, include discussion expert skills
            (only for conversations linked to a discussion session)
        is_admin: If True, include admin-only ML skills
    """
    from app.skills.registry import get_skill_registry

    registry = get_skill_registry()
    skill_names = list(CHAT_SKILL_NAMES)
    if include_discussion:
        skill_names.extend(DISCUSSION_SKILL_NAMES)
    if is_admin:
        skill_names.extend(ADMIN_SKILL_NAMES)
    tools: List[ToolDefinition] = []
    for name in skill_names:
        skill = registry.get(name)
        if skill is not None:
            tools.append(skill_to_tool_definition(skill))
    return tools


def get_tool_label(tool_name: str, args: Dict[str, Any]) -> str:
    """Build a human-readable label for a tool call."""
    base = TOOL_LABELS.get(tool_name, tool_name)
    symbol = args.get("symbol")
    query = args.get("query")
    if symbol:
        return f"{base}: {symbol}"
    if query:
        return f"{base}: {query[:30]}"
    return base


def _smart_serialize(data: Any, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    """Serialize data to JSON with structure-aware truncation.

    Preserves valid JSON and financial precision by intelligently trimming
    lists and dicts rather than hard-cutting the serialized string.
    """
    try:
        full = json.dumps(data, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        full = str(data)
    if len(full) <= max_chars:
        return full
    if isinstance(data, list):
        return _serialize_list(data, max_chars)
    if isinstance(data, dict):
        return _serialize_dict(data, max_chars)
    # Fallback: hard truncate (non-list/dict values that are too long)
    logger.debug("Smart serialize fallback: hard truncate %d -> %d chars", len(full), max_chars)
    return full[: max_chars - 3] + "..."


def _serialize_list(items: list, max_chars: int) -> str:
    """Truncate a list to fit within max_chars using binary search.

    Output format: first N items + an omitted-count sentinel object.
    Always produces valid JSON.
    """
    total = len(items)
    suffix_template = '{{"_omitted": "{n}/{t} items not shown"}}'

    # Check if zero items fit (just the omitted marker)
    zero_result = json.dumps(
        [{"_omitted": f"all {total} items too large"}],
        ensure_ascii=False, default=str,
    )
    if total == 0:
        return "[]"

    # Pre-serialize each item once so binary search is O(n) total, not O(n^2).
    # Each serialized item contributes its length + 1 for the comma separator.
    item_strs = []
    for item in items:
        try:
            item_strs.append(json.dumps(item, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            item_strs.append(json.dumps(str(item), ensure_ascii=False, default=str))

    # Build a prefix-sum of the serialized length when including items 0..i.
    # json.dumps separates elements with ", " (2 chars).
    # For n items + suffix: "[" + item0 + ", " + item1 + ... + ", " + suffix + "]"
    #   = 1 + sum(item_lens) + 2*(n) separators (n items + suffix = n+1 elements, n separators) + len(suffix) + 1
    # For n items without suffix (all fit): "[" + items + 2*(n-1) separators + "]"
    # For n=0: just the zero_result fallback.
    prefix_lens = []
    cumulative = 0
    for s in item_strs:
        cumulative += len(s)
        prefix_lens.append(cumulative)

    def _total_len(n: int) -> int:
        """Compute output length when keeping the first n items."""
        if n == 0:
            return len(zero_result)
        omitted = total - n
        if omitted == 0:
            # All items fit — no suffix: "[" + items + ", " separators + "]"
            return 1 + prefix_lens[n - 1] + 2 * (n - 1) + 1
        suffix = suffix_template.format(n=omitted, t=total)
        # "[" + items + ", " between each pair + ", " before suffix + suffix + "]"
        # n items + 1 suffix = n+1 elements → n separators of ", " (2 chars each)
        return 1 + prefix_lens[n - 1] + 2 * n + len(suffix) + 1

    # If all items fit, return full serialization (shouldn't reach here
    # normally since caller checked, but guard anyway).
    if _total_len(total) <= max_chars:
        return json.dumps(items, ensure_ascii=False, default=str)

    # Binary search for the largest n where _total_len(n) <= max_chars.
    lo, hi, best = 0, total - 1, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if _total_len(mid) <= max_chars:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1

    if best == 0:
        return zero_result

    kept = items[:best]
    omitted = total - best
    kept.append({"_omitted": f"{omitted}/{total} items not shown"})
    return json.dumps(kept, ensure_ascii=False, default=str)


def _serialize_dict(data: dict, max_chars: int) -> str:
    """Truncate a dict keeping all scalar fields and selectively including complex ones.

    Scalar fields (str, int, float, bool, None) are always preserved to
    maintain financial data precision.  Complex fields (list, dict) are
    added one by one; oversized lists are sampled and oversized dicts
    replaced with a placeholder.
    """
    _dumps = lambda v: json.dumps(v, ensure_ascii=False, default=str)

    scalars: Dict[str, Any] = {}
    complex_keys: list[str] = []
    for k, v in data.items():
        if isinstance(v, (str, int, float, bool, type(None))):
            scalars[k] = v
        else:
            complex_keys.append(k)

    # If scalars alone already exceed budget, hard-truncate as last resort.
    scalars_json = _dumps(scalars)
    if len(scalars_json) > max_chars:
        return scalars_json[: max_chars - 3] + "..."

    # If no complex fields, we are done.
    if not complex_keys:
        return scalars_json

    # Incrementally add complex fields.
    result = dict(scalars)
    for key in complex_keys:
        value = data[key]
        candidate = dict(result)
        candidate[key] = value
        candidate_json = _dumps(candidate)

        if len(candidate_json) <= max_chars:
            result[key] = value
            continue

        # Try to fit a reduced version of the value.
        if isinstance(value, list) and len(value) > 0:
            reduced = _try_reduce_list_field(value, key, result, max_chars, _dumps)
            if reduced is not None:
                result[key] = reduced
                continue

        if isinstance(value, dict):
            # Replace with a placeholder.
            candidate2 = dict(result)
            candidate2[key] = "{...}"
            if len(_dumps(candidate2)) <= max_chars:
                result[key] = "{...}"
                continue

        # Skip this field entirely — doesn't fit even as placeholder.

    # If we added nothing beyond scalars and there were complex fields, mark truncated.
    if set(result.keys()) == set(scalars.keys()) and complex_keys:
        logger.debug(
            "Dict serialization: all %d complex fields dropped, keeping %d scalars",
            len(complex_keys), len(scalars),
        )
        candidate = dict(result)
        candidate["_truncated"] = True
        if len(_dumps(candidate)) <= max_chars:
            result["_truncated"] = True

    return _dumps(result)


def _try_reduce_list_field(
    value: list,
    key: str,
    current: dict,
    max_chars: int,
    _dumps: Any,
) -> Any:
    """Try to fit a list field by sampling first 3 or 1 items with a suffix marker."""
    total = len(value)
    for sample_size in (3, 1):
        if sample_size >= total:
            continue
        sampled = list(value[:sample_size])
        sampled.append(f"...+{total - sample_size}")
        candidate = dict(current)
        candidate[key] = sampled
        if len(_dumps(candidate)) <= max_chars:
            return sampled
    return None


async def execute_chat_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    user_id: int,
    db: AsyncSession,
    discussion_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a skill in chat context with user scoping and truncation.

    Returns a dict with 'result' key on success or 'error' key on failure.
    For search_knowledge_base, includes 'raw_sources' for RAG event emission.
    """
    from app.skills.registry import get_skill_registry

    registry = get_skill_registry()
    skill = registry.get(tool_name)
    if skill is None:
        return {"error": f"Unknown tool: {tool_name}"}

    try:
        # Build kwargs from arguments + injected context
        kwargs = dict(arguments)
        if tool_name in _USER_SCOPED_SKILLS:
            kwargs["user_id"] = user_id
        if tool_name in _DB_SCOPED_SKILLS:
            kwargs["db"] = db
        if tool_name in _DISCUSSION_SCOPED_SKILLS:
            kwargs["discussion_session_id"] = discussion_session_id

        timeout = _SKILL_TIMEOUTS.get(tool_name, TOOL_TIMEOUT_SECONDS)
        result = await asyncio.wait_for(
            skill.execute(**kwargs),
            timeout=timeout,
        )

        if not result.success:
            return {"error": result.error or f"Tool {tool_name} failed"}

        out: Dict[str, Any] = {"result": _smart_serialize(result.data)}

        # Preserve raw list result for knowledge_base so caller can emit
        # rag_sources without re-parsing the truncated string
        if tool_name == "search_knowledge_base" and isinstance(result.data, list):
            out["raw_sources"] = result.data

        return out

    except asyncio.TimeoutError:
        timeout = _SKILL_TIMEOUTS.get(tool_name, TOOL_TIMEOUT_SECONDS)
        logger.warning("Tool %s timed out after %ds", tool_name, timeout)
        return {"error": f"Tool {tool_name} timed out"}
    except asyncio.CancelledError:
        logger.warning("Tool %s was cancelled", tool_name)
        return {"error": f"Tool {tool_name} was cancelled"}
    except Exception as e:
        logger.exception("Tool %s execution failed: %s", tool_name, e)
        return {"error": f"Tool {tool_name} failed"}
