"""Run rolling retrain backtest to simulate production model behavior."""
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


class MLRollingBacktestSkill(BaseSkill):
    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="ml_run_rolling_backtest",
            description=(
                "Run a rolling retrain backtest that retrains the model every N "
                "trading days during the validation window, simulating production "
                "behavior. Returns task_id immediately (async). The agent will be "
                "suspended until the backtest completes."
            ),
            category="prediction",
            parameters=[
                SkillParameter(
                    name="market",
                    type="string",
                    description="Market code (us, cn, hk)",
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
                SkillParameter(
                    name="retrain_interval",
                    type="integer",
                    description=(
                        "Retrain every N trading days (default 5 = weekly). "
                        "Smaller = more realistic but slower."
                    ),
                    required=False,
                    default=5,
                ),
                SkillParameter(
                    name="learning_rate",
                    type="number",
                    description="LightGBM learning rate",
                    required=False,
                ),
                SkillParameter(
                    name="num_leaves",
                    type="integer",
                    description="Max number of leaves per tree",
                    required=False,
                ),
                SkillParameter(
                    name="min_child_samples",
                    type="integer",
                    description="Min data points per leaf",
                    required=False,
                ),
                SkillParameter(
                    name="lambda_l2",
                    type="number",
                    description="L2 regularization strength",
                    required=False,
                ),
                SkillParameter(
                    name="feature_fraction",
                    type="number",
                    description="Feature sampling ratio per tree",
                    required=False,
                ),
                SkillParameter(
                    name="bagging_fraction",
                    type="number",
                    description="Data sampling ratio per iteration",
                    required=False,
                ),
                SkillParameter(
                    name="num_boost_round",
                    type="integer",
                    description="Max boosting iterations",
                    required=False,
                ),
                SkillParameter(
                    name="early_stopping_rounds",
                    type="integer",
                    description="Early stopping patience",
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
        validation_days = kwargs.get("validation_days", 60)
        forward_days = kwargs.get("forward_days", 5)
        retrain_interval = kwargs.get("retrain_interval", 5)

        if not market or not cutoff_date:
            return SkillResult(
                success=False, error="market and cutoff_date are required"
            )

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
            result = await client.ml_submit_rolling_backtest(
                market=market,
                cutoff_date=cutoff_date,
                validation_days=validation_days,
                forward_days=forward_days,
                retrain_interval=retrain_interval,
                config=config,
            )
            return SkillResult(success=True, data=result)
        except PredictionServiceError as e:
            logger.warning(
                "ML rolling backtest submission failed: %s", e
            )
            return SkillResult(
                success=False, error=f"Rolling backtest failed: {e}"
            )
        except Exception as e:
            logger.error(
                "ML rolling backtest unexpected error: %s", e, exc_info=True
            )
            return SkillResult(success=False, error=str(e))
