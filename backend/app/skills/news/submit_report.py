"""Skill: submit analysis report results.

Pure output skill — validates and passes through the LLM's report.
Used with ``tool_choice="required"`` to guarantee structured JSON output.

This is the highest-value conversion: Markdown strings inside JSON are the
most frequent source of ``extract_json_from_response`` failures (unescaped
quotes, newlines, code blocks).  Tool call arguments handle these natively.
"""

from __future__ import annotations

from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult


class SubmitReportSkill(BaseSkill):
    """Accept analysis report results from the LLM."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="submit_report",
            description=(
                "Submit the professional analysis report for a news article. "
                "The report should be a Markdown-formatted string."
            ),
            category="news",
            parameters=[
                SkillParameter(
                    name="analysis_report",
                    type="string",
                    description=(
                        "Complete Markdown analysis report with 6 sections: "
                        "核心解读, 投资洞察, 风险分析, 市场影响, 情绪指数, 专业信息. "
                        "Use \\n for line breaks within the string."
                    ),
                    required=True,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        analysis_report: str = (kwargs.get("analysis_report") or "").strip()

        return SkillResult(
            success=True,
            data={
                "analysis_report": analysis_report,
            },
        )
