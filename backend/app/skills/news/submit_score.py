"""Skill: submit all articles' Layer 1 scoring results in one call.

Pure output skill for Layer 1 batch scoring.  The LLM calls this tool
once per batch, submitting all articles' scores in a single ``results``
array.  Each element carries one article's index, tier, score, and reason.

Using ``tool_choice`` eliminates the ``response_format`` compatibility
issue (some proxies don't support ``json_object`` mode) and guarantees
valid JSON via the tool-call protocol.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult


class SubmitScoreSkill(BaseSkill):
    """Accept all articles' scoring results from the LLM in one call."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="submit_score",
            description=(
                "Submit scoring results for ALL articles in the batch. "
                "The results array must contain one entry per article."
            ),
            category="news",
            parameters=[
                SkillParameter(
                    name="results",
                    type="array",
                    description=(
                        "Array of scoring results, one per article in the batch."
                    ),
                    required=True,
                    items={
                        "type": "object",
                        "properties": {
                            "index": {
                                "type": "integer",
                                "description": "The article number (1-based) as shown in the batch.",
                            },
                            "tier": {
                                "type": "string",
                                "description": (
                                    "The tier name from the rubric "
                                    "(e.g. 极端, 重大, 重要, 一般, 边缘, 无关)."
                                ),
                            },
                            "score": {
                                "type": "integer",
                                "description": "Numeric score 0-100, within the tier's range.",
                            },
                            "reason": {
                                "type": "string",
                                "description": "Brief justification, max 20 characters.",
                            },
                        },
                        "required": ["index", "tier", "score", "reason"],
                    },
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        raw_results = kwargs.get("results", [])
        if not isinstance(raw_results, list):
            return SkillResult(success=False, error="results must be an array")

        validated: List[Dict[str, Any]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index", 0))
            except (ValueError, TypeError):
                index = 0

            tier = str(item.get("tier", "unknown"))[:20]

            try:
                score = int(item.get("score", 50))
                score = max(0, min(score, 100))
            except (ValueError, TypeError):
                score = 50

            reason = str(item.get("reason", ""))[:100]
            validated.append({
                "index": index,
                "tier": tier,
                "score": score,
                "reason": reason,
            })

        return SkillResult(success=True, data=validated)
