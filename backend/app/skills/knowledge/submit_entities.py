"""Skill: submit extracted entities with automatic ticker resolution.

Used by the entity extraction agent as its ONLY tool.  The LLM calls this
once with its analysis output; the skill handles all mechanical work:
  1. Normalize & verify ticker symbols via StockListService (in-memory, <10ms)
  2. Optionally expand industry themes via knowledge-base vector search
  3. Deduplicate and cap at 15 entities

By making this the output channel (with ``tool_choice`` forced), we guarantee
structured JSON via the tool-call protocol — no ``extract_json_from_response``
needed.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)

# Maximum entities returned after dedup + merge
_MAX_ENTITIES = 15

# Score assigned to theme-expanded entities (lower than direct mentions)
_THEME_EXPANSION_SCORE = 0.45

# Maximum themes to expand (each triggers a vector search)
_MAX_THEMES = 3


class SubmitEntitiesSkill(BaseSkill):
    """Accept entity extraction results and resolve tickers internally."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="submit_entities",
            description=(
                "Submit the final entity extraction results. The system will "
                "automatically verify and correct stock ticker symbols — you do "
                "NOT need to look up tickers yourself. Just provide company names "
                "and your best guess at the ticker. Also provide industry themes "
                "for the system to find additional related stocks."
            ),
            category="knowledge",
            parameters=[
                SkillParameter(
                    name="entities",
                    type="array",
                    description=(
                        "List of extracted entities. For stocks, always include "
                        "company_name — the system uses it to verify/correct tickers."
                    ),
                    required=True,
                    items={
                        "type": "object",
                        "properties": {
                            "entity": {
                                "type": "string",
                                "description": (
                                    "Stock ticker (AAPL, 600519.SS, 0700.HK) "
                                    "or macro factor name (Fed利率, CPI)"
                                ),
                            },
                            "type": {
                                "type": "string",
                                "enum": ["stock", "index", "macro"],
                            },
                            "company_name": {
                                "type": "string",
                                "description": (
                                    "Company name in Chinese or English "
                                    "(required for stock type, used for ticker verification)"
                                ),
                            },
                            "relation": {
                                "type": "string",
                                "enum": [
                                    "direct",
                                    "industry_peer",
                                    "supply_chain",
                                    "competitor",
                                    "beneficiary",
                                    "subsidiary",
                                ],
                            },
                            "score": {
                                "type": "number",
                                "description": "Relevance score 0.0-1.0",
                            },
                        },
                        "required": ["entity", "type", "relation", "score"],
                    },
                ),
                SkillParameter(
                    name="themes",
                    type="array",
                    description=(
                        "Industry themes or concepts for finding additional related "
                        "stocks via knowledge base. Examples: '人形机器人', 'AI芯片', "
                        "'新能源汽车产业链', 'semiconductor supply chain'. "
                        "Leave empty if the news only involves a single company "
                        "with no broader industry theme."
                    ),
                    required=False,
                    items={"type": "string"},
                ),
                SkillParameter(
                    name="primary_market",
                    type="string",
                    description=(
                        "The primary stock market this news is most relevant to. "
                        "Used to filter theme expansion results. "
                        "cn = China A-shares (沪深), hk = Hong Kong, us = US stocks."
                    ),
                    required=False,
                    enum=["cn", "hk", "us"],
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        entities: List[Dict[str, Any]] = kwargs.get("entities") or []
        themes: List[str] = kwargs.get("themes") or []
        primary_market: str = kwargs.get("primary_market") or ""

        if not entities and not themes:
            return SkillResult(
                success=True,
                data={"entities": []},
            )

        # --- Step 1: Resolve tickers for stock entities ---
        resolved_entities = await _resolve_tickers(entities)

        # --- Step 2: Expand themes via knowledge-base vector search ---
        # Infer dominant market from stock entities; fall back to LLM's
        # explicit primary_market when no stock-type entities are present
        # (e.g. article only mentions indices/macro factors).
        expanded: List[Dict[str, Any]] = []
        if themes:
            dominant_market = _infer_dominant_market(resolved_entities) or primary_market
            # db is injected by the caller (not a SkillParameter)
            db = kwargs.get("db")
            expanded = await _expand_themes(
                themes[:_MAX_THEMES], db, dominant_market=dominant_market,
            )
            resolved_entities.extend(expanded)

        # --- Step 3: Deduplicate by entity key ---
        deduped = _deduplicate(resolved_entities)

        # --- Step 4: Cap at max ---
        # Sort by score descending so we keep the most relevant
        deduped.sort(key=lambda e: e.get("score", 0), reverse=True)
        final = deduped[:_MAX_ENTITIES]

        logger.info(
            "submit_entities: %d input → %d resolved → %d expanded → %d final",
            len(entities),
            len(resolved_entities) - len(expanded),
            len(expanded),
            len(final),
        )

        return SkillResult(
            success=True,
            data={"entities": final},
            metadata={
                "input_count": len(entities),
                "theme_count": len(themes),
                "expanded_count": len(expanded) if themes else 0,
                "final_count": len(final),
            },
        )


async def _resolve_tickers(entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Resolve and verify ticker symbols using StockListService."""
    if not entities:
        return []

    try:
        from app.utils.entity_resolution import resolve_entity_tickers
        from app.services.stock_list_service import get_stock_list_service

        stock_list_svc = await get_stock_list_service()
        return resolve_entity_tickers(entities, stock_list_svc)
    except Exception as e:
        logger.warning("Ticker resolution failed (returning as-is): %s", e)
        return list(entities)


def _infer_dominant_market(entities: List[Dict[str, Any]]) -> str:
    """Infer the primary market from the LLM's direct entity picks.

    Returns "cn", "hk", "us", or "" (unknown/mixed).
    Used to filter theme expansion results to the same market.
    """
    market_counts: Dict[str, int] = {}
    for e in entities:
        if e.get("type") != "stock":
            continue
        sym = e.get("entity", "")
        if sym.endswith(".SS") or sym.endswith(".SZ"):
            market_counts["cn"] = market_counts.get("cn", 0) + 1
        elif sym.endswith(".HK"):
            market_counts["hk"] = market_counts.get("hk", 0) + 1
        elif sym and not sym.endswith("=F"):
            market_counts["us"] = market_counts.get("us", 0) + 1
    if not market_counts:
        return ""
    return max(market_counts, key=market_counts.get)  # type: ignore[arg-type]


def _symbol_matches_market(symbol: str, market: str) -> bool:
    """Check if a symbol belongs to the given market."""
    if not market:
        return True  # no filtering
    if market == "cn":
        return symbol.endswith(".SS") or symbol.endswith(".SZ")
    if market == "hk":
        return symbol.endswith(".HK")
    if market == "us":
        # US stocks: no suffix, or not matching CN/HK/metal patterns
        return not (
            symbol.endswith(".SS") or symbol.endswith(".SZ")
            or symbol.endswith(".HK") or symbol.endswith("=F")
        )
    return True


async def _expand_themes(
    themes: List[str],
    db: Any = None,
    dominant_market: str = "",
) -> List[Dict[str, Any]]:
    """Search knowledge base for stocks related to each theme.

    Returns entity dicts ready to merge with the main list.
    Filters results to match the dominant market (inferred from
    the LLM's direct entity picks) to avoid cross-market noise.
    Best-effort: failures are silently ignored.
    """
    if not themes:
        return []

    expanded: List[Dict[str, Any]] = []
    seen_symbols: set = set()

    try:
        from app.skills.knowledge.search_related_stocks import SearchRelatedStocksSkill

        skill = SearchRelatedStocksSkill()

        for theme in themes:
            theme = (theme or "").strip()
            if not theme:
                continue

            try:
                # Use a fresh task session if caller didn't provide one
                if db is None:
                    from app.db.task_session import get_task_session
                    async with get_task_session() as task_db:
                        result = await skill.safe_execute(
                            timeout=30.0, query=theme, db=task_db,
                        )
                else:
                    result = await skill.safe_execute(
                        timeout=30.0, query=theme, db=db,
                    )

                if not result.success or not result.data:
                    continue

                # result.data is a list of {symbol, text, relevance}
                items = result.data if isinstance(result.data, list) else []
                added_this_theme = 0
                for item in items:
                    if added_this_theme >= 3:  # max 3 per theme
                        break
                    symbol = item.get("symbol", "")
                    if not symbol or symbol in seen_symbols:
                        continue
                    # Filter by dominant market to avoid cross-market noise
                    if not _symbol_matches_market(symbol, dominant_market):
                        continue
                    seen_symbols.add(symbol)
                    expanded.append({
                        "entity": symbol,
                        "type": "stock",
                        "company_name": "",
                        "relation": "industry_peer",
                        "score": _THEME_EXPANSION_SCORE,
                    })
                    added_this_theme += 1
            except Exception as theme_err:
                logger.debug(
                    "Theme expansion failed for '%s': %s", theme, theme_err,
                )
    except Exception as e:
        logger.warning("Theme expansion setup failed: %s", e)

    logger.info(
        "Theme expansion: market=%s, %d results from %d themes",
        dominant_market or "(none)", len(expanded), len(themes),
    )

    return expanded


def _deduplicate(entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate entities by (entity, type) key, keeping highest score."""
    seen: Dict[str, Dict[str, Any]] = {}
    for e in entities:
        key = f"{e.get('entity', '')}::{e.get('type', '')}"
        existing = seen.get(key)
        if existing is None or e.get("score", 0) > existing.get("score", 0):
            seen[key] = e
    return list(seen.values())
