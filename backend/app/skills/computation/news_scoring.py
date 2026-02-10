"""Skill: score and sort news articles by importance."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)

# Authoritative financial news sources and their weight multipliers
SOURCE_WEIGHTS: Dict[str, float] = {
    "reuters": 1.5,
    "bloomberg": 1.5,
    "wsj": 1.4,
    "cnbc": 1.3,
    "ft": 1.4,
    "barrons": 1.3,
    "seekingalpha": 1.1,
    "marketwatch": 1.2,
}

# High-impact financial keywords and their weight multipliers
KEYWORDS: Dict[str, float] = {
    "earnings": 2.0,
    "revenue": 1.8,
    "profit": 1.8,
    "acquisition": 2.0,
    "merger": 2.0,
    "bankruptcy": 2.5,
    "fraud": 2.5,
    "fda": 2.0,
    "approval": 1.8,
    "upgrade": 1.5,
    "downgrade": 1.5,
}


def _score_news_articles(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Score and sort news articles by importance.

    Extracted from analysis_nodes.py for reuse across agents and chat tools.
    """
    scored = []
    for article in articles:
        score = 1.0

        source = (article.get("source") or "").lower()
        for src_key, weight in SOURCE_WEIGHTS.items():
            if src_key in source:
                score *= weight
                break

        title = (article.get("title") or "").lower()
        summary = (article.get("summary") or "").lower()
        text = f"{title} {summary}"
        max_kw_weight = 1.0
        for kw, weight in KEYWORDS.items():
            if kw in text:
                max_kw_weight = max(max_kw_weight, weight)
        score *= max_kw_weight

        scored.append({**article, "_importance_score": round(score, 2)})

    scored.sort(key=lambda x: x.get("_importance_score", 0), reverse=True)
    return scored


class ScoreNewsArticlesSkill(BaseSkill):
    """Score and rank news articles by source authority and keyword relevance."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="score_news_articles",
            description=(
                "Score and sort news articles by importance based on source "
                "authority (Reuters, Bloomberg, WSJ, etc.) and keyword relevance "
                "(earnings, acquisition, bankruptcy, etc.)."
            ),
            category="computation",
            parameters=[
                SkillParameter(
                    name="articles",
                    type="array",
                    description="List of news article dicts with title, summary, and source fields",
                    required=True,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        articles = kwargs.get("articles")

        if not isinstance(articles, list):
            return SkillResult(
                success=False,
                error="articles parameter is required and must be a list",
            )

        scored = _score_news_articles(articles)

        return SkillResult(
            success=True,
            data=scored,
            metadata={"article_count": len(scored)},
        )
