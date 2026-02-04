"""Multi-source news aggregation service with caching and fallback support."""

import asyncio
import hashlib
import html
import json
import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from app.config import settings

if TYPE_CHECKING:
    from app.models.user import User

logger = logging.getLogger(__name__)

# Thread pool for synchronous library calls
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = asyncio.Lock()

# Timeout for external API calls (in seconds)
EXTERNAL_API_TIMEOUT = 30

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


async def _get_executor() -> ThreadPoolExecutor:
    """Get thread pool executor, initialize if needed."""
    global _executor
    if _executor is None:
        async with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=5)
    return _executor


async def run_in_executor(func: Callable, *args, **kwargs) -> Any:
    """Run synchronous function in thread pool with timeout."""
    loop = asyncio.get_running_loop()
    executor = await _get_executor()
    return await asyncio.wait_for(
        loop.run_in_executor(
            executor,
            lambda: func(*args, **kwargs),
        ),
        timeout=EXTERNAL_API_TIMEOUT,
    )


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

    def to_dict(self) -> Dict[str, Any]:
        return {
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


def detect_market(symbol: str) -> str:
    """Detect market from symbol format."""
    symbol = symbol.upper()
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


class FinnhubProvider:
    """Finnhub news provider for US stocks."""

    @staticmethod
    async def get_news(
        symbol: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        api_key: Optional[str] = None,
    ) -> List[NewsArticle]:
        """
        Fetch news from Finnhub API for US stocks.

        Args:
            symbol: US stock symbol (e.g., AAPL)
            from_date: Start date for news
            to_date: End date for news
            api_key: Optional user-provided API key. Falls back to settings.FINNHUB_API_KEY if not provided.

        Returns:
            List of NewsArticle objects
        """
        api_key_to_use = api_key or settings.FINNHUB_API_KEY
        if not api_key_to_use:
            logger.warning("FINNHUB_API_KEY not configured, skipping Finnhub news")
            return []

        try:
            import finnhub
            from finnhub.exceptions import FinnhubAPIException

            def fetch():
                client = finnhub.Client(api_key=api_key_to_use)

                # Default to last 7 days if not specified
                if from_date is None:
                    _from = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
                else:
                    _from = from_date.strftime("%Y-%m-%d")

                if to_date is None:
                    _to = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                else:
                    _to = to_date.strftime("%Y-%m-%d")

                return client.company_news(symbol, _from=_from, to=_to)

            news_data = await run_in_executor(fetch)

            articles = []
            for item in news_data[:50]:  # Limit to 50 articles
                try:
                    published = datetime.fromtimestamp(
                        item.get("datetime", 0),
                        tz=timezone.utc,
                    )
                    # Sanitize title and summary to prevent XSS
                    article = NewsArticle(
                        id=generate_news_id(item.get("url", "")),
                        symbol=symbol,
                        title=sanitize_html(item.get("headline", "")) or "",
                        summary=sanitize_html(item.get("summary", "")),
                        source=item.get("source", "finnhub"),
                        url=item.get("url", ""),
                        published_at=published,
                        market=Market.US.value,
                    )
                    articles.append(article)
                except Exception as e:
                    logger.warning(f"Error parsing Finnhub news item: {e}")
                    continue

            logger.info(f"Fetched {len(articles)} news articles from Finnhub for {symbol}")
            return articles

        except asyncio.TimeoutError:
            logger.error(f"Finnhub API timeout for {symbol} (>{EXTERNAL_API_TIMEOUT}s)")
            return []
        except Exception as e:
            # Handle specific error types
            error_str = str(e).lower()
            if "429" in error_str or "rate limit" in error_str:
                logger.warning(f"Finnhub rate limit exceeded for {symbol}. Consider implementing backoff.")
            elif "401" in error_str or "403" in error_str or "unauthorized" in error_str:
                logger.error(f"Finnhub authentication error for {symbol}: {e}")
            elif "timeout" in error_str:
                logger.error(f"Finnhub connection timeout for {symbol}")
            else:
                logger.error(f"Finnhub news error for {symbol}: {e}")
            return []

    @staticmethod
    async def get_general_news(
        category: str = "general",
        api_key: Optional[str] = None,
    ) -> List[NewsArticle]:
        """
        Fetch general market news from Finnhub.

        Args:
            category: News category (general, forex, crypto, merger)
            api_key: Optional user-provided API key. Falls back to settings.FINNHUB_API_KEY if not provided.

        Returns:
            List of NewsArticle objects
        """
        api_key_to_use = api_key or settings.FINNHUB_API_KEY
        if not api_key_to_use:
            logger.warning("FINNHUB_API_KEY not configured, skipping Finnhub news")
            return []

        try:
            import finnhub

            def fetch():
                client = finnhub.Client(api_key=api_key_to_use)
                return client.general_news(category, min_id=0)

            news_data = await run_in_executor(fetch)

            articles = []
            for item in news_data[:30]:  # Limit to 30 articles
                try:
                    published = datetime.fromtimestamp(
                        item.get("datetime", 0),
                        tz=timezone.utc,
                    )
                    # Sanitize title and summary to prevent XSS
                    article = NewsArticle(
                        id=generate_news_id(item.get("url", "")),
                        symbol="MARKET",  # General market news
                        title=sanitize_html(item.get("headline", "")) or "",
                        summary=sanitize_html(item.get("summary", "")),
                        source=item.get("source", "finnhub"),
                        url=item.get("url", ""),
                        published_at=published,
                        market=Market.US.value,
                    )
                    articles.append(article)
                except Exception as e:
                    logger.warning(f"Error parsing Finnhub general news item: {e}")
                    continue

            logger.info(f"Fetched {len(articles)} general news articles from Finnhub")
            return articles

        except asyncio.TimeoutError:
            logger.error(f"Finnhub general news API timeout (>{EXTERNAL_API_TIMEOUT}s)")
            return []
        except Exception as e:
            # Handle specific error types
            error_str = str(e).lower()
            if "429" in error_str or "rate limit" in error_str:
                logger.warning("Finnhub rate limit exceeded for general news. Consider implementing backoff.")
            elif "401" in error_str or "403" in error_str or "unauthorized" in error_str:
                logger.error(f"Finnhub authentication error for general news: {e}")
            else:
                logger.error(f"Finnhub general news error: {e}")
            return []


class YFinanceProvider:
    """YFinance news provider."""

    @staticmethod
    def _parse_news_items(
        news_data: list,
        symbol: str,
        news_count: int = 20,
    ) -> List[NewsArticle]:
        """Parse yfinance news items into NewsArticle objects."""
        market = detect_market(symbol)
        articles = []
        for item in news_data[:news_count]:
            try:
                # Parse published timestamp
                published_timestamp = item.get("published_at") or item.get("publishedAt") or item.get("datetime") or item.get("providerPublishTime")
                if published_timestamp:
                    if isinstance(published_timestamp, (int, float)):
                        published = datetime.fromtimestamp(published_timestamp, tz=timezone.utc)
                    else:
                        published = datetime.fromisoformat(published_timestamp.replace("Z", "+00:00"))
                else:
                    published = datetime.now(timezone.utc)

                url = item.get("link") or item.get("url") or ""
                source = item.get("publisher") or item.get("source") or "yfinance"
                title = item.get("title", "")
                summary = item.get("summary") or item.get("description") or item.get("content", "")

                article = NewsArticle(
                    id=generate_news_id(url or title),
                    symbol=symbol,
                    title=sanitize_html(title) or "",
                    summary=sanitize_html(summary) if summary else None,
                    source=source,
                    url=url,
                    published_at=published,
                    market=market,
                )
                articles.append(article)
            except Exception as e:
                logger.warning(f"Error parsing YFinance news item: {e}")
                continue
        return articles

    @staticmethod
    async def get_news(
        symbol: str,
        news_count: int = 20,
    ) -> List[NewsArticle]:
        """
        Fetch news from YFinance using Search API (best for US stocks).

        Args:
            symbol: Stock symbol (e.g., AAPL)
            news_count: Number of news articles to fetch (default 20)

        Returns:
            List of NewsArticle objects
        """
        try:
            import yfinance as yf

            def fetch():
                search = yf.Search(symbol, news_count=news_count)
                return search.news if hasattr(search, "news") else []

            news_data = await run_in_executor(fetch)
            articles = YFinanceProvider._parse_news_items(news_data, symbol, news_count)
            logger.info(f"Fetched {len(articles)} news articles from YFinance Search for {symbol}")
            return articles

        except asyncio.TimeoutError:
            logger.error(f"YFinance news API timeout for {symbol} (>{EXTERNAL_API_TIMEOUT}s)")
            return []
        except ImportError:
            logger.warning("yfinance not installed, skipping YFinance news")
            return []
        except Exception as e:
            error_str = str(e).lower()
            if "rate limit" in error_str or "429" in error_str:
                logger.warning(f"YFinance rate limit exceeded for {symbol}")
            elif "timeout" in error_str:
                logger.error(f"YFinance connection timeout for {symbol}")
            else:
                logger.error(f"YFinance news error for {symbol}: {e}")
            return []

    @staticmethod
    async def get_news_by_ticker(
        symbol: str,
        news_count: int = 20,
    ) -> List[NewsArticle]:
        """
        Fetch news from YFinance using Ticker API (works for HK and international stocks).

        Args:
            symbol: Stock symbol (e.g., 1810.HK)
            news_count: Number of news articles to fetch (default 20)

        Returns:
            List of NewsArticle objects
        """
        try:
            import yfinance as yf

            def fetch():
                ticker = yf.Ticker(symbol)
                return ticker.news if hasattr(ticker, "news") else []

            news_data = await run_in_executor(fetch)

            # Ticker API returns nested format: {content: {title, summary, pubDate, provider: {displayName}, ...}}
            # Flatten to match _parse_news_items expected format
            flattened = []
            for item in (news_data or []):
                content = item.get("content", {}) if isinstance(item, dict) else {}
                if not content:
                    continue
                canonical = content.get("canonicalUrl", {}) or {}
                provider = content.get("provider", {}) or {}
                flattened.append({
                    "title": content.get("title", ""),
                    "summary": content.get("summary") or content.get("description", ""),
                    "published_at": content.get("pubDate", ""),
                    "url": canonical.get("url", ""),
                    "publisher": provider.get("displayName", "Yahoo Finance"),
                })

            articles = YFinanceProvider._parse_news_items(flattened, symbol, news_count)
            logger.info(f"Fetched {len(articles)} news articles from YFinance Ticker for {symbol}")
            return articles

        except asyncio.TimeoutError:
            logger.error(f"YFinance Ticker news timeout for {symbol}")
            return []
        except ImportError:
            logger.warning("yfinance not installed, skipping YFinance news")
            return []
        except Exception as e:
            logger.error(f"YFinance Ticker news error for {symbol}: {e}")
            return []


class AKShareProvider:
    """AKShare news provider for A-shares and HK stocks."""

    @staticmethod
    async def get_news_cn(symbol: str) -> List[NewsArticle]:
        """
        Fetch news for A-shares from AKShare (Eastmoney source).

        Args:
            symbol: A-share symbol (e.g., 600519.SS or 000001.SZ)

        Returns:
            List of NewsArticle objects
        """
        try:
            import akshare as ak

            # Extract stock code without market suffix
            code = symbol.replace(".SS", "").replace(".SZ", "")
            market = Market.SH.value if symbol.endswith(".SS") else Market.SZ.value

            def fetch():
                try:
                    # Use Eastmoney individual stock news
                    df = ak.stock_news_em(symbol=code)
                    return df.to_dict("records") if df is not None and not df.empty else []
                except Exception:
                    # Fallback: try general stock news
                    return []

            news_data = await run_in_executor(fetch)

            articles = []
            for item in news_data[:30]:
                try:
                    # Parse date - Eastmoney format varies
                    pub_str = str(item.get("发布时间", "") or item.get("时间", ""))
                    try:
                        if len(pub_str) > 10:
                            published = datetime.strptime(pub_str, "%Y-%m-%d %H:%M:%S")
                        else:
                            published = datetime.strptime(pub_str, "%Y-%m-%d")
                        published = published.replace(tzinfo=timezone.utc)
                    except Exception:
                        published = datetime.now(timezone.utc)

                    title = item.get("新闻标题", "") or item.get("标题", "")
                    url = item.get("新闻链接", "") or item.get("链接", "") or ""
                    content = item.get("新闻内容", "") or item.get("内容", "") or ""

                    # Sanitize title and summary to prevent XSS
                    article = NewsArticle(
                        id=generate_news_id(url or title),
                        symbol=symbol,
                        title=sanitize_html(title) or "",
                        summary=sanitize_html(content[:500]) if content else None,
                        source="eastmoney",
                        url=url,
                        published_at=published,
                        market=market,
                    )
                    articles.append(article)
                except Exception as e:
                    logger.warning(f"Error parsing AKShare CN news item: {e}")
                    continue

            logger.info(f"Fetched {len(articles)} news articles from AKShare for {symbol}")
            return articles

        except asyncio.TimeoutError:
            logger.error(f"AKShare CN news API timeout for {symbol} (>{EXTERNAL_API_TIMEOUT}s)")
            return []
        except Exception as e:
            logger.error(f"AKShare CN news error for {symbol}: {e}")
            return []

    @staticmethod
    async def get_news_hk(symbol: str) -> List[NewsArticle]:
        """
        Fetch news for HK stocks from AKShare.

        LIMITATION: AKShare does not provide dedicated HK stock news API.
        This method currently returns an empty list. For HK stocks, the
        news service will fall back to Finnhub as a secondary provider.

        Args:
            symbol: HK stock symbol (e.g., 0700.HK)

        Returns:
            List of NewsArticle objects (currently always empty - use Finnhub fallback)
        """
        # NOTE: AKShare does not have a dedicated HK stock news function.
        # The previous implementation was calling stock_hk_spot_em() which
        # returns real-time quote data, not news articles.
        #
        # Possible future improvements:
        # 1. Integrate with a dedicated HK news source API
        # 2. Use web scraping for HK financial news sites (requires careful legal review)
        # 3. Use AAStock or HKEX news feeds if available
        #
        # For now, return empty and rely on Finnhub fallback for HK stocks.

        logger.debug(
            f"AKShare HK news not available for {symbol}. "
            "Will fall back to Finnhub provider."
        )
        return []

    @staticmethod
    async def get_trending_news_cn() -> List[NewsArticle]:
        """
        Fetch trending/hot A-share market news from AKShare.

        Returns:
            List of NewsArticle objects
        """
        try:
            import akshare as ak

            def fetch():
                try:
                    # Get hot stock news from Eastmoney
                    df = ak.stock_info_global_em()
                    return df.to_dict("records") if df is not None and not df.empty else []
                except Exception:
                    try:
                        # Fallback to CCTV news
                        df = ak.news_cctv(date=datetime.now().strftime("%Y%m%d"))
                        return df.to_dict("records") if df is not None and not df.empty else []
                    except Exception:
                        return []

            news_data = await run_in_executor(fetch)

            articles = []
            for item in news_data[:20]:
                try:
                    pub_str = str(item.get("发布时间", "") or item.get("date", ""))
                    try:
                        if len(pub_str) > 10:
                            published = datetime.strptime(pub_str[:19], "%Y-%m-%d %H:%M:%S")
                        else:
                            published = datetime.strptime(pub_str[:10], "%Y-%m-%d")
                        published = published.replace(tzinfo=timezone.utc)
                    except Exception:
                        published = datetime.now(timezone.utc)

                    title = item.get("标题", "") or item.get("title", "")
                    url = item.get("链接", "") or item.get("url", "") or ""
                    content = item.get("内容", "") or item.get("content", "") or ""

                    # Sanitize title and summary to prevent XSS
                    article = NewsArticle(
                        id=generate_news_id(url or title),
                        symbol="MARKET",
                        title=sanitize_html(title) or "",
                        summary=sanitize_html(content[:500]) if content else None,
                        source="eastmoney",
                        url=url,
                        published_at=published,
                        market=Market.SH.value,
                    )
                    articles.append(article)
                except Exception as e:
                    logger.warning(f"Error parsing AKShare trending news item: {e}")
                    continue

            logger.info(f"Fetched {len(articles)} trending news articles from AKShare")
            return articles

        except asyncio.TimeoutError:
            logger.error(f"AKShare trending news API timeout (>{EXTERNAL_API_TIMEOUT}s)")
            return []
        except Exception as e:
            logger.error(f"AKShare trending news error: {e}")
            return []


class NewsService:
    """
    Multi-source news aggregation service.

    Data source strategy:
    - US stocks: Finnhub API (primary)
    - A-shares: AKShare/Eastmoney (primary)
    - HK stocks: AKShare (primary), Finnhub (fallback)
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

    async def get_news_by_symbol(
        self,
        symbol: str,
        force_refresh: bool = False,
        user: Optional["User"] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get news for a specific stock symbol.

        Args:
            symbol: Stock symbol
            force_refresh: Skip cache and fetch fresh data
            user: Optional user for accessing user-specific API keys

        Returns:
            List of news articles as dictionaries
        """
        market = detect_market(symbol)
        cache_key = self._get_cache_key("symbol", symbol.upper())

        # Check cache first
        if not force_refresh:
            cached = await self._get_cached(cache_key)
            if cached:
                logger.debug(f"Cache hit for news: {symbol}")
                return cached

        # Fetch from appropriate source based on market
        articles: List[NewsArticle] = []

        # Get user's Finnhub API key if available
        finnhub_key = user.settings.finnhub_api_key if user and user.settings else None

        if market == Market.US.value:
            # US stocks: Check user preference
            news_source = user.settings.news_source if user and user.settings else "yfinance"
            
            if news_source == "finnhub":
                # Use Finnhub only
                articles = await FinnhubProvider.get_news(symbol, api_key=finnhub_key)
            elif news_source == "auto":
                # Try YFinance first, fallback to Finnhub
                articles = await YFinanceProvider.get_news(symbol, news_count=20)
                if not articles:
                    logger.info(f"YFinance returned no news for {symbol}, falling back to Finnhub")
                    articles = await FinnhubProvider.get_news(symbol, api_key=finnhub_key)
            else:
                # Default: YFinance primary, Finnhub fallback
                articles = await YFinanceProvider.get_news(symbol, news_count=20)
                if not articles:
                    logger.info(f"YFinance returned no news for {symbol}, falling back to Finnhub")
                    articles = await FinnhubProvider.get_news(symbol, api_key=finnhub_key)

        elif market == Market.HK.value:
            # HK stocks: YFinance Ticker API (returns ticker-specific news), Finnhub fallback
            articles = await YFinanceProvider.get_news_by_ticker(symbol, news_count=20)
            if not articles:
                logger.info(f"YFinance Ticker returned no HK news for {symbol}, trying Finnhub")
                articles = await FinnhubProvider.get_news(symbol.replace(".HK", ""), api_key=finnhub_key)

        else:  # SH or SZ (A-shares)
            # A-shares: AKShare/Eastmoney primary
            articles = await AKShareProvider.get_news_cn(symbol)

        # Convert to dict and cache
        result = [a.to_dict() for a in articles]
        if result:
            await self._set_cached(cache_key, result)

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
            user: Optional user for accessing user-specific API keys

        Returns:
            List of trending news articles
        """
        # Get user's news source preference
        news_source = user.settings.news_source if user and user.settings else "auto"

        # Build cache key including user's news source preference
        cache_suffix = f"{market or 'all'}:{news_source}"
        cache_key = self._get_cache_key("trending", cache_suffix)

        # Check cache first
        if not force_refresh:
            cached = await self._get_cached(cache_key)
            if cached:
                logger.debug(f"Cache hit for trending news: {cache_suffix}")
                return cached

        articles: List[NewsArticle] = []

        # Get user's Finnhub API key if available
        finnhub_key = user.settings.finnhub_api_key if user and user.settings else None

        # Determine which markets to fetch based on news_source preference
        # auto = US→YFinance/Finnhub, A-shares→AKShare (fetch both)
        # yfinance/finnhub = US-focused sources, only show US news
        # akshare = Chinese A-share news from Eastmoney only
        if news_source == "auto":
            # Auto mode: fetch both US and CN news
            if market is None or market == Market.US.value:
                us_news = await FinnhubProvider.get_general_news("general", api_key=finnhub_key)
                articles.extend(us_news)
            if market is None or market in (Market.SH.value, Market.SZ.value):
                cn_news = await AKShareProvider.get_trending_news_cn()
                articles.extend(cn_news)
        elif news_source == "akshare":
            # Only fetch Chinese A-share market news
            if market is None or market in (Market.SH.value, Market.SZ.value):
                cn_news = await AKShareProvider.get_trending_news_cn()
                articles.extend(cn_news)
        else:
            # yfinance or finnhub: fetch US market news only
            # (YFinance doesn't have general market news, so we use Finnhub for both)
            if market is None or market == Market.US.value:
                us_news = await FinnhubProvider.get_general_news("general", api_key=finnhub_key)
                articles.extend(us_news)

        # Sort by published date, newest first
        articles.sort(key=lambda x: x.published_at, reverse=True)

        # Limit results
        articles = articles[:50]

        # Convert to dict and cache
        result = [a.to_dict() for a in articles]
        if result:
            await self._set_cached(cache_key, result)

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

        # Sort by published date
        sorted_news = sorted(
            all_news.values(),
            key=lambda x: x.get("published_at", ""),
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
