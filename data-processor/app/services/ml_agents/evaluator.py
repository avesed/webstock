"""Model evaluator agent — decides deploy/retry/reject.

Evaluates training results (IC, ICIR, fold ICs, feature importance, etc.)
and makes a deployment decision with structured reasoning.
"""

import json
import logging
from pathlib import Path
from typing import Any

from app.services.ml_agents.llm_client import MLAgentClient, MLAgentError
from app.services.ml_agents.schemas import EvaluationResult

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "evaluator_system.md"


class Evaluator:
    """Evaluate training results and decide deployment action."""

    def __init__(self, client: MLAgentClient | None = None):
        self._client = client or MLAgentClient()
        self._system_prompt: str | None = None

    def _get_system_prompt(self) -> str:
        if self._system_prompt is None:
            self._system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
        return self._system_prompt

    async def evaluate(
        self,
        market: str,
        training_results: dict[str, Any],
        training_config: dict[str, Any],
        data_profile: dict[str, Any],
        quality_thresholds: dict[str, float],
        is_retry: bool = False,
    ) -> EvaluationResult:
        """Evaluate training results.

        Args:
            market: Market code.
            training_results: Dict with ic, icir, ndcg_at_5/10/20, fold_ics,
                best_iters, feature_importance_top20, psi.
            training_config: The TrainingConfig that was used.
            data_profile: Summary from DataProfile (regime_analysis, warnings, universe_size).
            quality_thresholds: Dict with min_ic and min_icir for this market.
            is_retry: Whether this is a retry evaluation (affects retry recommendation).

        Returns:
            EvaluationResult with decision, reasoning, adjustments, confidence.
        """
        llm_input: dict[str, Any] = {
            "market": market,
            "training_results": training_results,
            "training_config": training_config,
            "data_profile": data_profile,
            "quality_thresholds": quality_thresholds,
        }

        if is_retry:
            llm_input["is_retry"] = True
            llm_input["instruction"] = (
                "This model was trained after a retry with adjusted parameters. "
                "If it passes the threshold, prefer 'deploy'. "
                "If it still fails, prefer 'reject' — do not recommend another retry."
            )

        result = await self._client.chat_json(
            system_prompt=self._get_system_prompt(),
            user_content=json.dumps(llm_input, default=str),
            temperature=0.1,
            max_tokens=1500,
        )

        try:
            evaluation = EvaluationResult(**result)
        except Exception as e:
            logger.warning(
                "Failed to parse evaluator LLM response: %s. Raw keys: %s",
                e,
                list(result.keys()),
            )
            raise

        logger.info(
            "Evaluator decision for market=%s: %s (confidence=%.2f) — %s",
            market,
            evaluation.decision,
            evaluation.confidence,
            evaluation.reasoning[:200],
        )

        return evaluation


# Module singleton
evaluator = Evaluator()
