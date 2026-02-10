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
MAX_TOOL_RESULT_CHARS = 500

# Per-tool execution timeout in seconds
TOOL_TIMEOUT_SECONDS = 15

# Skills exposed to the chat function-calling agent
CHAT_SKILL_NAMES = [
    "get_stock_quote",
    "get_stock_history",
    "get_stock_info",
    "get_stock_financials",
    "search_stocks",
    "get_news",
    "get_portfolio",
    "get_watchlist",
    "search_knowledge_base",
]

# Friendly display labels for tool calls (used by SSE events)
TOOL_LABELS = {
    "get_stock_quote": "获取实时报价",
    "get_stock_history": "获取历史数据",
    "get_stock_info": "获取公司信息",
    "get_stock_financials": "获取财务数据",
    "search_stocks": "搜索股票",
    "get_news": "获取新闻",
    "get_portfolio": "查看投资组合",
    "get_watchlist": "查看关注列表",
    "search_knowledge_base": "搜索知识库",
}

# Skills that need user_id and db injection
_USER_SCOPED_SKILLS = {"get_portfolio", "get_watchlist"}
_DB_SCOPED_SKILLS = {"get_portfolio", "get_watchlist", "search_knowledge_base", "get_news"}


def skill_to_tool_definition(skill: BaseSkill) -> ToolDefinition:
    """Convert a Skill to a ToolDefinition for LLM function calling."""
    defn = skill.definition()
    return ToolDefinition(
        name=defn.name,
        description=defn.description,
        parameters=defn.to_json_schema(),
    )


def get_chat_tools() -> List[ToolDefinition]:
    """Get ToolDefinition list for chat function calling."""
    from app.skills.registry import get_skill_registry

    registry = get_skill_registry()
    tools: List[ToolDefinition] = []
    for name in CHAT_SKILL_NAMES:
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


def _truncate(text: str, max_len: int = MAX_TOOL_RESULT_CHARS) -> str:
    """Truncate text to max length with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _sanitize_result(result: Any) -> str:
    """Convert tool result to a sanitized JSON string."""
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(result)
    return _truncate(text)


async def execute_chat_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    user_id: int,
    db: AsyncSession,
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

        result = await asyncio.wait_for(
            skill.execute(**kwargs),
            timeout=TOOL_TIMEOUT_SECONDS,
        )

        if not result.success:
            return {"error": result.error or f"Tool {tool_name} failed"}

        out: Dict[str, Any] = {"result": _sanitize_result(result.data)}

        # Preserve raw list result for knowledge_base so caller can emit
        # rag_sources without re-parsing the truncated string
        if tool_name == "search_knowledge_base" and isinstance(result.data, list):
            out["raw_sources"] = result.data

        return out

    except asyncio.TimeoutError:
        logger.warning("Tool %s timed out after %ds", tool_name, TOOL_TIMEOUT_SECONDS)
        return {"error": f"Tool {tool_name} timed out"}
    except Exception as e:
        logger.exception("Tool %s execution failed: %s", tool_name, e)
        return {"error": f"Tool {tool_name} failed"}
