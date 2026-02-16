"""Skill: search the internal knowledge base via RAG."""

from __future__ import annotations

import logging
import uuid as uuid_mod
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


def _normalize_symbol(raw: Optional[str]) -> Optional[str]:
    """Sanitize and normalize an optional stock symbol."""
    if not raw:
        return None

    from app.prompts.analysis.sanitizer import sanitize_symbol
    from app.utils.symbol_validation import validate_symbol

    sanitized = sanitize_symbol(raw)
    try:
        return validate_symbol(sanitized)
    except Exception:
        return sanitized


async def _enrich_news_results(
    db: AsyncSession,
    results: List[Any],
) -> List[Dict[str, Any]]:
    """Enrich RAG results whose source_type is 'news' with News table metadata.

    Non-news results pass through with the base ``to_dict()`` representation.
    """
    from app.models.news import News

    # Separate news vs non-news results
    news_source_ids: List[str] = []
    for r in results:
        if r.source_type == "news":
            try:
                uuid_mod.UUID(r.source_id)
                news_source_ids.append(r.source_id)
            except (ValueError, AttributeError):
                pass

    # Batch-fetch News rows
    news_metadata: Dict[str, News] = {}
    if news_source_ids:
        uuids = [uuid_mod.UUID(sid) for sid in news_source_ids]
        rows = (await db.execute(select(News).where(News.id.in_(uuids)))).scalars().all()
        news_metadata = {str(row.id): row for row in rows}

    enriched: List[Dict[str, Any]] = []
    for r in results:
        base = r.to_dict()

        news_row = news_metadata.get(r.source_id) if r.source_type == "news" else None
        if news_row:
            base["title"] = news_row.title
            base["source"] = news_row.source
            base["published_at"] = (
                news_row.published_at.isoformat() if news_row.published_at else None
            )
            base["url"] = news_row.url
            base["sentiment_tag"] = news_row.sentiment_tag
            base["content_score"] = news_row.content_score
            base["investment_summary"] = news_row.investment_summary
            base["detailed_summary"] = news_row.detailed_summary
            base["industry_tags"] = news_row.industry_tags or []
            base["event_tags"] = news_row.event_tags or []

        enriched.append(base)

    return enriched


class SearchKnowledgeBaseSkill(BaseSkill):
    """Search the internal knowledge base of past analysis reports, news, and research."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="search_knowledge_base",
            description=(
                "Search the internal knowledge base of past analysis reports, "
                "news articles, and research. Use for context about past analyses "
                "or when the user references previous reports."
            ),
            category="knowledge",
            parameters=[
                SkillParameter(
                    name="query",
                    type="string",
                    description="Natural language search query",
                    required=True,
                ),
                SkillParameter(
                    name="symbol",
                    type="string",
                    description="Optional: filter to a specific stock symbol",
                    required=False,
                ),
                SkillParameter(
                    name="source_type",
                    type="string",
                    description="Optional: filter by source type (e.g., 'news', 'analysis')",
                    required=False,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        # db is injected by the chat adapter, not exposed as a SkillParameter.
        db = kwargs.get("db")

        if db is None:
            return SkillResult(
                success=False,
                error="db must be provided by the caller",
            )

        from app.prompts.analysis.sanitizer import sanitize_input
        from app.services.rag import get_index_service
        from app.services.rag.embedding import get_embedding_config_from_db

        query = sanitize_input(kwargs.get("query", ""), max_length=500)
        symbol = _normalize_symbol(kwargs.get("symbol"))
        source_type = kwargs.get("source_type")

        if not query:
            return SkillResult(
                success=False,
                error="query parameter is required",
            )

        index_service = get_index_service()
        try:
            embed_config = await get_embedding_config_from_db(db)
        except ValueError as e:
            return SkillResult(success=False, error=str(e))

        query_embedding = await index_service.generate_embedding(
            query,
            model=embed_config.model,
            api_key=embed_config.api_key,
            base_url=embed_config.base_url,
        )

        if query_embedding:
            results = await index_service.search(
                db=db,
                query_embedding=query_embedding,
                query_text=query,
                symbol=symbol,
                source_type=source_type,
                top_k=5,
                embedding_model=embed_config.model,
            )
        else:
            # Fallback to keyword-only search when embedding generation fails
            logger.warning("Embedding generation failed, falling back to keyword search")
            backend = index_service.search_backends.get("keyword")
            results = (
                await backend.search(
                    db, query_text=query, symbol=symbol,
                    source_type=source_type, top_k=5,
                )
                if backend
                else []
            )

        if not results:
            return SkillResult(
                success=True,
                data={"info": "No relevant documents found in knowledge base"},
            )

        enriched = await _enrich_news_results(db, results)

        return SkillResult(
            success=True,
            data=enriched,
            metadata={
                "query": query,
                "symbol": symbol,
                "result_count": len(enriched),
            },
        )
