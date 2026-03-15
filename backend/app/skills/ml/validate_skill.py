"""Run out-of-sample validation on trained ML models."""
from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


class MLValidateSkill(BaseSkill):
    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="ml_run_validation",
            description=(
                "Run out-of-sample validation on trained models. Performs multi-day "
                "inference on dates after cutoff and computes IC, ICIR, quintile "
                "spread, direction accuracy, and hit rate."
            ),
            category="prediction",
            parameters=[
                SkillParameter(
                    name="task_id",
                    type="string",
                    description="Training task ID of the completed training run",
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
        task_id = kwargs.get("task_id", "")
        cutoff_date = kwargs.get("cutoff_date", "")
        validation_days = kwargs.get("validation_days", 60)
        forward_days = kwargs.get("forward_days", 5)

        if not task_id or not cutoff_date:
            return SkillResult(success=False, error="task_id and cutoff_date are required")

        try:
            from app.services.prediction_client import (
                PredictionServiceError,
                get_prediction_client,
            )

            client = await get_prediction_client()
            result = await client.ml_run_validation(
                task_id, cutoff_date, validation_days, forward_days,
            )
            return SkillResult(success=True, data=result)
        except PredictionServiceError as e:
            logger.warning("ML validation failed for task %s: %s", task_id, e)
            return SkillResult(success=False, error=f"Validation failed: {e}")
        except Exception as e:
            logger.error("ML validation unexpected error: %s", e, exc_info=True)
            return SkillResult(success=False, error=str(e))
