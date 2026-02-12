"""Qlib factor computation skill for LLM agents and analysis."""
import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult
from app.services.qlib_client import get_qlib_client, QlibServiceError

logger = logging.getLogger(__name__)


class QlibFactorSkill(BaseSkill):
    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="qlib_compute_factors",
            description="Compute quantitative factors (Alpha158) for a stock using Qlib. "
                        "Returns top factors ranked by z-score for the latest date.",
            category="quantitative",
            parameters=[
                SkillParameter(
                    name="symbol",
                    type="string",
                    description="Stock symbol (e.g., AAPL, 600000.SS)",
                ),
                SkillParameter(
                    name="market",
                    type="string",
                    description="Market code",
                    required=False,
                    default="us",
                    enum=["us", "hk", "cn", "sh", "sz", "metal"],
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        symbol = kwargs.get("symbol", "")
        market = kwargs.get("market", "us")
        if not symbol:
            return SkillResult(success=False, error="symbol is required")
        try:
            client = await get_qlib_client()
            result = await client.get_factor_summary(symbol, market)
            return SkillResult(success=True, data=result)
        except QlibServiceError as e:
            logger.warning("qlib_compute_factors failed for %s: %s", symbol, e)
            return SkillResult(success=False, error=str(e))
        except Exception as e:
            logger.error("qlib_compute_factors unexpected error: %s", e)
            return SkillResult(success=False, error=f"Qlib service error: {e}")
