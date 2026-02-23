"""Discussion agent-as-skill classes.

These skills wrap discussion agents as callable tools for the chat service,
allowing users to ask follow-up questions to specific experts after a discussion.
Also includes DispatchRoundSkill for moderator-controlled discussion flow.
"""

import logging
from typing import Any, Dict, List

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


class _BaseDiscussionExpertSkill(BaseSkill):
    """Base class for discussion expert skills."""

    _agent_type: str = ""
    _skill_name: str = ""
    _description: str = ""
    _role_zh: str = ""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name=self._skill_name,
            description=self._description,
            category="discussion",
            parameters=[
                SkillParameter(
                    name="question",
                    type="string",
                    description="The question to ask this expert.",
                    required=True,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        """Execute the expert skill.

        Requires 'db' and 'discussion_session_id' injected by chat_adapter.
        """
        question = kwargs.get("question", "")
        db = kwargs.get("db")
        discussion_session_id = kwargs.get("discussion_session_id")

        if not question:
            return SkillResult(success=False, error="Question is required")
        if not discussion_session_id:
            return SkillResult(
                success=False,
                error="This skill is only available in discussion chat conversations",
            )

        try:
            from sqlalchemy import select
            from app.models.discussion import DiscussionSession, DiscussionMessage
            from app.core.llm import get_discussion_langchain_model
            from langchain_core.messages import SystemMessage, HumanMessage

            # Verify session ownership (fail-closed: require user_id)
            user_id = kwargs.get("user_id")
            if not user_id:
                return SkillResult(success=False, error="User context required")
            session_check = await db.execute(
                select(DiscussionSession.id).where(
                    DiscussionSession.id == discussion_session_id,
                    DiscussionSession.user_id == user_id,
                )
            )
            if not session_check.scalar_one_or_none():
                return SkillResult(success=False, error="Discussion session not found")

            # Load discussion messages for this agent + synthesis
            result = await db.execute(
                select(DiscussionMessage)
                .where(
                    DiscussionMessage.session_id == discussion_session_id,
                    DiscussionMessage.agent_type.in_([self._agent_type, "synthesis", "moderator"]),
                )
                .order_by(DiscussionMessage.created_at)
            )
            messages = result.scalars().all()

            # Build context from discussion messages
            context_parts = []
            for msg in messages:
                label = msg.agent_type
                if msg.agent_type == self._agent_type:
                    label = self._role_zh
                elif msg.agent_type == "synthesis":
                    label = "综合报告"
                elif msg.agent_type == "moderator":
                    label = "综合专家"
                context_parts.append(f"[{label}] {msg.content[:800]}")

            discussion_context = "\n\n".join(context_parts[-10:])  # Last 10 messages

            prompt = (
                f"你是{self._role_zh}。以下是之前讨论的上下文：\n\n"
                f"{discussion_context}\n\n"
                f"用户提问：{question}\n\n"
                f"请基于你在讨论中的分析和专业知识回答。保持简洁但有深度。"
            )

            llm = await get_discussion_langchain_model(db_session=db)
            result = await llm.ainvoke([SystemMessage(content=prompt)])
            content = result.content if hasattr(result, "content") else str(result)

            return SkillResult(success=True, data=content)

        except Exception as e:
            logger.exception("Discussion expert skill %s failed: %s", self._skill_name, e)
            return SkillResult(success=False, error=str(e)[:200])


class AskFundamentalExpertSkill(_BaseDiscussionExpertSkill):
    _agent_type = "fundamental"
    _skill_name = "ask_fundamental_expert"
    _description = "向基本面分析专家提问（仅在讨论对话中可用）"
    _role_zh = "基本面专家"


class AskTechnicalExpertSkill(_BaseDiscussionExpertSkill):
    _agent_type = "technical"
    _skill_name = "ask_technical_expert"
    _description = "向技术面专家提问（仅在讨论对话中可用）"
    _role_zh = "技术面专家"


class AskSentimentExpertSkill(_BaseDiscussionExpertSkill):
    _agent_type = "sentiment"
    _skill_name = "ask_sentiment_expert"
    _description = "向情绪面专家提问（仅在讨论对话中可用）"
    _role_zh = "情绪面专家"


class AskNewsExpertSkill(_BaseDiscussionExpertSkill):
    _agent_type = "news"
    _skill_name = "ask_news_expert"
    _description = "向新闻面专家提问（仅在讨论对话中可用）"
    _role_zh = "新闻面专家"


# ---------------------------------------------------------------------------
# Moderator dispatch skill
# ---------------------------------------------------------------------------

# Allowlist of skills the moderator can request as additional data
_DISPATCH_ALLOWED_SKILLS = {
    "get_stock_quote", "get_stock_info", "get_stock_financials",
    "get_stock_history", "get_analyst_ratings", "get_news",
    "get_institutional_holders", "get_fund_holdings_cn",
    "get_northbound_holding", "get_sector_industry",
    "get_market_context", "qlib_compute_factors",
}


class DispatchRoundSkill(BaseSkill):
    """Executable dispatch tool for the moderator to control discussion flow.

    The moderator calls this via bind_tools() to:
    1. Decide whether to continue debate or conclude for synthesis
    2. Select which agents respond next and what topics to focus on
    3. Optionally request additional data not in the initial pre-fetch

    Runtime context (symbol, market, shared_data) is injected as kwargs
    by moderator_review_node, following the same pattern as expert skills
    receiving db/discussion_session_id from chat_adapter.
    """

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="dispatch_round",
            description=(
                "Control the discussion flow: continue debate with targeted agents, "
                "or conclude for synthesis. Optionally request additional data."
            ),
            category="discussion",
            parameters=[
                SkillParameter(
                    name="action",
                    type="string",
                    description="'direct_to_agent' to continue debate, 'conclude' to move to synthesis",
                    required=True,
                    enum=["direct_to_agent", "conclude"],
                ),
                SkillParameter(
                    name="target_agents",
                    type="array",
                    required=False,
                    description="Which agents should respond next (order by conversation flow)",
                    items={"type": "string", "enum": ["fundamental", "technical", "sentiment", "news"]},
                ),
                SkillParameter(
                    name="focus_topics",
                    type="array",
                    required=False,
                    description="Specific topics or questions for the next round",
                    items={"type": "string"},
                ),
                SkillParameter(
                    name="data_requests",
                    type="array",
                    required=False,
                    description="Additional data skill names to fetch (e.g. 'get_institutional_holders')",
                    items={"type": "string"},
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        action = kwargs.get("action", "conclude")
        target_agents: List[str] = kwargs.get("target_agents") or []
        focus_topics: List[str] = kwargs.get("focus_topics") or []
        data_requests: List[str] = kwargs.get("data_requests") or []

        # Runtime context injected by moderator_review_node
        symbol: str = kwargs.get("symbol", "")
        market: str = kwargs.get("market", "")
        shared_data: Dict[str, Any] = kwargs.get("shared_data") or {}

        # Validate target_agents
        valid_agents = {"fundamental", "technical", "sentiment", "news"}
        target_agents = [a for a in target_agents if a in valid_agents]

        result: Dict[str, Any] = {
            "action": action,
            "target_agents": target_agents,
            "focus_topics": focus_topics,
            "data_requests": data_requests,
        }

        # Fetch requested data if any (time-budgeted to avoid blocking discussion)
        if data_requests and symbol:
            import time as _time

            from app.skills.registry import get_skill_registry

            registry = get_skill_registry()
            updated = dict(shared_data)
            fetched = 0
            budget_start = _time.time()

            for skill_name in data_requests:
                # Overall time budget for all data requests
                if _time.time() - budget_start > 45:
                    logger.warning("dispatch_round: 数据请求超时预算, 跳过剩余技能")
                    break
                if skill_name not in _DISPATCH_ALLOWED_SKILLS:
                    logger.warning("dispatch_round: 跳过不允许的技能 %s", skill_name)
                    continue
                # Skip if already present
                if any(k.startswith(f"{skill_name}|") for k in updated):
                    continue
                skill = registry.get(skill_name)
                if not skill:
                    logger.warning("dispatch_round: 技能 %s 已允许但未在注册表中", skill_name)
                    continue
                skill_kwargs: Dict[str, Any] = {"symbol": symbol, "market": market}
                if skill_name == "get_stock_history":
                    skill_kwargs.update({"period": "1y", "interval": "1d"})
                try:
                    sr = await skill.safe_execute(timeout=15.0, **skill_kwargs)
                    cache_key = f"{skill_name}|symbol={symbol}|market={market}"
                    updated[cache_key] = sr
                    if sr.success:
                        fetched += 1
                        logger.info("dispatch_round: 额外数据获取成功 %s", skill_name)
                except Exception as e:
                    logger.warning("dispatch_round: 额外数据获取失败 %s: %s", skill_name, e)

            if fetched > 0:
                result["updated_shared_data"] = updated
                result["fetched_count"] = fetched

        return SkillResult(success=True, data=result)
