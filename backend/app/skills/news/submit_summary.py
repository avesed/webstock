"""Skill: submit news summary generation results.

Pure output skill — validates and passes through the LLM's summary.
Used with ``tool_choice="required"`` to guarantee structured JSON output.
"""

from __future__ import annotations

from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult


class SubmitSummarySkill(BaseSkill):
    """Accept summary generation results from the LLM."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="submit_summary",
            description=(
                "Submit the investment summary and detailed summary "
                "for a news article."
            ),
            category="news",
            parameters=[
                SkillParameter(
                    name="investment_summary",
                    type="string",
                    description=(
                        "One-sentence summary for card preview, max 50 characters"
                    ),
                    required=True,
                ),
                SkillParameter(
                    name="detailed_summary",
                    type="string",
                    description=(
                        "Comprehensive summary preserving all key details, data, "
                        "timelines, and causal relationships. 5-20 sentences."
                    ),
                    required=True,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        investment_summary: str = (kwargs.get("investment_summary") or "").strip()
        detailed_summary: str = (kwargs.get("detailed_summary") or "").strip()

        # Enforce length limit on investment_summary
        if len(investment_summary) > 80:
            investment_summary = investment_summary[:77] + "..."

        return SkillResult(
            success=True,
            data={
                "investment_summary": investment_summary,
                "detailed_summary": detailed_summary,
            },
        )
