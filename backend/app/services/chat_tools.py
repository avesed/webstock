"""Chat agent tool definitions and executor for function calling.

Tool definitions use the provider-agnostic ToolDefinition type from the
LLM Gateway.  The gateway's OpenAI provider converts them to OpenAI format
internally; Anthropic provider converts to Anthropic format, etc.

All database operations use async SQLAlchemy ORM to prevent SQL injection.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import HTTPException
from app.core.llm import ToolDefinition
from app.prompts.analysis.sanitizer import sanitize_input, sanitize_symbol
from app.utils.symbol_validation import validate_symbol as _validate_symbol

logger = logging.getLogger(__name__)

# Maximum characters per tool result to prevent context explosion
MAX_TOOL_RESULT_CHARS = 500

# Per-tool execution timeout in seconds
TOOL_TIMEOUT_SECONDS = 15


def _normalize_symbol(raw: Optional[str]) -> str:
    """Sanitize and normalize a stock symbol.

    Applies sanitize_symbol for injection protection, then validate_symbol
    for market-specific normalization (e.g. 01810.HK -> 1810.HK).
    Falls back to the sanitized-only value if validation raises.
    """
    sanitized = sanitize_symbol(raw)
    try:
        return _validate_symbol(sanitized)
    except (HTTPException, Exception):
        return sanitized

# Valid enum values for history tool
VALID_PERIODS = {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"}
VALID_INTERVALS = {"1m", "5m", "15m", "1h", "1d", "1wk", "1mo"}


# ---------------------------------------------------------------------------
# Tool definitions (provider-agnostic ToolDefinition)
# ---------------------------------------------------------------------------

CHAT_TOOLS: List[ToolDefinition] = [
    ToolDefinition(
        name="get_stock_quote",
        description=(
            "Get real-time stock quote including price, change, volume, "
            "and market cap. Use when the user asks about current price."
        ),
        parameters={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Stock ticker (e.g. AAPL, 0700.HK, 600519.SS)",
                }
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="get_stock_history",
        description=(
            "Get historical OHLCV price data. Use for trend analysis "
            "or when the user asks about past performance."
        ),
        parameters={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker"},
                "period": {
                    "type": "string",
                    "enum": sorted(VALID_PERIODS),
                    "description": "Time period (default 1y)",
                },
                "interval": {
                    "type": "string",
                    "enum": sorted(VALID_INTERVALS),
                    "description": "Data interval (default 1d)",
                },
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="get_stock_info",
        description=(
            "Get company information: description, sector, industry, "
            "website, employee count. Use when the user asks about a company."
        ),
        parameters={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker"}
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="get_stock_financials",
        description=(
            "Get financial metrics: PE ratio, EPS, margins, ROE, "
            "debt ratios, dividend data. Use for fundamental analysis."
        ),
        parameters={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker"}
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="search_stocks",
        description=(
            "Search for stocks by name or ticker across US, HK, "
            "and China A-share markets."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Company name or partial ticker",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="get_news",
        description=(
            "Get news for a stock from two sources: (1) realtime API headlines "
            "with sentiment, (2) embedded full-content articles from knowledge base. "
            "Use when the user asks about news, events, or wants detailed coverage."
        ),
        parameters={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker"}
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="get_portfolio",
        description=(
            "Get the user's portfolio summary including holdings, "
            "total value, and performance. Use when the user asks "
            "about their portfolio."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="get_watchlist",
        description=(
            "Get the user's watchlist symbols. Use when the user "
            "asks about stocks they are watching."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="search_knowledge_base",
        description=(
            "Search the internal knowledge base of past analysis reports, "
            "news articles, and research. Use for context about past analyses "
            "or when the user references previous reports."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query",
                },
                "symbol": {
                    "type": "string",
                    "description": "Optional: filter to a specific stock symbol",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
]


# ---------------------------------------------------------------------------
# Friendly display labels for tool calls (used by SSE events)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Tool executor
# ---------------------------------------------------------------------------

def _truncate(text: str, max_len: int = MAX_TOOL_RESULT_CHARS) -> str:
    """Truncate text to max length with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _sanitize_result(result: Any) -> str:
    """Convert tool result to a sanitized JSON string."""
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(result)
    return _truncate(text)


async def execute_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    user_id: int,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Execute a chat tool and return the result.

    All tool executions are wrapped in try/except and timeout.
    Arguments are sanitized before passing to services.

    Args:
        tool_name: Name of the tool to execute
        arguments: LLM-generated arguments (will be sanitized)
        user_id: Current user's ID (for user-scoped tools)
        db: Database session (for tools needing DB access)

    Returns:
        Dict with 'result' key on success or 'error' key on failure
    """
    try:
        result = await asyncio.wait_for(
            _dispatch_tool(tool_name, arguments, user_id, db),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
        out: Dict[str, Any] = {"result": _sanitize_result(result)}
        # Preserve raw list result for knowledge_base so caller can emit
        # rag_sources without re-parsing the truncated string.
        if tool_name == "search_knowledge_base" and isinstance(result, list):
            out["raw_sources"] = result
        return out
    except asyncio.TimeoutError:
        logger.warning("Tool %s timed out after %ds", tool_name, TOOL_TIMEOUT_SECONDS)
        return {"error": f"Tool {tool_name} timed out"}
    except Exception as e:
        logger.exception("Tool %s execution failed: %s", tool_name, e)
        return {"error": f"Tool {tool_name} failed"}


async def _dispatch_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    user_id: int,
    db: AsyncSession,
) -> Any:
    """Route tool call to the appropriate service method."""

    if tool_name == "get_stock_quote":
        symbol = _normalize_symbol(arguments.get("symbol"))
        from app.services.stock_service import get_stock_service
        service = await get_stock_service()
        result = await service.get_quote(symbol)
        return result or {"error": f"No quote data available for {symbol}"}

    elif tool_name == "get_stock_history":
        from app.services.stock_service import (
            HistoryInterval,
            HistoryPeriod,
            get_stock_service,
        )
        symbol = _normalize_symbol(arguments.get("symbol"))
        period_str = arguments.get("period", "1y")
        interval_str = arguments.get("interval", "1d")

        # Validate enum values
        if period_str not in VALID_PERIODS:
            period_str = "1y"
        if interval_str not in VALID_INTERVALS:
            interval_str = "1d"

        period = HistoryPeriod(period_str)
        interval = HistoryInterval(interval_str)

        service = await get_stock_service()
        data = await service.get_history(symbol, period, interval)
        if not data:
            return {"error": f"No history data available for {symbol}"}

        # Truncate bars to last 30 and add summary
        bars = data.get("bars", [])
        summary = {
            "symbol": symbol,
            "period": period_str,
            "interval": interval_str,
            "total_bars": len(bars),
        }
        if bars:
            closes = [b.get("close", 0) for b in bars if b.get("close") is not None]
            if closes:
                summary["high"] = max(closes)
                summary["low"] = min(closes)
                summary["latest_close"] = closes[-1]
            summary["recent_bars"] = bars[-10:]  # Last 10 only
        return summary

    elif tool_name == "get_stock_info":
        symbol = _normalize_symbol(arguments.get("symbol"))
        from app.services.stock_service import get_stock_service
        service = await get_stock_service()
        result = await service.get_info(symbol)
        return result or {"error": f"No info available for {symbol}"}

    elif tool_name == "get_stock_financials":
        symbol = _normalize_symbol(arguments.get("symbol"))
        from app.services.stock_service import get_stock_service
        service = await get_stock_service()
        result = await service.get_financials(symbol)
        return result or {"error": f"No financial data available for {symbol}"}

    elif tool_name == "search_stocks":
        query = sanitize_input(arguments.get("query", ""), max_length=100)
        if not query or query == "N/A":
            return {"error": "Search query is required"}
        from app.services.stock_service import get_stock_service
        service = await get_stock_service()
        results = await service.search(query)
        return [r.to_dict() if hasattr(r, "to_dict") else r for r in results[:10]]

    elif tool_name == "get_news":
        symbol = _normalize_symbol(arguments.get("symbol"))
        from app.services.news_service import get_news_service
        from app.services.embedding_service import get_embedding_service, get_embedding_model_from_db
        from app.services.rag_service import get_rag_service

        news_service = await get_news_service()
        embedding_service = get_embedding_service()
        rag_service = get_rag_service()
        embedding_model = await get_embedding_model_from_db(db)

        # Parallel fetch: realtime news + embedding generation
        realtime_task = news_service.get_news_by_symbol(symbol)
        embedding_task = embedding_service.generate_embedding(
            f"latest news about {symbol} stock", model=embedding_model
        )

        articles, query_embedding = await asyncio.gather(
            realtime_task, embedding_task, return_exceptions=True
        )

        # Handle exceptions from gather
        if isinstance(articles, Exception):
            logger.warning("Failed to fetch realtime news for %s: %s", symbol, articles)
            articles = []
        if isinstance(query_embedding, Exception):
            logger.warning("Failed to generate embedding for %s: %s", symbol, query_embedding)
            query_embedding = None

        # Build result list
        trimmed = []

        # Add realtime news (limit to 4 to leave room for RAG)
        for a in (articles or [])[:4]:
            trimmed.append({
                "title": (a.get("title") or "")[:150],
                "source": a.get("source"),
                "published_at": a.get("published_at"),
                "summary": (a.get("summary") or "")[:150],
                "sentiment_score": a.get("sentiment_score"),
                "type": "realtime",
            })

        # Query RAG for embedded news full content
        if query_embedding:
            try:
                rag_results = await rag_service.vector_search_only(
                    db=db,
                    query_embedding=query_embedding,
                    symbol=symbol,
                    source_type="news",
                    top_k=2,
                )
                for r in rag_results:
                    trimmed.append({
                        "title": "深度内容",
                        "source": "knowledge_base",
                        "text": r.chunk_text[:200],
                        "score": round(r.score, 3),
                        "type": "full_content",
                    })
            except Exception as e:
                logger.warning("RAG search failed for %s: %s", symbol, e)

        return trimmed or {"info": f"No news found for {symbol}"}

    elif tool_name == "get_portfolio":
        from sqlalchemy import select
        from app.models.portfolio import Portfolio
        from app.services.portfolio_service import PortfolioService

        svc = PortfolioService(db)
        # Find user's default portfolio
        result = await db.execute(
            select(Portfolio).where(
                Portfolio.user_id == user_id,
                Portfolio.is_default == True,
            )
        )
        portfolio = result.scalar_one_or_none()
        if not portfolio:
            return {"info": "No portfolio found. Create one in the Portfolio page."}

        summary = await svc.get_portfolio_summary(portfolio)
        return {
            "name": summary.portfolio_name,
            "currency": summary.currency,
            "total_cost": str(summary.total_cost),
            "total_market_value": str(summary.total_market_value) if summary.total_market_value else None,
            "total_profit_loss": str(summary.total_profit_loss) if summary.total_profit_loss else None,
            "total_profit_loss_percent": summary.total_profit_loss_percent,
            "holdings_count": summary.holdings_count,
            "day_change": str(summary.day_change) if summary.day_change else None,
            "day_change_percent": summary.day_change_percent,
        }

    elif tool_name == "get_watchlist":
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from app.models.watchlist import Watchlist

        result = await db.execute(
            select(Watchlist)
            .where(Watchlist.user_id == user_id, Watchlist.is_default == True)
            .options(selectinload(Watchlist.items))
        )
        watchlist = result.scalar_one_or_none()
        if not watchlist or not watchlist.items:
            return {"info": "Watchlist is empty."}
        symbols = [item.symbol for item in watchlist.items]
        return {"watchlist": watchlist.name, "symbols": symbols}

    elif tool_name == "search_knowledge_base":
        query = sanitize_input(arguments.get("query", ""), max_length=500)
        symbol = arguments.get("symbol")
        if symbol:
            symbol = _normalize_symbol(symbol)

        from app.services.embedding_service import get_embedding_service, get_embedding_model_from_db
        from app.services.rag_service import get_rag_service

        embedding_svc = get_embedding_service()
        rag_svc = get_rag_service()
        embedding_model = await get_embedding_model_from_db(db)

        # Generate embedding for query
        query_embedding = await embedding_svc.generate_embedding(query, model=embedding_model)
        if not query_embedding:
            return {"info": "Could not generate embedding for search query"}

        results = await rag_svc.search(
            db=db,
            query_embedding=query_embedding,
            query_text=query,
            symbol=symbol,
            top_k=3,
        )
        if not results:
            return {"info": "No relevant documents found in knowledge base"}

        return [r.to_dict() for r in results]

    else:
        return {"error": f"Unknown tool: {tool_name}"}
