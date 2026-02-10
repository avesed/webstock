"""Skill: fetch global market news from Finnhub and AKShare providers."""

from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


class FetchGlobalNewsSkill(BaseSkill):
    """Fetch global market news from multiple providers (Finnhub + AKShare).

    This skill wraps the Layer 1 global news fetching logic from the news
    monitor pipeline.  It fetches from Finnhub general news categories
    (general, forex, crypto, merger) and AKShare trending A-share news.

    Designed to be called by LangGraph news pipeline nodes.
    """

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="fetch_global_news",
            description=(
                "Fetch global market news from Finnhub (general, forex, crypto, "
                "merger categories) and AKShare (trending A-share news). Returns "
                "a list of article dicts with url, title, summary, source, symbol, "
                "market, and published_at."
            ),
            category="news",
            parameters=[
                SkillParameter(
                    name="finnhub_api_key",
                    type="string",
                    description="Optional Finnhub API key override. Falls back to system config.",
                    required=False,
                ),
                SkillParameter(
                    name="categories",
                    type="array",
                    description=(
                        "Finnhub news categories to fetch. "
                        "Defaults to [\"general\", \"forex\", \"crypto\", \"merger\"]."
                    ),
                    required=False,
                    default=["general", "forex", "crypto", "merger"],
                ),
                SkillParameter(
                    name="include_akshare",
                    type="boolean",
                    description="Whether to include AKShare trending CN news. Default true.",
                    required=False,
                    default=True,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        from app.services.news_service import AKShareProvider, FinnhubProvider

        finnhub_api_key = kwargs.get("finnhub_api_key")
        categories = kwargs.get("categories") or ["general", "forex", "crypto", "merger"]
        include_akshare = kwargs.get("include_akshare", True)

        articles = []
        errors = []
        finnhub_count = 0
        akshare_count = 0

        # Fetch from Finnhub across all requested categories
        for cat in categories:
            try:
                cat_articles = await FinnhubProvider.get_general_news(
                    category=cat, api_key=finnhub_api_key
                )
                serialized = [
                    a.to_dict() if hasattr(a, "to_dict") else a
                    for a in cat_articles
                ]
                articles.extend(serialized)
                finnhub_count += len(serialized)
            except Exception as e:
                logger.warning("FetchGlobalNewsSkill Finnhub [%s] error: %s", cat, e)
                errors.append(f"Finnhub [{cat}]: {e}")

        # Fetch from AKShare trending news
        if include_akshare:
            try:
                akshare_articles = await AKShareProvider.get_trending_news_cn()
                serialized = [
                    a.to_dict() if hasattr(a, "to_dict") else a
                    for a in akshare_articles
                ]
                articles.extend(serialized)
                akshare_count += len(serialized)
            except Exception as e:
                logger.warning("FetchGlobalNewsSkill AKShare error: %s", e)
                errors.append(f"AKShare: {e}")

        return SkillResult(
            success=len(articles) > 0,
            data={"articles": articles, "count": len(articles)},
            error="; ".join(errors) if errors else None,
            metadata={
                "finnhub_count": finnhub_count,
                "akshare_count": akshare_count,
                "categories": categories,
            },
        )
