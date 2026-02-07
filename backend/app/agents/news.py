"""News analysis agent for assessing news impact on stock prices."""

import logging
from typing import Any, Dict, List, Optional

from app.agents.base import AgentType, BaseAgent
from app.agents.prompts.news_prompt import (
    build_news_analysis_prompt,
    get_news_analysis_system_prompt,
)
from app.core.circuit_breaker import CircuitBreaker
from app.core.token_bucket import TokenBucket

logger = logging.getLogger(__name__)


class NewsAgent(BaseAgent):
    """
    Agent for analyzing news impact on stock prices.

    Analyzes:
    - News sentiment and tone
    - Potential price impact (direction, magnitude, timeframe)
    - Key themes and catalysts
    - Risk factors from news events

    Unlike other agents that analyze market data directly, this agent
    focuses on interpreting news articles and their market implications.
    """

    def __init__(
        self,
        rate_limiter: Optional[TokenBucket] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        super().__init__(rate_limiter, circuit_breaker)

    @property
    def agent_type(self) -> AgentType:
        """Return the agent type."""
        return AgentType.NEWS

    def get_system_prompt(self, market: str, language: str = "en") -> str:
        """Get the system prompt for news analysis."""
        return get_news_analysis_system_prompt(language)

    async def build_user_prompt(
        self,
        symbol: str,
        market: str,
        data: Dict[str, Any],
        language: str = "en",
    ) -> str:
        """Build the user prompt with news data."""
        articles = data.get("articles", [])
        if not articles:
            if language == "zh":
                return (
                    f"未找到 {symbol} 的近期新闻文章。"
                    f"请根据股票代码和市场（{market}）提供一般市场展望。"
                )
            return (
                f"No recent news articles found for {symbol}. "
                f"Please provide a general market outlook based on the symbol "
                f"and market ({market})."
            )

        # Build a combined prompt from the most important articles
        combined_summaries = []
        for article in articles[:5]:  # Top 5 articles
            if article.get("summary"):
                combined_summaries.append(article["summary"][:300])

        # Use the first article's details for the structured prompt
        primary = articles[0]
        prompt = build_news_analysis_prompt(
            symbol=symbol,
            title=primary.get("title", "No title" if language == "en" else "无标题"),
            summary=(
                "\n\n".join(combined_summaries)
                if combined_summaries
                else primary.get("summary")
            ),
            source=primary.get("source", "unknown" if language == "en" else "未知"),
            published_at=primary.get("publishedAt", "unknown" if language == "en" else "未知"),
            market=market,
            additional_context=data.get("stock_context"),
            language=language,
        )

        # Append additional article titles for comprehensive analysis
        if len(articles) > 1:
            if language == "zh":
                prompt += "\n\n## 其他近期头条\n"
            else:
                prompt += "\n\n## Additional Recent Headlines\n"
            for article in articles[1:10]:
                source = article.get("source", "")
                prompt += f"- [{source}] {article.get('title', '')}\n"

        return prompt

    async def prepare_data(
        self,
        symbol: str,
        market: str,
    ) -> Dict[str, Any]:
        """
        Prepare news data for analysis.

        Fetches recent news articles and optional stock context.
        """
        from app.services.news_service import get_news_service

        news_service = await get_news_service()

        # Fetch news articles
        try:
            articles = await news_service.get_news_by_symbol(symbol)
        except Exception as e:
            logger.error("Failed to fetch news for %s: %s", symbol, e)
            articles = []

        logger.info("Fetched %d news articles for %s", len(articles), symbol)

        # Get basic stock context for the AI (with timeout)
        import asyncio
        try:
            stock_context = await asyncio.wait_for(
                self._get_stock_context(symbol), timeout=10.0
            )
        except asyncio.TimeoutError:
            logger.warning("Stock context fetch timed out for %s", symbol)
            stock_context = None

        # Score and sort articles by importance (copies to avoid mutating cached data)
        scored_articles = self._score_articles(articles)

        return {
            "articles": scored_articles,
            "stock_context": stock_context,
        }

    def _score_articles(
        self,
        articles: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Score and sort articles by importance.

        Scoring factors:
        - Source reputation weight
        - Title keyword relevance
        """
        SOURCE_WEIGHTS = {
            "reuters": 1.5,
            "bloomberg": 1.5,
            "wsj": 1.4,
            "cnbc": 1.3,
            "ft": 1.4,
            "barrons": 1.3,
            "seekingalpha": 1.1,
            "marketwatch": 1.2,
            "eastmoney": 1.2,
            "sina": 1.1,
            "finnhub": 1.0,
            "yfinance": 1.0,
        }

        IMPORTANCE_KEYWORDS = {
            # High-impact events
            "earnings": 2.0, "revenue": 1.8, "profit": 1.8, "loss": 1.8,
            "acquisition": 2.0, "merger": 2.0, "buyout": 2.0,
            "bankruptcy": 2.5, "fraud": 2.5, "investigation": 2.0,
            "fda": 2.0, "approval": 1.8, "patent": 1.5,
            "dividend": 1.5, "buyback": 1.5, "split": 1.5,
            # Medium-impact events
            "upgrade": 1.5, "downgrade": 1.5, "target": 1.3,
            "guidance": 1.5, "forecast": 1.3, "outlook": 1.3,
            "ceo": 1.4, "resign": 1.5, "appoint": 1.3,
            "lawsuit": 1.5, "settlement": 1.4, "regulation": 1.3,
            # Chinese market keywords
            "\u5229\u6da6": 1.8, "\u8425\u6536": 1.8, "\u4e8f\u635f": 1.8,
            "\u6536\u8d2d": 2.0, "\u5408\u5e76": 2.0, "\u5206\u7ea2": 1.5,
            "\u6da8\u505c": 1.5, "\u8dcc\u505c": 1.5, "\u505c\u724c": 1.8,
        }

        scored = []
        for article in articles:
            score = 1.0

            # Source weight
            source = (article.get("source") or "").lower()
            for src_key, weight in SOURCE_WEIGHTS.items():
                if src_key in source:
                    score *= weight
                    break

            # Keyword weight
            title = (article.get("title") or "").lower()
            summary = (article.get("summary") or "").lower()
            text = f"{title} {summary}"
            max_keyword_weight = 1.0
            for keyword, weight in IMPORTANCE_KEYWORDS.items():
                if keyword in text:
                    max_keyword_weight = max(max_keyword_weight, weight)
            score *= max_keyword_weight

            # Copy to avoid mutating cached data from NewsService
            scored_article = {**article, "_importance_score": round(score, 2)}
            scored.append(scored_article)

        # Sort by importance score descending
        scored.sort(key=lambda x: x.get("_importance_score", 0), reverse=True)
        return scored

    async def _get_stock_context(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get current stock context for the AI prompt."""
        try:
            from app.services.stock_service import get_stock_service

            stock_service = await get_stock_service()
            quote = await stock_service.get_quote(symbol)
            if quote:
                return {
                    "price": quote.get("price"),
                    "change_percent": quote.get("changePercent"),
                    "market_cap": quote.get("marketCap"),
                    "sector": quote.get("sector"),
                }
        except Exception as e:
            logger.debug("Could not get stock context for %s: %s", symbol, e)
        return None


async def create_news_agent(
    rate_limiter: Optional[TokenBucket] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
) -> NewsAgent:
    """Create a news analysis agent."""
    return NewsAgent(
        rate_limiter=rate_limiter,
        circuit_breaker=circuit_breaker,
    )
