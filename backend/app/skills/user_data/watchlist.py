"""Skill: get the user's default watchlist symbols."""

from __future__ import annotations

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillResult

logger = logging.getLogger(__name__)


class GetWatchlistSkill(BaseSkill):
    """Fetch the current user's default watchlist and its stock symbols."""

    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="get_watchlist",
            description=(
                "Get the user's watchlist symbols. Use when the user "
                "asks about stocks they are watching."
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
        from sqlalchemy.orm import selectinload
        from app.models.watchlist import Watchlist

        result = await db.execute(
            select(Watchlist)
            .where(
                Watchlist.user_id == user_id,
                Watchlist.is_default == True,  # noqa: E712
            )
            .options(selectinload(Watchlist.items))
        )
        watchlist = result.scalar_one_or_none()

        if not watchlist or not watchlist.items:
            return SkillResult(
                success=True,
                data={"info": "Watchlist is empty."},
            )

        symbols = [item.symbol for item in watchlist.items]

        return SkillResult(
            success=True,
            data={"watchlist": watchlist.name, "symbols": symbols},
            metadata={"symbol_count": len(symbols)},
        )
