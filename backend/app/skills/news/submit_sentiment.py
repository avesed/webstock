"""Skill: submit sentiment and tag analysis results.

Pure output skill — validates and passes through the LLM's sentiment
analysis.  Used with ``tool_choice="required"`` to guarantee structured
JSON output without ``extract_json_from_response``.
"""

from __future__ import annotations

from typing import Any, List

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

_INDUSTRY_TAGS = [
    "tech", "finance", "healthcare", "energy", "consumer",
    "industrial", "materials", "utilities", "realestate", "telecom",
]
_EVENT_TAGS = [
    "earnings", "merger", "ipo", "regulatory", "executive",
    "product", "lawsuit", "dividend", "buyback", "guidance", "macro",
]


class SubmitSentimentSkill(BaseSkill):
    """Accept sentiment and tag analysis results from the LLM."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="submit_sentiment",
            description=(
                "Submit the sentiment and tag analysis results for a news article."
            ),
            category="news",
            parameters=[
                SkillParameter(
                    name="sentiment",
                    type="string",
                    description="Overall market sentiment of the news",
                    required=True,
                    enum=["bullish", "bearish", "neutral"],
                ),
                SkillParameter(
                    name="industry_tags",
                    type="array",
                    description="Relevant industry sectors (max 5)",
                    required=True,
                    items={"type": "string", "enum": _INDUSTRY_TAGS},
                ),
                SkillParameter(
                    name="event_tags",
                    type="array",
                    description="Event type tags (max 5)",
                    required=True,
                    items={"type": "string", "enum": _EVENT_TAGS},
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        sentiment: str = kwargs.get("sentiment", "neutral")
        industry_tags: List[str] = (kwargs.get("industry_tags") or [])[:5]
        event_tags: List[str] = (kwargs.get("event_tags") or [])[:5]

        # Validate sentiment enum
        if sentiment not in ("bullish", "bearish", "neutral"):
            sentiment = "neutral"

        return SkillResult(
            success=True,
            data={
                "sentiment": sentiment,
                "industry_tags": industry_tags,
                "event_tags": event_tags,
            },
        )
