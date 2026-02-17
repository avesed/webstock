"""Skill: search the stock profile knowledge base for related stocks.

Used by the entity extraction agent (via function calling) to find stocks
associated with an industry theme, concept, or supply chain relationship.
The knowledge base contains vectorized stock profiles enriched with concept
board labels, industry classifications, and business descriptions.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


class SearchRelatedStocksSkill(BaseSkill):
    """Search vectorized stock profiles for thematically related stocks."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="search_related_stocks",
            description=(
                "Search the stock knowledge base for stocks related to an industry "
                "theme, concept, or supply chain. Returns matching stock symbols with "
                "their profile summary and relevance score. Use this when you need to "
                "find stocks associated with a topic like '人形机器人', 'AI芯片', "
                "'新能源汽车', 'semiconductor', or 'cloud computing'."
            ),
            category="knowledge",
            parameters=[
                SkillParameter(
                    name="query",
                    type="string",
                    description=(
                        "Natural language query describing the industry theme, concept, "
                        "or business area to search for related stocks. "
                        "Examples: '人形机器人制造商和供应商', 'AI GPU chips', "
                        "'白酒行业龙头', 'electric vehicle battery supply chain'"
                    ),
                    required=True,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        # db is injected by the caller, not exposed as a SkillParameter
        db = kwargs.get("db")
        if db is None:
            return SkillResult(
                success=False,
                error="db must be provided by the caller",
            )

        from app.services.rag import get_index_service
        from app.services.rag.embedding import get_embedding_config_from_db

        query = (kwargs.get("query") or "").strip()
        if not query:
            return SkillResult(success=False, error="query parameter is required")

        # Truncate overly long queries
        query = query[:500]

        try:
            embed_config = await get_embedding_config_from_db(db)
        except ValueError as e:
            return SkillResult(success=False, error=str(e))

        index_service = get_index_service()

        # Generate query embedding
        query_embedding = await index_service.generate_embedding(
            query,
            model=embed_config.model,
            api_key=embed_config.api_key,
            base_url=embed_config.base_url,
        )

        if not query_embedding:
            logger.warning(
                "[SearchRelatedStocks] Embedding generation failed for query: %s",
                query[:100],
            )
            return SkillResult(
                success=False,
                error="Failed to generate query embedding",
            )

        # Search stock_profile embeddings — use hybrid search for best results
        results = await index_service.search(
            db=db,
            query_embedding=query_embedding,
            query_text=query,
            source_type="stock_profile",
            top_k=20,
            embedding_model=embed_config.model,
        )

        if not results:
            return SkillResult(
                success=True,
                data={"info": "No related stocks found in knowledge base"},
            )

        # Format results: symbol + truncated profile text + relevance
        formatted: List[Dict[str, Any]] = []
        for r in results:
            formatted.append({
                "symbol": r.source_id,
                "text": (r.chunk_text or "")[:300],
                "relevance": round(r.score, 4) if r.score else 0.0,
            })

        return SkillResult(
            success=True,
            data=formatted,
            metadata={
                "query": query,
                "result_count": len(formatted),
            },
        )
