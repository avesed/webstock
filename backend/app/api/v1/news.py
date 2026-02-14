"""News API endpoints."""

import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user
from app.db.database import get_db
from app.models.news import News, NewsAlert
from app.models.user import User
from app.models.user_settings import UserSettings
from app.models.watchlist import Watchlist, WatchlistItem
from app.schemas.news import (
    BatchFetchRequest,
    BatchFetchResponse,
    MessageResponse,
    NewsAlertCreate,
    NewsAlertListResponse,
    NewsAlertResponse,
    NewsAlertUpdate,
    NewsAnalysisRequest,
    NewsAnalysisResponse,
    NewsFeedResponse,
    NewsFullContentResponse,
    NewsResponse,
    SentimentTimelineItemResponse,
    SentimentTimelineResponse,
    TrendingNewsResponse,
)
from app.services.news_service import get_news_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/news", tags=["News"])


def _news_to_response(article: News) -> NewsResponse:
    """Convert a News DB model to NewsResponse schema."""
    return NewsResponse(
        id=str(article.id),
        symbol=article.symbol,
        title=article.title,
        summary=article.investment_summary or article.summary,
        source=article.source,
        url=article.url,
        published_at=article.published_at,
        market=article.market,
        sentiment_score=article.sentiment_score,
        sentiment_tag=article.sentiment_tag,
        ai_analysis=article.ai_analysis,
        related_entities=article.related_entities,
        industry_tags=article.industry_tags,
        event_tags=article.event_tags,
        content_score=article.content_score,
        processing_path=article.processing_path,
        score_details=article.score_details,
        content_status=article.content_status,
        filter_status=article.filter_status,
        created_at=article.created_at,
    )

# Rate limiting configurations for different endpoints
# Symbol news: 100 requests per minute
SYMBOL_NEWS_RATE_LIMIT = rate_limit(max_requests=100, window_seconds=60, key_prefix="news_symbol")
# Feed/Trending: 30 requests per minute
FEED_RATE_LIMIT = rate_limit(max_requests=30, window_seconds=60, key_prefix="news_feed")
# Analyze: 10 requests per minute (uses AI)
ANALYZE_RATE_LIMIT = rate_limit(max_requests=10, window_seconds=60, key_prefix="news_analyze")
# Alerts CRUD: 60 requests per minute
ALERTS_RATE_LIMIT = rate_limit(max_requests=60, window_seconds=60, key_prefix="news_alerts")
# Full content: 30 requests per minute
CONTENT_RATE_LIMIT = rate_limit(max_requests=30, window_seconds=60, key_prefix="news_content")


@router.get(
    "/market",
    response_model=NewsFeedResponse,
    summary="Get market news from database",
    description="Get news articles stored and filtered by the news monitor pipeline.",
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(FEED_RATE_LIMIT),
):
    """
    Get market news from the database (populated by news_monitor pipeline).

    Returns articles that have been fetched, filtered, and stored by the
    automatic news monitoring system. Unlike /feed and /trending which
    call external APIs in real-time, this endpoint reads from the local database.

    Use show_all=true to include deleted/failed/blocked articles (for admin view).
    """
    from sqlalchemy import desc

    # Base query: filter by content status
    if show_all:
        # Admin view: include all statuses except pending (not yet processed)
        query = select(News).where(
            News.content_status.notin_(["pending"]),
        )
    elif content_status:
        query = select(News).where(
            News.content_status == content_status,
        )
    else:
        # Default: only show successfully processed articles
        query = select(News).where(
            News.content_status.in_(["fetched", "embedded", "partial"]),
        )

    # Optional market filter
    if market:
        query = query.where(News.market == market)

    # Optional filter_status filter
    if filter_status:
        query = query.where(News.filter_status == filter_status)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate, ordered by published_at desc
    query = query.order_by(desc(News.published_at))
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    articles = result.scalars().all()

    # Convert to response format
    news_list = []
    for article in articles:
        news_list.append(_news_to_response(article))

    return NewsFeedResponse(
        news=news_list,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(page * page_size) < total,
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(FEED_RATE_LIMIT),
):
    """
    Get trending/hot market news.

    - **market**: Optional market filter (US, HK, SH, SZ)
    """
    # Load user with settings
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(User).where(User.id == current_user.id).options(selectinload(User.settings))
    )
    user = result.scalar_one_or_none()
    
    news_service = await get_news_service()
    news = await news_service.get_trending_news(market=market, user=user)

    return TrendingNewsResponse(
        news=[NewsResponse(**n) for n in news],
        market=market,
        fetched_at=datetime.now(timezone.utc),
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(FEED_RATE_LIMIT),
):
    """
    Get personalized news feed based on user's watchlist stocks.

    Reads from the database (populated by the news monitoring pipeline).
    Matches articles where:
    - symbol matches a watchlist stock, OR
    - related_entities contains a watchlist stock ticker (for MARKET news)

    - **page**: Page number (1-indexed)
    - **page_size**: Number of items per page (max 100)
    """
    from sqlalchemy import desc, or_, cast, literal
    from sqlalchemy.dialects.postgresql import JSONB

    # Get all symbols from user's watchlists
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
            news=[],
            total=0,
            page=page,
            page_size=page_size,
            has_more=False,
        )

    # Build condition to match related_entities containing any watchlist symbol
    # Both sides of @> must be JSONB, so cast the column and the literal value
    entities_jsonb = cast(News.related_entities, JSONB)
    entity_conditions = []
    for sym in symbols:
        pattern = cast(literal(f'[{{"entity": "{sym}"}}]'), JSONB)
        entity_conditions.append(entities_jsonb.op("@>")(pattern))

    # Match by: direct symbol match OR related_entities contains a watchlist symbol
    query = select(News).where(
        News.content_status.in_(["fetched", "embedded", "partial"]),
        or_(
            News.symbol.in_(symbols),
            *entity_conditions,
        ),
    )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate, ordered by published_at desc
    query = query.order_by(desc(News.published_at))
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    articles = result.scalars().all()

    # Convert to response format
    news_list = []
    for article in articles:
        news_list.append(_news_to_response(article))

    return NewsFeedResponse(
        news=news_list,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(page * page_size) < total,
    )


@router.get(
    "/alerts",
    response_model=NewsAlertListResponse,
    summary="Get user's news alerts",
    description="Get all news alerts configured by the user.",
)
async def get_news_alerts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(ALERTS_RATE_LIMIT),
):
    """
    Get all news alerts for the current user.
    """
    query = (
        select(NewsAlert)
        .where(NewsAlert.user_id == current_user.id)
        .order_by(NewsAlert.created_at.desc())
    )
    result = await db.execute(query)
    alerts = result.scalars().all()

    return NewsAlertListResponse(
        alerts=[NewsAlertResponse.model_validate(a) for a in alerts],
        total=len(alerts),
    )


@router.post(
    "/alerts",
    response_model=NewsAlertResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create news alert",
    description="Create a new news alert for keyword monitoring.",
)
async def create_news_alert(
    data: NewsAlertCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(ALERTS_RATE_LIMIT),
):
    """
    Create a new news alert.

    - **symbol**: Stock symbol (optional, None means all watchlist stocks)
    - **keywords**: List of keywords to monitor
    - **is_active**: Whether the alert is active
    """
    # Validate keywords
    if not data.keywords or len(data.keywords) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one keyword is required",
        )

    # Check alert limit (max 20 per user)
    count_query = select(func.count(NewsAlert.id)).where(
        NewsAlert.user_id == current_user.id
    )
    result = await db.execute(count_query)
    existing_count = result.scalar()

    if existing_count >= 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum number of news alerts (20) reached",
        )

    alert = NewsAlert(
        user_id=current_user.id,
        symbol=data.symbol.upper() if data.symbol else None,
        keywords=[k.lower().strip() for k in data.keywords],
        is_active=data.is_active,
    )

    db.add(alert)
    await db.commit()
    await db.refresh(alert)

    logger.info(f"Created news alert {alert.id} for user {current_user.id}")

    return NewsAlertResponse.model_validate(alert)


@router.put(
    "/alerts/{alert_id}",
    response_model=NewsAlertResponse,
    summary="Update news alert",
    description="Update an existing news alert.",
)
async def update_news_alert(
    alert_id: UUID,
    data: NewsAlertUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(ALERTS_RATE_LIMIT),
):
    """
    Update a news alert.

    - **alert_id**: ID of the alert to update
    - **symbol**: New stock symbol (optional)
    - **keywords**: New keywords (optional)
    - **is_active**: New active status (optional)
    """
    # Get alert
    query = select(NewsAlert).where(
        NewsAlert.id == alert_id,
        NewsAlert.user_id == current_user.id,
    )
    result = await db.execute(query)
    alert = result.scalar_one_or_none()

    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="News alert not found",
        )

    # Update fields
    if data.symbol is not None:
        alert.symbol = data.symbol.upper() if data.symbol else None
    if data.keywords is not None:
        if len(data.keywords) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one keyword is required",
            )
        alert.keywords = [k.lower().strip() for k in data.keywords]
    if data.is_active is not None:
        alert.is_active = data.is_active

    await db.commit()
    await db.refresh(alert)

    logger.info(f"Updated news alert {alert_id}")

    return NewsAlertResponse.model_validate(alert)


@router.delete(
    "/alerts/{alert_id}",
    response_model=MessageResponse,
    summary="Delete news alert",
    description="Delete a news alert.",
)
async def delete_news_alert(
    alert_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(ALERTS_RATE_LIMIT),
):
    """
    Delete a news alert.

    - **alert_id**: ID of the alert to delete
    """
    # Delete alert
    delete_query = delete(NewsAlert).where(
        NewsAlert.id == alert_id,
        NewsAlert.user_id == current_user.id,
    )
    result = await db.execute(delete_query)

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="News alert not found",
        )

    await db.commit()

    logger.info(f"Deleted news alert {alert_id}")

    return MessageResponse(message="News alert deleted successfully")


@router.get(
    "/{symbol}/sentiment-timeline",
    response_model=SentimentTimelineResponse,
    summary="Get sentiment timeline for a stock",
    description="Get daily aggregated sentiment scores for a stock based on filtered news articles.",
)
async def get_sentiment_timeline(
    symbol: str,
    days: int = Query(30, ge=7, le=90, description="Number of days to look back"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(SYMBOL_NEWS_RATE_LIMIT),
):
    """
    Get sentiment timeline for a specific stock.

    Aggregates daily sentiment from news articles that passed the filtering pipeline
    (filter_status='keep') and have a sentiment_tag assigned.

    - **symbol**: Stock symbol (e.g., AAPL, 0700.HK, 600519.SS)
    - **days**: Number of days to look back (7-90, default 30)
    """
    from datetime import timedelta
    from sqlalchemy import text

    symbol = symbol.strip().upper()

    # Auto-append exchange suffix for bare A-share codes
    if re.match(r"^[0-9]{6}$", symbol):
        if symbol.startswith(("600", "601", "603", "605", "688")):
            symbol = f"{symbol}.SS"
        elif symbol.startswith(("000", "001", "002", "003", "300", "301")):
            symbol = f"{symbol}.SZ"

    # Auto-append .HK for bare 4-5 digit codes
    if re.match(r"^[0-9]{4,5}$", symbol):
        symbol = f"{symbol}.HK"

    # Normalize HK symbols: 01810.HK → 1810.HK
    if symbol.endswith(".HK"):
        code = symbol[:-3]
        try:
            code = str(int(code)).zfill(4)
        except ValueError:
            pass
        symbol = f"{code}.HK"

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    query = text("""
        SELECT
            DATE(published_at) AS date,
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE sentiment_tag = 'bullish') AS bullish,
            COUNT(*) FILTER (WHERE sentiment_tag = 'bearish') AS bearish,
            COUNT(*) FILTER (WHERE sentiment_tag = 'neutral') AS neutral
        FROM news
        WHERE symbol = :symbol
            AND filter_status = 'keep'
            AND sentiment_tag IS NOT NULL
            AND published_at >= :cutoff
        GROUP BY DATE(published_at)
        ORDER BY date ASC
    """)

    result = await db.execute(query, {"symbol": symbol, "cutoff": cutoff})
    rows = result.fetchall()

    data = []
    for row in rows:
        total = row.total
        bullish = row.bullish
        bearish = row.bearish
        neutral = row.neutral
        score = (bullish - bearish) / total if total > 0 else 0.0
        data.append(
            SentimentTimelineItemResponse(
                date=str(row.date),
                bullish=bullish,
                bearish=bearish,
                neutral=neutral,
                total=total,
                score=round(score, 4),
            )
        )

    logger.info(
        "Sentiment timeline for %s: %d days, %d data points",
        symbol,
        days,
        len(data),
    )

    return SentimentTimelineResponse(
        symbol=symbol,
        days=days,
        data=data,
    )


@router.get(
    "/{symbol}",
    response_model=List[NewsResponse],
    summary="Get news for a stock",
    description="Get recent news articles for a specific stock symbol.",
)
async def get_stock_news(
    symbol: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(SYMBOL_NEWS_RATE_LIMIT),
):
    """
    Get news for a specific stock.

    - **symbol**: Stock symbol (e.g., AAPL, 0700.HK, 600519.SS)
    """
    symbol = symbol.strip().upper()

    # Auto-append exchange suffix for bare 6-digit A-share codes
    if re.match(r"^[0-9]{6}$", symbol):
        if symbol.startswith(("600", "601", "603", "605", "688")):
            symbol = f"{symbol}.SS"
        elif symbol.startswith(("000", "001", "002", "003", "300", "301")):
            symbol = f"{symbol}.SZ"

    # Auto-append .HK for bare 4-5 digit codes
    if re.match(r"^[0-9]{4,5}$", symbol):
        symbol = f"{symbol}.HK"

    # Normalize HK symbols: 01810.HK → 1810.HK (yfinance uses 4-digit codes)
    if symbol.endswith(".HK"):
        code = symbol[:-3]
        try:
            code = str(int(code)).zfill(4)
        except ValueError:
            pass
        symbol = f"{code}.HK"

    # Load user with settings
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(User).where(User.id == current_user.id).options(selectinload(User.settings))
    )
    user = result.scalar_one_or_none()

    news_service = await get_news_service()
    news = await news_service.get_news_by_symbol(symbol, user=user)

    return [NewsResponse(**n) for n in news]


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


@router.get(
    "/content/{news_id}",
    response_model=NewsFullContentResponse,
    summary="Get news full content",
    description="Get full content of a news article including scraped text.",
)
async def get_news_full_content(
    news_id: str,
    force_refresh: bool = Query(False, description="Force re-fetch content"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(CONTENT_RATE_LIMIT),
):
    """
    Get full content of a news article.

    - **news_id**: UUID of the news article
    - **force_refresh**: If True, re-fetch content even if already fetched
    """
    from app.models.news import ContentStatus
    from app.services.news_storage_service import get_news_storage_service

    try:
        news_uuid = UUID(news_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid news ID format",
        )

    # Get news record
    query = select(News).where(News.id == news_uuid)
    result = await db.execute(query)
    news = result.scalar_one_or_none()

    if not news:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="News article not found",
        )

    # Check if we need to trigger fetch
    is_fetching = False
    if force_refresh or news.content_status == ContentStatus.PENDING.value:
        # Trigger async fetch via Layer 1.5 (single article batch)
        try:
            from worker.tasks.full_content_tasks import batch_fetch_content
            batch_fetch_content.delay([{
                "news_id": str(news.id),
                "url": news.url,
                "market": news.market or "US",
                "symbol": news.symbol or "",
                "title": news.title or "",
                "summary": news.summary or "",
                "source": news.source or "",
            }])
            is_fetching = True
            logger.info("Triggered content fetch for news_id=%s", news_id)
        except Exception as e:
            logger.error("Failed to trigger content fetch: %s", e)

    # Get full content from storage
    full_content = None
    word_count = 0
    storage_service = get_news_storage_service()

    if news.content_file_path:
        content_data = storage_service.read_content(news.content_file_path)
        if content_data:
            full_content = content_data.get("full_text")
            word_count = content_data.get("word_count", 0)

    return NewsFullContentResponse(
        id=str(news.id),
        title=news.title,
        full_content=full_content,
        content_status=news.content_status,
        language=news.language,
        authors=news.authors,
        keywords=news.keywords,
        word_count=word_count,
        is_fetching=is_fetching,
        fetched_at=news.content_fetched_at,
        error=news.content_error,
    )


@router.post(
    "/batch-fetch-content",
    response_model=BatchFetchResponse,
    summary="Batch fetch news content",
    description="Trigger content fetching for multiple news articles.",
)
async def batch_fetch_content(
    data: BatchFetchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(CONTENT_RATE_LIMIT),
):
    """
    Batch trigger content fetching for multiple news articles.

    - **news_ids**: List of news article UUIDs (max 50)
    """
    from app.models.news import ContentStatus

    # Validate UUIDs
    valid_uuids = []
    for news_id in data.news_ids:
        try:
            valid_uuids.append(UUID(news_id))
        except ValueError:
            continue

    if not valid_uuids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid news IDs provided",
        )

    # Get news records
    query = select(News).where(
        News.id.in_(valid_uuids),
        News.content_status.in_([
            ContentStatus.PENDING.value,
            ContentStatus.FAILED.value,
        ]),
    )
    result = await db.execute(query)
    news_list = result.scalars().all()

    if not news_list:
        return BatchFetchResponse(
            queued=0,
            message="No news articles need content fetching",
        )

    # Build batch items
    batch_items = []
    for news in news_list:
        batch_items.append({
            "news_id": str(news.id),
            "url": news.url,
            "market": news.market,
            "symbol": news.symbol,
            "user_id": current_user.id,
        })

    # Dispatch batch task via Layer 1.5
    try:
        from worker.tasks.full_content_tasks import batch_fetch_content
        batch_fetch_content.delay(batch_items)
        logger.info(
            "Queued batch content fetch for %d news articles",
            len(batch_items),
        )
    except Exception as e:
        logger.error("Failed to queue batch fetch: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue batch fetch",
        )

    return BatchFetchResponse(
        queued=len(batch_items),
        message=f"Queued {len(batch_items)} news articles for content fetching",
    )
