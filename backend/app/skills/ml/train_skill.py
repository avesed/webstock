"""Submit ML training task with custom LightGBM config."""
from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)

_LGB_KEYS = [
    "learning_rate", "num_leaves", "min_child_samples",
    "lambda_l2", "feature_fraction", "bagging_fraction",
]
_DIRECTION_LGB_KEYS = ["learning_rate", "num_leaves", "min_child_samples", "lambda_l2"]


class MLTrainSkill(BaseSkill):
    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="ml_submit_training",
            description=(
                "Submit a training task. Returns a task_id to poll for "
                "completion. Pass individual hyperparameters to override "
                "the market baseline — omitted params keep baseline values."
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
                    name="forward_days",
                    type="integer",
                    description="Forward prediction horizon in days",
                    required=False,
                    default=5,
                ),
                SkillParameter(
                    name="learning_rate",
                    type="number",
                    description="LightGBM learning rate (e.g. 0.01)",
                    required=False,
                ),
                SkillParameter(
                    name="num_leaves",
                    type="integer",
                    description="Max number of leaves per tree (e.g. 31)",
                    required=False,
                ),
                SkillParameter(
                    name="min_child_samples",
                    type="integer",
                    description="Min data points per leaf (e.g. 30)",
                    required=False,
                ),
                SkillParameter(
                    name="lambda_l2",
                    type="number",
                    description="L2 regularization strength (e.g. 1.0)",
                    required=False,
                ),
                SkillParameter(
                    name="feature_fraction",
                    type="number",
                    description="Feature sampling ratio per tree (e.g. 0.7)",
                    required=False,
                ),
                SkillParameter(
                    name="bagging_fraction",
                    type="number",
                    description="Data sampling ratio per iteration (e.g. 0.8)",
                    required=False,
                ),
                SkillParameter(
                    name="num_boost_round",
                    type="integer",
                    description="Max boosting iterations (e.g. 1000)",
                    required=False,
                ),
                SkillParameter(
                    name="early_stopping_rounds",
                    type="integer",
                    description="Early stopping patience (e.g. 100)",
                    required=False,
                ),
                SkillParameter(
                    name="direction_learning_rate",
                    type="number",
                    description="Direction model learning rate (default: US 0.005, CN/HK 0.01)",
                    required=False,
                ),
                SkillParameter(
                    name="direction_num_leaves",
                    type="integer",
                    description="Direction model max leaves per tree (default: 15)",
                    required=False,
                ),
                SkillParameter(
                    name="direction_min_child_samples",
                    type="integer",
                    description="Direction model min samples per leaf (default: 100)",
                    required=False,
                ),
                SkillParameter(
                    name="direction_lambda_l2",
                    type="number",
                    description="Direction model L2 regularization (default: 5.0)",
                    required=False,
                ),
            ],
            admin_only=True,
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        market = kwargs.get("market", "")
        cutoff_date = kwargs.get("cutoff_date", "")
        forward_days = kwargs.get("forward_days", 5)

        if not market or not cutoff_date:
            return SkillResult(success=False, error="market and cutoff_date are required")

        # Assemble config from flat params
        lgb_overrides = {k: kwargs[k] for k in _LGB_KEYS if kwargs.get(k) is not None}
        config: dict[str, Any] = {}
        if lgb_overrides:
            config["lgb_overrides"] = lgb_overrides
        for k in ("num_boost_round", "early_stopping_rounds"):
            if kwargs.get(k) is not None:
                config[k] = kwargs[k]

        # Direction model overrides
        direction_overrides = {}
        for k in _DIRECTION_LGB_KEYS:
            val = kwargs.get(f"direction_{k}")
            if val is not None:
                direction_overrides[k] = val
        if direction_overrides:
            config["direction_lgb_overrides"] = direction_overrides

        try:
            from app.services.prediction_client import (
                PredictionServiceError,
                get_prediction_client,
            )

            client = await get_prediction_client()
            result = await client.ml_submit_training(
                market, cutoff_date, forward_days, config,
            )
            return SkillResult(success=True, data=result)
        except PredictionServiceError as e:
            logger.warning("ML training submission failed for %s: %s", market, e)
            return SkillResult(success=False, error=f"Training submission failed: {e}")
        except Exception as e:
            logger.error("ML training submission unexpected error: %s", e, exc_info=True)
            return SkillResult(success=False, error=str(e))
