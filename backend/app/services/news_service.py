"""Multi-source news aggregation service with caching and fallback support.

News fetching is delegated to the data-service microservice via
DataServiceClient.  This module handles caching, format adaptation,
and the public API consumed by routers and skills.
"""

import asyncio
import hashlib
import html
import json
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, TypedDict

from app.config import settings

if TYPE_CHECKING:
    from app.models.user import User

logger = logging.getLogger(__name__)

# Cache TTL: 30 min base + random 0-5 min to prevent thundering herd
NEWS_CACHE_BASE_TTL = 1800  # 30 minutes
NEWS_CACHE_RAND_TTL = 300   # 5 minutes

# HTML tag pattern for stripping
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def sanitize_html(text: Optional[str]) -> Optional[str]:
    """
    Sanitize HTML from text to prevent XSS attacks.

    Strips HTML tags and escapes special characters.

    Args:
        text: Input text that may contain HTML

    Returns:
        Sanitized text with HTML removed and special chars escaped
    """
    if text is None:
        return None

    # First strip all HTML tags
    text = HTML_TAG_PATTERN.sub("", text)

    # Then escape any remaining special characters
    text = html.escape(text)

    # Clean up excessive whitespace
    text = " ".join(text.split())

    return text.strip() if text else None


class NewsSource(str, Enum):
    """News source providers."""

    FINNHUB = "finnhub"
    AKSHARE = "akshare"
    EASTMONEY = "eastmoney"
    YFINANCE = "yfinance"


class Market(str, Enum):
    """Stock market identifiers."""

    US = "US"
    HK = "HK"
    SH = "SH"
    SZ = "SZ"
    METAL = "METAL"


class RelatedEntity(TypedDict):
    """Related entity extracted from news (stock/index/macro factor)."""

    entity: str  # Entity identifier (ticker, index name, or macro factor)
    type: Literal["stock", "index", "macro"]  # Entity type
    score: float  # Relevance score 0.0-1.0


@dataclass
class NewsArticle:
    """News article data structure."""

    id: str  # Unique ID derived from URL hash
    symbol: str
    title: str
    summary: Optional[str]
    source: str
    url: str
    published_at: datetime
    market: str
    sentiment_score: Optional[float] = None
    ai_analysis: Optional[str] = None
    related_entities: Optional[List[RelatedEntity]] = field(default=None)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "id": self.id,
            "symbol": self.symbol,
            "title": self.title,
            "summary": self.summary,
            "source": self.source,
            "url": self.url,
            "publishedAt": self.published_at.isoformat(),
            "market": self.market,
            "sentimentScore": self.sentiment_score,
            "aiAnalysis": self.ai_analysis,
        }
        if self.related_entities is not None:
            result["relatedEntities"] = self.related_entities
        return result


def detect_market(symbol: str) -> str:
    """Detect market from symbol format."""
    import re
    symbol = symbol.upper()
    # Check precious metals first (GC=F, SI=F, PL=F, PA=F)
    if re.match(r"^(GC|SI|PL|PA)=F$", symbol):
        return Market.METAL.value
    if symbol.endswith(".HK"):
        return Market.HK.value
    elif symbol.endswith(".SS"):
        return Market.SH.value
    elif symbol.endswith(".SZ"):
        return Market.SZ.value
    else:
        return Market.US.value


def generate_news_id(url: str) -> str:
    """Generate deterministic ID from URL."""
    return hashlib.md5(url.encode()).hexdigest()


async def extract_related_entities(
    db,  # AsyncSession
    articles: List[Dict[str, Any]],
    batch_size: int = 10,
) -> Dict[str, List[RelatedEntity]]:
    """
    Batch extract related entities (stocks/indices/macro factors) from news articles.

    Uses the system's news_filter_model configuration for LLM extraction.

    Args:
        db: Database session for accessing system settings
        articles: List of news articles, each with 'url', 'headline', 'summary'
        batch_size: Number of articles to process per LLM call

    Returns:
        Mapping of URL to list of RelatedEntity with scores
    """
    from app.core.llm import get_llm_gateway, ChatRequest, Message, Role
    from app.services.news_layer3_analysis_service import get_news_llm_settings

    # Use unified provider system for news filter LLM config
    try:
        llm_settings = await get_news_llm_settings(db)
    except ValueError:
        logger.warning("No API key configured for news filtering, skipping entity extraction")
        return {a.get("url", ""): [] for a in articles}

    api_key = llm_settings.api_key
    base_url = llm_settings.base_url
    model = llm_settings.model

    gateway = get_llm_gateway()
    results: Dict[str, List[RelatedEntity]] = {}

    # Process in batches to reduce API calls
    for i in range(0, len(articles), batch_size):
        batch = articles[i : i + batch_size]

        news_text = "\n\n".join(
            [
                f"[{j+1}] {a.get('headline', '')}\n{a.get('summary', '')}"
                for j, a in enumerate(batch)
            ]
        )

        prompt = f"""分析以下新闻，提取相关的金融实体及影响评分。

实体类型:
- stock: 个股代码 (AAPL, TSLA, NVDA, 0700.HK, 600519.SS 等)
- index: 大盘指数 (SP500, NASDAQ, DOW, 上证, 恒指, 纳指 等)
- macro: 宏观/地缘因素 (Fed利率, 通胀, 中美关系, 石油价格, 就业数据 等)

评分标准 (0.0-1.0):
- 0.9-1.0: 新闻主要讨论此实体
- 0.6-0.8: 高度相关（直接影响）
- 0.3-0.5: 有一定关联（间接影响）
- 0.1-0.2: 仅顺带提及

返回 JSON 格式:
{{"1": [{{"entity": "AAPL", "type": "stock", "score": 0.95}}, {{"entity": "Fed利率", "type": "macro", "score": 0.7}}], "2": [{{"entity": "SP500", "type": "index", "score": 0.85}}]}}

注意:
- 每条新闻最多提取 6 个实体
- 没有相关实体返回空数组
- 优先提取高相关性实体

新闻:
{news_text}"""

        try:
            chat_request = ChatRequest(
                model=model,
                messages=[Message(role=Role.USER, content=prompt)],
                temperature=0,
                max_tokens=1500,
            )
            response = await gateway.chat(
                chat_request,
                system_api_key=api_key,
                system_base_url=base_url,
                use_user_config=False,
            )

            content = response.content or ""
            start = content.find("{")
            end = content.rfind("}") + 1

            if start >= 0 and end > start:
                parsed = json.loads(content[start:end])
                for j, a in enumerate(batch):
                    entities = parsed.get(str(j + 1), [])
                    validated: List[RelatedEntity] = []
                    for e in entities:
                        if isinstance(e, dict) and all(
                            k in e for k in ["entity", "type", "score"]
                        ):
                            if e["type"] in ("stock", "index", "macro"):
                                validated.append(
                                    {
                                        "entity": str(e["entity"]),
                                        "type": e["type"],
                                        "score": max(0.0, min(1.0, float(e["score"]))),
                                    }
                                )
                    # Sort by score descending
                    validated.sort(key=lambda x: x["score"], reverse=True)
                    results[a.get("url", "")] = validated
            else:
                # No valid JSON found
                for a in batch:
                    results[a.get("url", "")] = []

        except json.JSONDecodeError as e:
            logger.warning("Failed to parse LLM response as JSON: %s", e)
            for a in batch:
                results[a.get("url", "")] = []
        except Exception as e:
            logger.warning("LLM entity extraction failed: %s", e)
            for a in batch:
                results[a.get("url", "")] = []

    return results


class NewsService:
    """
    Multi-source news aggregation service.

    All data fetching is delegated to the data-service microservice
    via DataServiceClient.  This class handles Redis caching, format
    adaptation, and the public API.
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client

    async def _get_redis(self):
        """Get Redis client for caching."""
        if self._redis is None:
            from app.db.redis import get_redis
            self._redis = await get_redis()
        return self._redis

    def _get_cache_key(self, key_type: str, identifier: str) -> str:
        """Build cache key."""
        return f"news:{key_type}:{identifier}"

    def _get_cache_ttl(self) -> int:
        """Get TTL with randomization to prevent thundering herd."""
        return NEWS_CACHE_BASE_TTL + random.randint(0, NEWS_CACHE_RAND_TTL)

    async def _get_cached(self, key: str) -> Optional[List[Dict]]:
        """Get cached news data."""
        try:
            redis = await self._get_redis()
            data = await redis.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning(f"Cache get error: {e}")
        return None

    async def _set_cached(self, key: str, data: List[Dict]) -> None:
        """Set cached news data."""
        try:
            redis = await self._get_redis()
            await redis.setex(key, self._get_cache_ttl(), json.dumps(data, default=str))
        except Exception as e:
            logger.warning(f"Cache set error: {e}")

    @staticmethod
    def _adapt_articles(articles: List[Dict[str, Any]], default_symbol: str = "") -> List[Dict[str, Any]]:
        """Convert data-service news format to backend camelCase format."""
        adapted = []
        for a in articles:
            article_id = a.get("id", "")
            url = a.get("url", "")
            if not article_id or not url:
                logger.warning("Skipping article with missing id/url: %s", a.get("title", "")[:80])
                continue
            adapted.append({
                "id": article_id,
                "symbol": a.get("symbol", default_symbol),
                "title": a.get("title", ""),
                "summary": a.get("summary"),
                "source": a.get("source", "unknown"),
                "url": url,
                "publishedAt": a.get("published_at") or a.get("publishedAt", ""),
                "market": (a.get("market") or "US").upper(),
                "sentimentScore": None,
                "aiAnalysis": None,
            })
        return adapted

    async def get_news_by_symbol(
        self,
        symbol: str,
        force_refresh: bool = False,
        user: Optional["User"] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get news for a specific stock symbol via data-service.

        Args:
            symbol: Stock symbol
            force_refresh: Skip cache and fetch fresh data
            user: Optional user for accessing user-specific settings

        Returns:
            List of news articles as dictionaries
        """
        cache_key = self._get_cache_key("symbol", symbol.upper())

        # Check cache first
        if not force_refresh:
            cached = await self._get_cached(cache_key)
            if cached:
                logger.debug(f"Cache hit for news: {symbol}")
                return cached
            logger.debug(f"Cache miss for news: {symbol}, fetching from data-service")

        # Determine source preference from user settings
        source = "auto"
        if user and user.settings:
            news_source = getattr(user.settings, "news_source", None)
            if news_source:
                source = news_source

        # Fetch from data-service
        try:
            from app.services.data_service_client import get_data_service_client
            data_client = await get_data_service_client()
            raw = await data_client.get_company_news(symbol, source=source)
        except Exception as e:
            logger.error(f"Data-service news fetch failed for {symbol}: {e}")
            raw = None

        if not raw:
            logger.debug(f"No news returned from data-service for {symbol}")
            return []

        result = self._adapt_articles(raw, default_symbol=symbol)

        # Cache results
        if result:
            await self._set_cached(cache_key, result)

        logger.info(f"Fetched {len(result)} news articles via data-service for {symbol}")
        return result

    async def get_trending_news(
        self,
        market: Optional[str] = None,
        force_refresh: bool = False,
        user: Optional["User"] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get trending/hot news, optionally filtered by market.

        Args:
            market: Market filter (US, HK, SH, SZ) or None for all
            force_refresh: Skip cache and fetch fresh data
            user: Optional user for accessing user-specific settings

        Returns:
            List of trending news articles
        """
        # Get user's news source preference (defensive access for news_source column)
        news_source = "auto"
        if user and user.settings:
            news_source = getattr(user.settings, 'news_source', None) or "auto"

        # Build cache key including user's news source preference
        cache_suffix = f"{market or 'all'}:{news_source}"
        cache_key = self._get_cache_key("trending", cache_suffix)

        # Check cache first
        if not force_refresh:
            cached = await self._get_cached(cache_key)
            if cached:
                logger.debug(f"Cache hit for trending news: {cache_suffix}")
                return cached

        from app.services.data_service_client import get_data_service_client

        try:
            data_client = await get_data_service_client()
        except Exception as e:
            logger.error(f"Failed to get data-service client for trending news: {e}")
            return []

        all_articles: List[Dict[str, Any]] = []

        # Determine which markets to fetch based on news_source preference
        # auto = US + CN (fetch both)
        # yfinance/finnhub = US-focused sources, only show US news
        # akshare = Chinese A-share news from Eastmoney only
        if news_source == "auto":
            # Auto mode: fetch both US and CN news
            if market is None or market == Market.US.value:
                try:
                    us_raw = await data_client.get_general_news(category="general")
                    if us_raw:
                        all_articles.extend(self._adapt_articles(us_raw, default_symbol="MARKET"))
                except Exception as e:
                    logger.warning(f"Trending US news fetch failed: {e}")

            if market is None or market in (Market.SH.value, Market.SZ.value):
                try:
                    cn_raw = await data_client.get_trending_cn_news()
                    if cn_raw:
                        all_articles.extend(self._adapt_articles(cn_raw, default_symbol="MARKET"))
                except Exception as e:
                    logger.warning(f"Trending CN news fetch failed: {e}")

        elif news_source == "akshare":
            # Only fetch Chinese A-share market news
            if market is None or market in (Market.SH.value, Market.SZ.value):
                try:
                    cn_raw = await data_client.get_trending_cn_news()
                    if cn_raw:
                        all_articles.extend(self._adapt_articles(cn_raw, default_symbol="MARKET"))
                except Exception as e:
                    logger.warning(f"Trending CN news fetch failed: {e}")

        else:
            # yfinance or finnhub: fetch US market news only
            if market is None or market == Market.US.value:
                try:
                    us_raw = await data_client.get_general_news(category="general")
                    if us_raw:
                        all_articles.extend(self._adapt_articles(us_raw, default_symbol="MARKET"))
                except Exception as e:
                    logger.warning(f"Trending US news fetch failed: {e}")

        # Sort by published date, newest first
        all_articles.sort(
            key=lambda x: x.get("publishedAt", ""),
            reverse=True,
        )

        # Limit results
        result = all_articles[:50]

        if result:
            await self._set_cached(cache_key, result)

        logger.info(f"Fetched {len(result)} trending news articles via data-service")
        return result

    async def get_news_feed(
        self,
        symbols: List[str],
        page: int = 1,
        page_size: int = 20,
        user: Optional["User"] = None,
    ) -> Dict[str, Any]:
        """
        Get aggregated news feed for multiple symbols (e.g., user's watchlist).

        Args:
            symbols: List of stock symbols
            page: Page number (1-indexed)
            page_size: Number of items per page
            user: Optional user for accessing user-specific API keys

        Returns:
            Paginated news feed
        """
        if not symbols:
            return {
                "news": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "has_more": False,
            }

        # Fetch news for all symbols concurrently
        tasks = [self.get_news_by_symbol(symbol, user=user) for symbol in symbols[:20]]  # Limit to 20 symbols
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate and deduplicate
        all_news: Dict[str, Dict] = {}
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Error fetching news: {result}")
                continue
            for article in result:
                news_id = article.get("id")
                if news_id and news_id not in all_news:
                    all_news[news_id] = article

        # Sort by published date (camelCase key from _adapt_articles)
        sorted_news = sorted(
            all_news.values(),
            key=lambda x: x.get("publishedAt", ""),
            reverse=True,
        )

        # Paginate
        total = len(sorted_news)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_news = sorted_news[start_idx:end_idx]

        return {
            "news": page_news,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": end_idx < total,
        }


# Singleton instance
_news_service: Optional[NewsService] = None
_news_service_lock = asyncio.Lock()


async def get_news_service() -> NewsService:
    """Get singleton news service instance."""
    global _news_service
    if _news_service is None:
        async with _news_service_lock:
            if _news_service is None:
                _news_service = NewsService()
    return _news_service


async def cleanup_news_service() -> None:
    """Cleanup news service resources."""
    global _news_service
    _news_service = None
