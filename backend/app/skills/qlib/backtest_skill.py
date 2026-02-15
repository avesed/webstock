"""Qlib backtest creation skill for LLM agents and chat.

Allows LLM agents to create backtests on behalf of users.  The skill
proxies to qlib-service via the QlibClient and records the backtest
in the local PostgreSQL database.

Because backtests are user-scoped and require DB access, the `execute()`
method expects `user_id` (int) and `db` (AsyncSession) to be injected
at call-time (same pattern as portfolio / watchlist skills).
"""

import logging
from typing import Any

from app.schemas.qlib import BacktestCreateRequest, QlibMarket, QlibStrategyType
from app.services.backtest_service import BacktestManagementService
from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


class QlibBacktestSkill(BaseSkill):
    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="qlib_create_backtest",
            description=(
                "Create a quantitative backtest using Qlib. "
                "Runs a strategy (top-K stock selection, signal-based, or long-short) "
                "over a stock pool for a given date range. Returns a backtest id "
                "that can be polled for progress and results."
            ),
            category="quantitative",
            parameters=[
                SkillParameter(
                    name="name",
                    type="string",
                    description="Descriptive name for the backtest (e.g., 'US Tech Top-10 2024')",
                ),
                SkillParameter(
                    name="symbols",
                    type="array",
                    description="List of stock symbols to include in the backtest pool (e.g., ['AAPL', 'MSFT', 'GOOGL'])",
                    items={"type": "string"},
                ),
                SkillParameter(
                    name="market",
                    type="string",
                    description="Market code for the symbols",
                    required=False,
                    default="us",
                    enum=["us", "hk", "cn", "sh", "sz", "metal"],
                ),
                SkillParameter(
                    name="start_date",
                    type="string",
                    description="Backtest start date in YYYY-MM-DD format",
                ),
                SkillParameter(
                    name="end_date",
                    type="string",
                    description="Backtest end date in YYYY-MM-DD format",
                ),
                SkillParameter(
                    name="strategy_type",
                    type="string",
                    description="Strategy type to run",
                    required=False,
                    default="topk",
                    enum=["topk", "signal", "long_short"],
                ),
                SkillParameter(
                    name="topk",
                    type="integer",
                    description="Number of top stocks to hold for topk strategy (default: 10)",
                    required=False,
                    default=10,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        name = kwargs.get("name", "")
        symbols = kwargs.get("symbols", [])
        market = kwargs.get("market", "us")
        start_date = kwargs.get("start_date", "")
        end_date = kwargs.get("end_date", "")
        strategy_type = kwargs.get("strategy_type", "topk")
        topk = kwargs.get("topk", 10)

        # Injected context
        user_id = kwargs.get("user_id")
        db = kwargs.get("db")

        # Validate required fields
        if not name:
            return SkillResult(success=False, error="name is required")
        if not symbols or not isinstance(symbols, list):
            return SkillResult(success=False, error="symbols must be a non-empty list")
        if not start_date:
            return SkillResult(success=False, error="start_date is required (YYYY-MM-DD)")
        if not end_date:
            return SkillResult(success=False, error="end_date is required (YYYY-MM-DD)")
        if user_id is None:
            return SkillResult(success=False, error="user_id is required (internal)")
        if db is None:
            return SkillResult(success=False, error="db session is required (internal)")

        try:
            # Build strategy_config from shorthand params
            strategy_config = {}
            if strategy_type == "topk" and topk:
                strategy_config["k"] = int(topk)

            request = BacktestCreateRequest(
                name=name,
                market=QlibMarket(market),
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                strategy_type=QlibStrategyType(strategy_type),
                strategy_config=strategy_config,
            )

            backtest = await BacktestManagementService.create_backtest(
                db, user_id, request,
            )

            return SkillResult(
                success=True,
                data={
                    "backtest_id": str(backtest.id),
                    "name": backtest.name,
                    "status": backtest.status,
                    "market": backtest.market,
                    "symbols_count": len(backtest.symbols) if backtest.symbols else 0,
                    "start_date": str(backtest.start_date),
                    "end_date": str(backtest.end_date),
                    "strategy_type": backtest.strategy_type,
                    "error": backtest.error_message,
                },
            )

        except ValueError as e:
            logger.warning("qlib_create_backtest validation error: %s", e)
            return SkillResult(success=False, error=f"Invalid parameter: {e}")
        except Exception as e:
            logger.error("qlib_create_backtest unexpected error: %s", e)
            return SkillResult(success=False, error=f"Backtest creation failed: {e}")
