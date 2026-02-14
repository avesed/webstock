"""Skill registry — central lookup for all registered skills."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from app.skills.base import BaseSkill, SkillDefinition

logger = logging.getLogger(__name__)

_registry: Optional[SkillRegistry] = None


class SkillRegistry:
    """Central registry of all available skills."""

    def __init__(self) -> None:
        self._skills: Dict[str, BaseSkill] = {}
        self._by_category: Dict[str, List[BaseSkill]] = {}

    def register(self, skill: BaseSkill) -> None:
        """Register a skill instance."""
        defn = skill.definition()
        if defn.name in self._skills:
            logger.warning("Skill %s already registered, overwriting", defn.name)
        self._skills[defn.name] = skill
        self._by_category.setdefault(defn.category, []).append(skill)
        logger.debug("Registered skill: %s (category=%s)", defn.name, defn.category)

    def get(self, name: str) -> Optional[BaseSkill]:
        """Get a skill by name."""
        return self._skills.get(name)

    def get_by_category(self, category: str) -> List[BaseSkill]:
        """Get all skills in a category."""
        return list(self._by_category.get(category, []))

    def get_all(self) -> List[BaseSkill]:
        """Get all registered skills."""
        return list(self._skills.values())

    def get_definitions(self, names: Optional[List[str]] = None) -> List[SkillDefinition]:
        """Get definitions for the given skill names (or all if None)."""
        if names is None:
            return [s.definition() for s in self._skills.values()]
        return [
            self._skills[n].definition()
            for n in names
            if n in self._skills
        ]

    @property
    def skill_names(self) -> List[str]:
        return list(self._skills.keys())

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills


def get_skill_registry() -> SkillRegistry:
    """Get the singleton skill registry, auto-registering all skills on first call."""
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
        _register_all_skills(_registry)
    return _registry


def reset_skill_registry() -> None:
    """Reset the registry (for testing or Celery worker cleanup)."""
    global _registry
    _registry = None


def _register_all_skills(registry: SkillRegistry) -> None:
    """Import and register every skill class."""
    # Market data skills
    from app.skills.market_data.stock_quote import GetStockQuoteSkill
    from app.skills.market_data.stock_history import GetStockHistorySkill
    from app.skills.market_data.stock_info import GetStockInfoSkill
    from app.skills.market_data.stock_financials import GetStockFinancialsSkill
    from app.skills.market_data.search_stocks import SearchStocksSkill
    from app.skills.market_data.institutional import GetInstitutionalHoldersSkill
    from app.skills.market_data.fund_holdings_cn import GetFundHoldingsCnSkill
    from app.skills.market_data.northbound import GetNorthboundHoldingSkill
    from app.skills.market_data.sector_industry import GetSectorIndustrySkill
    from app.skills.market_data.analyst_ratings import GetAnalystRatingsSkill
    from app.skills.market_data.market_context import GetMarketContextSkill

    # Computation skills
    from app.skills.computation.technical_indicators import CalculateTechnicalIndicatorsSkill
    from app.skills.computation.history_summary import CalculateHistorySummarySkill
    from app.skills.computation.news_scoring import ScoreNewsArticlesSkill

    # News skills
    from app.skills.news.get_news import GetNewsSkill
    from app.skills.news.fetch_global_news import FetchGlobalNewsSkill
    from app.skills.news.fetch_full_content import FetchFullContentSkill
    from app.skills.news.deep_filter import DeepFilterNewsSkill

    # User data skills
    from app.skills.user_data.portfolio import GetPortfolioSkill
    from app.skills.user_data.watchlist import GetWatchlistSkill

    # Knowledge skills
    from app.skills.knowledge.search_kb import SearchKnowledgeBaseSkill
    from app.skills.knowledge.embed_document import EmbedDocumentSkill

    # Quantitative (Qlib)
    from app.skills.qlib.factor_skill import QlibFactorSkill
    from app.skills.qlib.expression_skill import QlibExpressionSkill
    from app.skills.qlib.backtest_skill import QlibBacktestSkill
    from app.skills.qlib.portfolio_skill import PortfolioOptimizationSkill

    for skill_class in [
        # Market data
        GetStockQuoteSkill,
        GetStockHistorySkill,
        GetStockInfoSkill,
        GetStockFinancialsSkill,
        SearchStocksSkill,
        GetInstitutionalHoldersSkill,
        GetFundHoldingsCnSkill,
        GetNorthboundHoldingSkill,
        GetSectorIndustrySkill,
        GetAnalystRatingsSkill,
        GetMarketContextSkill,
        # Computation
        CalculateTechnicalIndicatorsSkill,
        CalculateHistorySummarySkill,
        ScoreNewsArticlesSkill,
        # News
        GetNewsSkill,
        FetchGlobalNewsSkill,
        FetchFullContentSkill,
        DeepFilterNewsSkill,
        # User data
        GetPortfolioSkill,
        GetWatchlistSkill,
        # Knowledge
        SearchKnowledgeBaseSkill,
        EmbedDocumentSkill,
        # Quantitative (Qlib)
        QlibFactorSkill,
        QlibExpressionSkill,
        QlibBacktestSkill,
        PortfolioOptimizationSkill,
    ]:
        registry.register(skill_class())

    logger.info("Skill registry initialized with %d skills", len(registry))
