"""Skill: get institutional holders data (US/HK markets via yfinance)."""

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


class GetInstitutionalHoldersSkill(BaseSkill):
    """Fetch institutional holders data for US and HK stocks via yfinance."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="get_institutional_holders",
            description=(
                "Get institutional holders data for a stock, including major "
                "institutional shareholders and their positions. "
                "Primarily available for US and HK markets."
            ),
            category="market_data",
            parameters=[
                SkillParameter(
                    name="symbol",
                    type="string",
                    description="Stock ticker (e.g. AAPL, 0700.HK)",
                    required=True,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        symbol = _normalize_symbol(kwargs.get("symbol"))

        from app.services.providers import get_provider_router

        router = await get_provider_router()
        result = await router.yfinance.get_institutional_holders(symbol)

        if not result:
            return SkillResult(
                success=False,
                error=f"No institutional holders data available for {symbol}",
            )

        return SkillResult(success=True, data=result)
