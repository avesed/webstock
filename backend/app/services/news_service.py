"""Multi-source news aggregation service with caching and fallback support."""

import asyncio
import hashlib
import html
import json
import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional, TypedDict

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
    from openai import AsyncOpenAI

    from app.config import settings as app_settings
    from app.services.settings_service import SettingsService

    # Get system settings for LLM configuration
    settings_service = SettingsService()
    system_settings = await settings_service.get_system_settings(db)

    # Use news-specific LLM configuration
    if system_settings.news_use_llm_config:
        api_key = (
            system_settings.news_openai_api_key
            or system_settings.openai_api_key
            or app_settings.OPENAI_API_KEY
        )
        base_url = (
            system_settings.news_openai_base_url
            or system_settings.openai_base_url
            or app_settings.OPENAI_API_BASE
        )
    else:
        api_key = system_settings.openai_api_key or app_settings.OPENAI_API_KEY
        base_url = system_settings.openai_base_url or app_settings.OPENAI_API_BASE

    model = system_settings.news_filter_model or "gpt-4o-mini"

    if not api_key:
        logger.warning("No OpenAI API key configured, skipping entity extraction")
        return {a.get("url", ""): [] for a in articles}

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
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
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=1500,
            )

            content = response.choices[0].message.content or ""
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

    @staticmethod
    async def get_market_news_with_entities(
        db,  # AsyncSession
        category: str = "general",
        api_key: Optional[str] = None,
    ) -> List[NewsArticle]:
        """
        Fetch market news from Finnhub and extract related entities using LLM.

        This method combines Finnhub's general market news with LLM-based entity
        extraction to identify related stocks, indices, and macro factors.

        Args:
            db: Database session for accessing system settings
            category: News category (general, forex, crypto, merger)
            api_key: Optional user-provided Finnhub API key

        Returns:
            List of NewsArticle objects with related_entities populated
        """
        # 1. Fetch raw news from Finnhub
        raw_news = await FinnhubProvider.get_general_news(category, api_key)

        if not raw_news:
            logger.info("No news articles fetched from Finnhub general news")
            return []

        # 2. Prepare articles data for entity extraction
        articles_data = [
            {
                "url": article.url,
                "headline": article.title,
                "summary": article.summary or "",
            }
            for article in raw_news
        ]

        # 3. Batch extract related entities using LLM
        entities_map = await extract_related_entities(db, articles_data)

        # 4. Update articles with extracted entities and determine primary symbol
        for article in raw_news:
            entities = entities_map.get(article.url, [])
            article.related_entities = entities

            # Set primary symbol: prefer highest-scored stock entity
            stock_entities = [e for e in entities if e["type"] == "stock"]
            if stock_entities:
                # Use the stock with highest score as primary symbol
                article.symbol = stock_entities[0]["entity"]
            elif entities:
                # No stock entities, use first entity (could be index/macro)
                article.symbol = entities[0]["entity"]
            # else: keep default "MARKET" symbol from get_general_news

        entities_count = sum(1 for a in raw_news if a.related_entities)
        logger.info(
            f"Processed {len(raw_news)} market news articles, "
            f"{entities_count} with extracted entities"
        )

        return raw_news


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
                    # Parse date - Eastmoney format varies (timestamps are CST/UTC+8)
                    pub_str = str(item.get("发布时间", "") or item.get("时间", ""))
                    cst = timezone(timedelta(hours=8))
                    try:
                        if len(pub_str) > 10:
                            published = datetime.strptime(pub_str, "%Y-%m-%d %H:%M:%S")
                        else:
                            published = datetime.strptime(pub_str, "%Y-%m-%d")
                        # Mark as CST then convert to UTC
                        published = published.replace(tzinfo=cst).astimezone(timezone.utc)
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
                    cst = timezone(timedelta(hours=8))
                    try:
                        if len(pub_str) > 10:
                            published = datetime.strptime(pub_str[:19], "%Y-%m-%d %H:%M:%S")
                        else:
                            published = datetime.strptime(pub_str[:10], "%Y-%m-%d")
                        # Mark as CST then convert to UTC
                        published = published.replace(tzinfo=cst).astimezone(timezone.utc)
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

        # Get user's Finnhub API key if available (defensive access in case column doesn't exist)
        finnhub_key = None
        if user and user.settings:
            finnhub_key = getattr(user.settings, 'finnhub_api_key', None)

        if market == Market.US.value:
            # US stocks: Check user preference (defensive access for news_source)
            news_source = "yfinance"
            if user and user.settings:
                news_source = getattr(user.settings, 'news_source', None) or "yfinance"
            
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

        elif market == Market.METAL.value:
            # Precious metals: YFinance Ticker API (GC=F, SI=F, etc.)
            articles = await YFinanceProvider.get_news_by_ticker(symbol, news_count=20)

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

        articles: List[NewsArticle] = []

        # Get user's Finnhub API key if available (defensive access)
        finnhub_key = None
        if user and user.settings:
            finnhub_key = getattr(user.settings, 'finnhub_api_key', None)

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
