"""Skill: submit impact assessment results.

Pure output skill — validates and passes through the LLM's impact analysis.
Used with ``tool_choice="required"`` to guarantee structured JSON output.
"""

from __future__ import annotations

from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult


class SubmitImpactSkill(BaseSkill):
    """Accept impact assessment results from the LLM."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="submit_impact",
            description=(
                "Submit the impact assessment for a news article, covering "
                "market, sector, and stock-level impacts."
            ),
            category="news",
            parameters=[
                SkillParameter(
                    name="market_impact",
                    type="string",
                    description="Analysis of impact on the overall market",
                    required=True,
                ),
                SkillParameter(
                    name="sector_impact",
                    type="string",
                    description="Analysis of impact on related industry sectors",
                    required=True,
                ),
                SkillParameter(
                    name="stock_impact",
                    type="string",
                    description="Analysis of impact on specific stocks",
                    required=True,
                ),
                SkillParameter(
                    name="time_horizon",
                    type="string",
                    description="Primary time dimension of the impact",
                    required=True,
                    enum=["short_term", "medium_term", "long_term"],
                ),
                SkillParameter(
                    name="impact_magnitude",
                    type="string",
                    description="Overall impact intensity",
                    required=True,
                    enum=["high", "medium", "low"],
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        time_horizon = kwargs.get("time_horizon", "short_term")
        if time_horizon not in ("short_term", "medium_term", "long_term"):
            time_horizon = "short_term"

        impact_magnitude = kwargs.get("impact_magnitude", "medium")
        if impact_magnitude not in ("high", "medium", "low"):
            impact_magnitude = "medium"

        return SkillResult(
            success=True,
            data={
                "market_impact": kwargs.get("market_impact", ""),
                "sector_impact": kwargs.get("sector_impact", ""),
                "stock_impact": kwargs.get("stock_impact", ""),
                "time_horizon": time_horizon,
                "impact_magnitude": impact_magnitude,
            },
        )
