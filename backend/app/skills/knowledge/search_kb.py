"""Skill: search the internal knowledge base via RAG."""

from __future__ import annotations

import logging
from typing import Any, Optional

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
        from app.services.rag.embedding import get_embedding_model_from_db

        query = sanitize_input(kwargs.get("query", ""), max_length=500)
        symbol = _normalize_symbol(kwargs.get("symbol"))

        if not query:
            return SkillResult(
                success=False,
                error="query parameter is required",
            )

        index_service = get_index_service()
        try:
            embedding_model = await get_embedding_model_from_db(db)
        except ValueError as e:
            return SkillResult(success=False, error=str(e))

        query_embedding = await index_service.generate_embedding(
            query, model=embedding_model
        )
        if not query_embedding:
            return SkillResult(
                success=True,
                data={"info": "Could not generate embedding for search query"},
            )

        results = await index_service.search(
            db=db,
            query_embedding=query_embedding,
            query_text=query,
            symbol=symbol,
            top_k=3,
            embedding_model=embedding_model,
        )

        if not results:
            return SkillResult(
                success=True,
                data={"info": "No relevant documents found in knowledge base"},
            )

        return SkillResult(
            success=True,
            data=[r.to_dict() for r in results],
            metadata={
                "query": query,
                "symbol": symbol,
                "result_count": len(results),
            },
        )
