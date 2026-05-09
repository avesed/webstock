"""Profile data statistics for ML training."""
from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


class MLProfileSkill(BaseSkill):
    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="ml_profile_data",
            description=(
                "Profile the feature matrix for a market: NaN rates, return stats, "
                "sector distribution, and current MarketConfig baseline. Use this "
                "first to understand data before generating training config."
            ),
            category="prediction",
            parameters=[
                SkillParameter(
                    name="market",
                    type="string",
                    description="Market code",
                    enum=["us", "cn", "hk"],
                ),
                SkillParameter(
                    name="cutoff_date",
                    type="string",
                    description="Training cutoff date (YYYY-MM-DD)",
                ),
                SkillParameter(
                    name="validation_days",
                    type="integer",
                    description="Number of validation trading days",
                    required=False,
                    default=60,
                ),
                SkillParameter(
                    name="forward_days",
                    type="integer",
                    description="Forward prediction horizon in days",
                    required=False,
                    default=5,
                ),
            ],
            admin_only=True,
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        market = kwargs.get("market", "")
        cutoff_date = kwargs.get("cutoff_date", "")
        validation_days = kwargs.get("validation_days", 60)
        forward_days = kwargs.get("forward_days", 5)

        if not market or not cutoff_date:
            return SkillResult(success=False, error="market and cutoff_date are required")

        try:
            from app.services.alphaforge_client import (
                AlphaForgeServiceError,
                get_alphaforge_client,
            )

            client = await get_alphaforge_client()
            result = await client.ml_profile_data(
                market, cutoff_date, validation_days, forward_days,
            )
            return SkillResult(success=True, data=result)
        except AlphaForgeServiceError as e:
            logger.warning("ML profile failed for %s: %s", market, e)
            return SkillResult(success=False, error=f"Profile failed: {e}")
        except Exception as e:
            logger.error("ML profile unexpected error: %s", e, exc_info=True)
            return SkillResult(success=False, error=str(e))
