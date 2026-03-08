"""Training strategist agent — generates training configuration.

Takes a DataProfile + baseline MarketConfig and produces a TrainingConfig
(either confirming baseline or making targeted adjustments with reasoning).
"""

import dataclasses
import json
import logging
from pathlib import Path
from typing import Any

from app.services.market_config import MarketConfig, get_market_config
from app.services.ml_agents.llm_client import MLAgentClient, MLAgentError
from app.services.ml_agents.schemas import DataProfile, TrainingConfig

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "strategist_system.md"


class Strategist:
    """Generate training configuration from data profile."""

    def __init__(self, client: MLAgentClient | None = None):
        self._client = client or MLAgentClient()
        self._system_prompt: str | None = None

    def _get_system_prompt(self) -> str:
        if self._system_prompt is None:
            self._system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
        return self._system_prompt

    async def generate(
        self,
        profile: DataProfile,
        market: str,
        previous_evaluation: dict[str, Any] | None = None,
    ) -> TrainingConfig:
        """Generate training configuration.

        Args:
            profile: DataProfile from the Profiler.
            market: Market code.
            previous_evaluation: If this is a retry, the Evaluator's suggested_adjustments.

        Returns:
            TrainingConfig with all MarketConfig fields + reasoning.
        """
        baseline = get_market_config(market)
        baseline_dict = dataclasses.asdict(baseline)

        # Build LLM input
        llm_input: dict[str, Any] = {
            "market": market,
            "data_profile": {
                "regime_analysis": profile.regime_analysis,
                "data_quality_warnings": profile.data_quality_warnings,
            },
            "baseline_config": baseline_dict,
            "universe_size": profile.universe_size,
            "feature_count": len(profile.feature_nan_rates),
            "training_days": profile.n_trading_days,
        }

        if previous_evaluation:
            llm_input["previous_evaluation"] = previous_evaluation
            llm_input["instruction"] = (
                "This is a RETRY. The previous model was evaluated and the evaluator "
                "suggested specific adjustments. Apply them to the baseline config, "
                "incorporating the evaluator's feedback."
            )

        result = await self._client.chat_json(
            system_prompt=self._get_system_prompt(),
            user_content=json.dumps(llm_input),
            temperature=0.1,
            max_tokens=1500,
        )

        # Parse into TrainingConfig (clamp_values validator auto-corrects ranges)
        try:
            config = TrainingConfig(**result)
        except Exception as e:
            logger.warning(
                "Failed to parse strategist LLM response: %s. Raw keys: %s",
                e,
                list(result.keys()),
            )
            raise

        logger.info(
            "Strategist generated config for market=%s: %s",
            market,
            config.reasoning[:200],
        )

        # Log deviations from baseline
        for f in dataclasses.fields(baseline):
            field_name = f.name
            baseline_val = baseline_dict.get(field_name)
            config_val = getattr(config, field_name, None)
            if config_val is not None and baseline_val != config_val:
                logger.info(
                    "  Config deviation: %s: %s -> %s",
                    field_name,
                    baseline_val,
                    config_val,
                )

        return config


# Module singleton
strategist = Strategist()
