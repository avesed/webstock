"""Check ML training task status."""
from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


class MLTrainingStatusSkill(BaseSkill):
    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="ml_get_training_status",
            description=(
                "Check the status of a training task. Returns status "
                "(submitted/training/completed/failed), progress percentage, "
                "and training results (IC, ICIR, fold ICs) once completed."
            ),
            category="prediction",
            parameters=[
                SkillParameter(
                    name="task_id",
                    type="string",
                    description="Training task ID returned by ml_submit_training",
                ),
            ],
            admin_only=True,
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        task_id = kwargs.get("task_id", "")

        if not task_id:
            return SkillResult(success=False, error="task_id is required")

        try:
            from app.services.alphaforge_client import (
                AlphaForgeServiceError,
                get_alphaforge_client,
            )

            client = await get_alphaforge_client()
            result = await client.ml_get_training_task(task_id)
            return SkillResult(success=True, data=result)
        except AlphaForgeServiceError as e:
            logger.warning("ML training status check failed for %s: %s", task_id, e)
            return SkillResult(success=False, error=f"Training status check failed: {e}")
        except Exception as e:
            logger.error("ML training status unexpected error: %s", e, exc_info=True)
            return SkillResult(success=False, error=str(e))
