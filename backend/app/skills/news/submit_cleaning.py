"""Skill: submit content cleaning and image extraction results.

Pure output skill for Layer 2 content cleaning.  The LLM conservatively
cleans article text and extracts image insights, then calls this tool
with the results.

Using ``tool_choice="required"`` eliminates the ``response_format``
compatibility issue (some proxies don't support ``json_object`` mode)
and guarantees valid JSON via the tool-call protocol.
"""

from __future__ import annotations

from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult


class SubmitCleaningSkill(BaseSkill):
    """Accept content cleaning results from the LLM."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="submit_cleaning",
            description=(
                "Submit the cleaned article text and any insights extracted "
                "from embedded images."
            ),
            category="news",
            parameters=[
                SkillParameter(
                    name="cleaned_text",
                    type="string",
                    description=(
                        "Conservatively cleaned article text. Must be highly "
                        "similar to the original — only remove obvious junk "
                        "(ads, navigation, cookie banners, social buttons)."
                    ),
                    required=True,
                ),
                SkillParameter(
                    name="image_insights",
                    type="string",
                    description=(
                        "Key data extracted from embedded images (charts, tables, "
                        "rankings). Empty string if no images or no useful data."
                    ),
                    required=True,
                ),
                SkillParameter(
                    name="has_critical_visual_data",
                    type="boolean",
                    description=(
                        "True only if images contain critical data (financial charts, "
                        "tables with numbers) not present in the text."
                    ),
                    required=True,
                ),
                SkillParameter(
                    name="article_published_at",
                    type="string",
                    description=(
                        "Article publication datetime extracted from the text, "
                        "in ISO-8601 format (e.g. '2026-02-25T14:30:00+08:00'). "
                        "Empty string if no clear publication time can be identified."
                    ),
                    required=False,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        cleaned_text = (kwargs.get("cleaned_text") or "").strip()
        image_insights = (kwargs.get("image_insights") or "").strip()
        has_critical_visual_data = bool(kwargs.get("has_critical_visual_data", False))
        article_published_at = (kwargs.get("article_published_at") or "").strip()

        data = {
            "cleaned_text": cleaned_text,
            "image_insights": image_insights,
            "has_critical_visual_data": has_critical_visual_data,
        }
        if article_published_at:
            data["article_published_at"] = article_published_at

        return SkillResult(success=True, data=data)
