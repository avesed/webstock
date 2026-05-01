"""Skill: fetch news articles for a stock symbol."""

from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


def _normalize_symbol(raw: Any) -> str:
    """Sanitize and normalize a stock symbol."""
    from app.prompts.analysis.sanitizer import sanitize_symbol
    from app.utils.symbol_validation import validate_symbol

    sanitized = sanitize_symbol(raw)
    try:
        return validate_symbol(sanitized)
    except Exception:
        return sanitized


class GetNewsSkill(BaseSkill):
    """Fetch news articles for a stock symbol from the news service."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="get_news",
            description=(
                "Get news articles for a stock symbol. Returns headlines with "
                "title, source, published date, summary, and sentiment score."
            ),
            category="news",
            parameters=[
                SkillParameter(
                    name="symbol",
                    type="string",
                    description="Stock ticker (e.g. AAPL, 0700.HK, 600519.SS)",
                    required=True,
                ),
                SkillParameter(
                    name="limit",
                    type="integer",
                    description="Maximum number of articles to return (default 10)",
                    required=False,
                    default=10,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        symbol = _normalize_symbol(kwargs.get("symbol"))
        limit = kwargs.get("limit", 10)

        if not isinstance(limit, int) or limit < 1:
            limit = 10

        from app.services.newsforge_proxy import NewsForgeProxy

        try:
            proxy = NewsForgeProxy()
            articles = await proxy.get_symbol_news(symbol, limit=limit)
            data = [a.model_dump() if hasattr(a, "model_dump") else a for a in articles]
        except Exception as e:
            logger.warning("NewsForge fetch failed for %s: %s", symbol, e)
            data = []

        return SkillResult(
            success=True,
            data=data,
            metadata={"symbol": symbol, "article_count": len(data)},
        )
