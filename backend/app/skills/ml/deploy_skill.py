"""Deploy a validated ML config as the best backtest result."""
from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


class MLDeploySkill(BaseSkill):
    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="ml_deploy_config",
            description=(
                "Deploy a validated config as the best backtest result. Writes the "
                "effective config and metrics to the ml_backtests table."
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
                    name="backtest_id",
                    type="string",
                    description="Backtest ID to associate the deployment with",
                ),
                SkillParameter(
                    name="effective_config",
                    type="object",
                    description="The full effective MarketConfig used for training",
                ),
                SkillParameter(
                    name="iteration",
                    type="integer",
                    description="Iteration number of the training run",
                    required=False,
                    default=1,
                ),
                SkillParameter(
                    name="val_ic",
                    type="number",
                    description="Validation IC score",
                ),
                SkillParameter(
                    name="train_ic",
                    type="number",
                    description="Training IC score",
                    required=False,
                ),
                SkillParameter(
                    name="train_icir",
                    type="number",
                    description="Training ICIR score",
                    required=False,
                ),
            ],
            admin_only=True,
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        market = kwargs.get("market", "")
        backtest_id = kwargs.get("backtest_id", "")
        effective_config = kwargs.get("effective_config")
        iteration = kwargs.get("iteration", 1)
        val_ic = kwargs.get("val_ic", 0.0)
        train_ic = kwargs.get("train_ic")
        train_icir = kwargs.get("train_icir")

        if not market or not backtest_id:
            return SkillResult(success=False, error="market and backtest_id are required")
        if not isinstance(effective_config, dict):
            return SkillResult(success=False, error="effective_config must be a dict")

        try:
            from app.services.alphaforge_client import (
                AlphaForgeServiceError,
                get_alphaforge_client,
            )

            client = await get_alphaforge_client()
            result = await client.ml_deploy_config(
                market=market,
                backtest_id=backtest_id,
                effective_config=effective_config,
                iteration=iteration,
                val_ic=val_ic,
                train_ic=train_ic,
                train_icir=train_icir,
            )
            return SkillResult(success=True, data=result)
        except AlphaForgeServiceError as e:
            logger.warning("ML deploy failed for %s: %s", market, e)
            return SkillResult(success=False, error=f"Deploy failed: {e}")
        except Exception as e:
            logger.error("ML deploy unexpected error: %s", e, exc_info=True)
            return SkillResult(success=False, error=str(e))
