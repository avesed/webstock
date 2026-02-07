"""News filter service using LLM to evaluate news relevance."""

import logging
from typing import Optional

from app.config import settings
from app.core.openai_client import get_openai_client
from app.prompts import NEWS_FILTER_SYSTEM_PROMPT, NEWS_FILTER_USER_PROMPT

logger = logging.getLogger(__name__)

# Default model for news filtering (should be fast and cheap)
DEFAULT_FILTER_MODEL = "gpt-4o-mini"


class NewsFilterService:
    """
    Service for filtering news articles using LLM evaluation.

    Uses a small, fast model (default: gpt-4o-mini) to evaluate whether
    a news article is relevant and valuable for investors.
    """

    def __init__(self, model: Optional[str] = None) -> None:
        """
        Initialize the filter service.

        Args:
            model: Model to use for filtering. Defaults to gpt-4o-mini.
        """
        self.model = model or DEFAULT_FILTER_MODEL

    async def evaluate_relevance(
        self,
        title: str,
        summary: Optional[str] = None,
        full_text: Optional[str] = None,
        source: Optional[str] = None,
        symbol: Optional[str] = None,
        model: Optional[str] = None,
    ) -> bool:
        """
        Evaluate whether a news article is relevant for investors.

        Args:
            title: News article title
            summary: Article summary (optional)
            full_text: Full article text (optional, will be truncated)
            source: News source name
            symbol: Related stock symbol
            model: Override model for this evaluation

        Returns:
            True if the article should be kept, False if it should be filtered out
        """
        if not title:
            logger.warning("Cannot evaluate news without title")
            return False

        import time
        start_time = time.monotonic()
        use_model = model or self.model

        logger.info(
            "Starting news filter evaluation: model=%s, symbol=%s, title=%s",
            use_model, symbol or "N/A", title[:50]
        )

        try:
            # Build the prompt
            full_text_section = ""
            if full_text:
                # Truncate full text to avoid token limits
                truncated = full_text[:2000] if len(full_text) > 2000 else full_text
                full_text_section = f"Full text (truncated):\n{truncated}"

            user_prompt = NEWS_FILTER_USER_PROMPT.format(
                title=title,
                summary=summary or "N/A",
                source=source or "Unknown",
                symbol=symbol or "N/A",
                full_text_section=full_text_section,
            )

            # Call OpenAI API
            client = get_openai_client()
            response = await client.chat.completions.create(
                model=use_model,
                messages=[
                    {"role": "system", "content": NEWS_FILTER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=10,  # We only need KEEP or DELETE
                temperature=0.0,  # Deterministic output
            )

            # Log token usage
            elapsed = time.monotonic() - start_time
            usage = response.usage
            if usage:
                logger.info(
                    "Filter LLM call completed: model=%s, prompt_tokens=%d, completion_tokens=%d, elapsed=%.2fs",
                    use_model, usage.prompt_tokens, usage.completion_tokens, elapsed
                )

            # Parse response
            result = response.choices[0].message.content.strip().upper()

            if result == "KEEP":
                logger.debug("News filtered: KEEP - %s", title[:50])
                return True
            elif result == "DELETE":
                logger.debug("News filtered: DELETE - %s", title[:50])
                return False
            else:
                # Unexpected response, default to keeping
                logger.warning(
                    "Unexpected filter response '%s' for news: %s",
                    result, title[:50]
                )
                return True

        except Exception as e:
            logger.error("Error evaluating news relevance: %s", e)
            # On error, default to keeping the article
            return True

    async def batch_evaluate(
        self,
        articles: list[dict],
        model: Optional[str] = None,
    ) -> dict[str, bool]:
        """
        Evaluate multiple articles in batch.

        Args:
            articles: List of article dicts with keys: id, title, summary, full_text, source, symbol
            model: Override model for evaluation

        Returns:
            Dict mapping article id to keep (True) or delete (False)
        """
        results = {}

        for article in articles:
            article_id = article.get("id")
            if not article_id:
                continue

            keep = await self.evaluate_relevance(
                title=article.get("title", ""),
                summary=article.get("summary"),
                full_text=article.get("full_text"),
                source=article.get("source"),
                symbol=article.get("symbol"),
                model=model,
            )
            results[article_id] = keep

        logger.info(
            "Batch evaluation complete: %d articles, %d kept, %d deleted",
            len(results),
            sum(1 for v in results.values() if v),
            sum(1 for v in results.values() if not v),
        )

        return results


# Singleton instance
_filter_service: Optional[NewsFilterService] = None


def get_news_filter_service(model: Optional[str] = None) -> NewsFilterService:
    """
    Get NewsFilterService instance.

    Args:
        model: Model to use for filtering

    Returns:
        NewsFilterService instance
    """
    global _filter_service
    if _filter_service is None:
        _filter_service = NewsFilterService(model=model)
    return _filter_service
