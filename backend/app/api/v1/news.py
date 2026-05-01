"""News API endpoints -- proxy-only mode via NewsForge."""

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.models.watchlist import Watchlist, WatchlistItem
from app.schemas.news import (
    NewsAnalysisRequest,
    NewsAnalysisResponse,
    NewsFeedResponse,
    NewsResponse,
    SentimentTimelineResponse,
    TrendingNewsResponse,
)
from app.services.newsforge_proxy import NewsForgeProxy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/news", tags=["News"])

# Rate limiting configurations for different endpoints
# Symbol news: 100 requests per minute
SYMBOL_NEWS_RATE_LIMIT = rate_limit(max_requests=100, window_seconds=60, key_prefix="news_symbol")
# Feed/Trending: 30 requests per minute
FEED_RATE_LIMIT = rate_limit(max_requests=30, window_seconds=60, key_prefix="news_feed")
# Analyze: 10 requests per minute (uses AI)
ANALYZE_RATE_LIMIT = rate_limit(max_requests=10, window_seconds=60, key_prefix="news_analyze")
# Content: 30 requests per minute
CONTENT_RATE_LIMIT = rate_limit(max_requests=30, window_seconds=60, key_prefix="news_content")


async def news_analysis_stream_rate_limit(
    request: Request,
    last_event_id: str = Query("0-0", alias="lastEventId"),
    force_new: bool = Query(False, alias="forceNew"),
):
    """Only rate-limit new analysis requests, not SSE reconnections."""
    is_new_request = (last_event_id == "0-0") or force_new
    if is_new_request:
        limiter = rate_limit(max_requests=10, window_seconds=60, key_prefix="news_analysis_stream")
        await limiter(request)


@router.get(
    "/market",
    response_model=NewsFeedResponse,
    summary="Get market news",
    description="Get news articles from NewsForge, optionally filtered by market.",
)
async def get_market_news(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    market: Optional[str] = Query(
        None,
        description="Filter by market (US, HK, SH, SZ, MARKET)",
    ),
    filter_status: Optional[str] = Query(
        None,
        description="Filter by status (keep, useful, uncertain, delete)",
    ),
    content_status: Optional[str] = Query(
        None,
        description="Filter by content status (embedded, fetched, failed, deleted, blocked, pending)",
    ),
    show_all: bool = Query(
        False,
        description="Show all articles including deleted/failed (admin view)",
    ),
    search: Optional[str] = Query(
        None,
        max_length=100,
        description="Keyword search on article title",
    ),
    sentiment_tag: Optional[str] = Query(
        None,
        pattern=r"^(bullish|bearish|neutral)$",
        description="Filter by sentiment tag",
    ),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(FEED_RATE_LIMIT),
):
    """Get market news from NewsForge."""
    try:
        proxy = NewsForgeProxy()
        return await proxy.get_market_news(
            page=page,
            page_size=page_size,
            market=market,
            search=search,
            sentiment_tag=sentiment_tag,
            filter_status=filter_status,
            content_status=content_status,
            show_all=show_all,
        )
    except Exception:
        logger.exception("NewsForge proxy failed for /news/market")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="News service unavailable",
        )


@router.get(
    "/trending",
    response_model=TrendingNewsResponse,
    summary="Get trending news",
    description="Get hot/trending market news, optionally filtered by market.",
)
async def get_trending_news(
    market: Optional[str] = Query(
        None,
        description="Filter by market (US, HK, SH, SZ)",
        regex="^(US|HK|SH|SZ)$",
    ),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(FEED_RATE_LIMIT),
):
    """Get trending/hot market news from NewsForge."""
    try:
        proxy = NewsForgeProxy()
        return await proxy.get_trending(market=market)
    except Exception:
        logger.exception("NewsForge proxy failed for /news/trending")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="News service unavailable",
        )


@router.get(
    "/feed",
    response_model=NewsFeedResponse,
    summary="Get user's news feed",
    description="Get aggregated news for stocks in user's watchlists.",
)
async def get_news_feed(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(
        None,
        max_length=100,
        description="Keyword search on article title",
    ),
    sentiment_tag: Optional[str] = Query(
        None,
        pattern=r"^(bullish|bearish|neutral)$",
        description="Filter by sentiment tag",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(FEED_RATE_LIMIT),
):
    """
    Get personalized news feed based on user's watchlist stocks.

    Fetches symbols from the user's watchlists, then queries NewsForge
    for matching articles.
    """
    # Fetch watchlist symbols from local DB
    symbol_query = (
        select(WatchlistItem.symbol)
        .join(Watchlist)
        .where(Watchlist.user_id == current_user.id)
        .distinct()
    )
    result = await db.execute(symbol_query)
    symbols = [row[0] for row in result.fetchall()]

    if not symbols:
        return NewsFeedResponse(
            news=[], total=0, page=page, page_size=page_size, has_more=False,
        )

    try:
        proxy = NewsForgeProxy()
        return await proxy.get_feed(
            symbols=symbols,
            page=page,
            page_size=page_size,
            search=search,
            sentiment_tag=sentiment_tag,
        )
    except Exception:
        logger.exception("NewsForge proxy failed for /news/feed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="News service unavailable",
        )


@router.get(
    "/{symbol}/sentiment-timeline",
    response_model=SentimentTimelineResponse,
    summary="Get sentiment timeline for a stock",
    description="Get daily aggregated sentiment scores for a stock.",
)
async def get_sentiment_timeline(
    symbol: str,
    days: int = Query(30, ge=7, le=90, description="Number of days to look back"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(SYMBOL_NEWS_RATE_LIMIT),
):
    """Get sentiment timeline for a specific stock from NewsForge."""
    try:
        proxy = NewsForgeProxy()
        return await proxy.get_sentiment_timeline(
            symbol=symbol.strip().upper(), days=days,
        )
    except Exception:
        logger.exception(
            "NewsForge proxy failed for /%s/sentiment-timeline", symbol,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="News service unavailable",
        )


@router.get(
    "/article/{news_id}",
    response_model=NewsResponse,
    summary="Get a single news article",
    description="Get a news article by its ID, including all content tiers.",
)
async def get_news_article(
    news_id: str,
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(CONTENT_RATE_LIMIT),
):
    """Get a single news article by ID from NewsForge."""
    try:
        proxy = NewsForgeProxy()
        return await proxy.get_article(news_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="News article not found",
            )
        logger.exception("NewsForge proxy failed for /news/article/%s", news_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="News service unavailable",
        )
    except Exception:
        logger.exception("NewsForge proxy failed for /news/article/%s", news_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="News service unavailable",
        )


@router.get(
    "/article/{news_id}/stream/analysis",
    summary="Stream deep analysis for a news article",
    description="Generate or retrieve a deep AI analysis report via SSE streaming.",
)
async def stream_news_analysis(
    news_id: str,
    last_event_id: str = Query("0-0", alias="lastEventId"),
    force_new: bool = Query(False, alias="forceNew"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(news_analysis_stream_rate_limit),
):
    """
    Stream deep analysis for a news article via NewsForge.

    Query params:
    - `lastEventId`: SSE stream ID to resume from (default "0-0")
    - `forceNew`: Force regeneration even if cached

    Rate limit: 10/min per user. SSE reconnections (lastEventId != "0-0")
    bypass rate limit via news_analysis_stream_rate_limit dependency.
    """
    try:
        proxy = NewsForgeProxy()
        upstream_resp = await proxy.stream_analysis(news_id)

        async def _proxy_sse():
            try:
                async for chunk in upstream_resp.aiter_bytes():
                    yield chunk
            finally:
                await upstream_resp.aclose()

        return StreamingResponse(
            _proxy_sse(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception:
        logger.exception(
            "NewsForge proxy failed for /news/article/%s/stream/analysis",
            news_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="News service unavailable",
        )


@router.get(
    "/{symbol}",
    response_model=List[NewsResponse],
    summary="Get news for a stock",
    description="Get recent news articles for a specific stock symbol.",
)
async def get_stock_news(
    symbol: str,
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(SYMBOL_NEWS_RATE_LIMIT),
):
    """Get news for a specific stock from NewsForge."""
    try:
        proxy = NewsForgeProxy()
        return await proxy.get_symbol_news(symbol=symbol.strip().upper())
    except Exception:
        logger.exception("NewsForge proxy failed for /news/%s", symbol)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="News service unavailable",
        )


@router.post(
    "/analyze",
    response_model=NewsAnalysisResponse,
    summary="AI analyze news article",
    description="Get AI analysis of a news article's impact on stock price.",
)
async def analyze_news(
    data: NewsAnalysisRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _rate_limit: None = Depends(ANALYZE_RATE_LIMIT),
):
    """
    Get AI analysis for a news article.

    Accepts news content directly in the request body for analysis.
    Uses user's configured OpenAI API key if available, otherwise falls back to system config.

    Returns sentiment score, impact prediction, and key points.
    """
    from app.prompts.analysis.news_prompt import (
        build_news_analysis_prompt,
        get_news_analysis_system_prompt,
    )
    from app.core.llm import get_llm_gateway, ChatRequest, Message, Role
    from app.services.settings_service import get_settings_service

    # Get resolved AI configuration using SettingsService
    # Priority: user settings (if permitted) > system settings > env variables
    settings_service = get_settings_service()
    ai_config = await settings_service.get_user_ai_config(db, current_user.id)

    model = ai_config.model or "gpt-4o-mini"

    # Check if we have an API key
    if not ai_config.api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI analysis is not available. Please configure your OpenAI API key in Settings.",
        )

    try:
        gateway = get_llm_gateway()

        # Determine language (from request, default to "en")
        language = data.language or "en"
        if language not in ("en", "zh"):
            language = "en"

        # Build prompt from request data
        system_prompt = get_news_analysis_system_prompt(language=language)
        user_prompt = build_news_analysis_prompt(
            symbol=data.symbol,
            title=data.title,
            summary=data.summary or "",
            source=data.source or "unknown",
            published_at=data.published_at.isoformat() if data.published_at else datetime.now(timezone.utc).isoformat(),
            market=data.market or "US",
            language=language,
        )

        # Don't pass max_tokens/temperature - let API use defaults
        # This ensures compatibility with reasoning models (o1, gpt-5, etc.)
        chat_request = ChatRequest(
            model=model,
            messages=[
                Message(role=Role.SYSTEM, content=system_prompt),
                Message(role=Role.USER, content=user_prompt),
            ],
        )
        response = await gateway.chat(
            chat_request,
            system_api_key=ai_config.api_key,
            system_base_url=ai_config.base_url,
        )

        content = response.content or ""

        # Parse JSON from response
        try:
            # Try to find JSON in the response
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                analysis = json.loads(content[start:end])
            else:
                raise ValueError("No JSON found in response")
        except (json.JSONDecodeError, ValueError):
            # Return default analysis if parsing fails
            analysis = {
                "sentiment_score": 0.0,
                "sentiment_label": "neutral",
                "impact_prediction": {
                    "direction": "neutral",
                    "magnitude": "low",
                },
                "key_points": ["Analysis parsing failed"],
                "summary": content[:500] if content else "Analysis unavailable",
            }

        return NewsAnalysisResponse(
            news_id=data.news_id or "generated",
            sentiment_score=float(analysis.get("sentiment_score", 0)),
            sentiment_label=analysis.get("sentiment_label", "neutral"),
            impact_prediction=json.dumps(analysis.get("impact_prediction", {})),
            key_points=analysis.get("key_points", []),
            summary=analysis.get("summary", ""),
            analyzed_at=datetime.now(timezone.utc),
        )

    except Exception as e:
        logger.exception(f"News analysis error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to analyze news article",
        )
