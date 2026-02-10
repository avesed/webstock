"""Skill: get the user's default portfolio summary."""

from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillResult

logger = logging.getLogger(__name__)


class GetPortfolioSkill(BaseSkill):
    """Fetch the current user's default portfolio summary including holdings and P&L."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="get_portfolio",
            description=(
                "Get the user's portfolio summary including holdings, "
                "total value, and performance. Use when the user asks "
                "about their portfolio."
            ),
            category="user_data",
            parameters=[],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        # user_id and db are injected by the chat adapter, not exposed as
        # SkillParameter entries.
        user_id = kwargs.get("user_id")
        db = kwargs.get("db")

        if user_id is None or db is None:
            return SkillResult(
                success=False,
                error="user_id and db must be provided by the caller",
            )

        from sqlalchemy import select
        from app.models.portfolio import Portfolio
        from app.services.portfolio_service import PortfolioService

        svc = PortfolioService(db)
        result = await db.execute(
            select(Portfolio).where(
                Portfolio.user_id == user_id,
                Portfolio.is_default == True,  # noqa: E712
            )
        )
        portfolio = result.scalar_one_or_none()

        if not portfolio:
            return SkillResult(
                success=True,
                data={"info": "No portfolio found. Create one in the Portfolio page."},
            )

        summary = await svc.get_portfolio_summary(portfolio)

        return SkillResult(
            success=True,
            data={
                "name": summary.portfolio_name,
                "currency": summary.currency,
                "total_cost": str(summary.total_cost),
                "total_market_value": (
                    str(summary.total_market_value) if summary.total_market_value else None
                ),
                "total_profit_loss": (
                    str(summary.total_profit_loss) if summary.total_profit_loss else None
                ),
                "total_profit_loss_percent": summary.total_profit_loss_percent,
                "holdings_count": summary.holdings_count,
                "day_change": (
                    str(summary.day_change) if summary.day_change else None
                ),
                "day_change_percent": summary.day_change_percent,
            },
        )
