"""Portfolio optimization skill for LLM agents and chat.

Wraps PyPortfolioOpt to provide portfolio optimization capabilities.
This skill runs in the main backend process (not qlib-service).
"""

import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


class PortfolioOptimizationSkill(BaseSkill):
    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="optimize_portfolio",
            description=(
                "Optimize a stock portfolio using mean-variance optimization (PyPortfolioOpt). "
                "Given a list of stock symbols, computes optimal weights using the specified method. "
                "Methods: max_sharpe (maximize Sharpe ratio), min_volatility (minimize risk), "
                "risk_parity (equal risk contribution), efficient_return (target a specific return)."
            ),
            category="quantitative",
            parameters=[
                SkillParameter(
                    name="symbols",
                    type="array",
                    description="List of stock symbols to optimize (e.g., ['AAPL', 'MSFT', 'GOOGL']). Minimum 2.",
                    items={"type": "string"},
                ),
                SkillParameter(
                    name="method",
                    type="string",
                    description="Optimization method",
                    required=False,
                    default="max_sharpe",
                    enum=["max_sharpe", "min_volatility", "risk_parity", "efficient_return"],
                ),
                SkillParameter(
                    name="lookback_days",
                    type="integer",
                    description="Number of trading days of historical data to use (default: 252 = 1 year)",
                    required=False,
                    default=252,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        from app.services.portfolio_optimization import (
            PortfolioOptimizationError,
            PortfolioOptimizationService,
        )

        symbols = kwargs.get("symbols", [])
        method = kwargs.get("method", "max_sharpe")
        lookback_days = kwargs.get("lookback_days", 252)

        if not symbols or not isinstance(symbols, list) or len(symbols) < 2:
            return SkillResult(success=False, error="symbols must be a list of at least 2 stock symbols")

        try:
            result = await PortfolioOptimizationService.optimize(
                symbols=symbols,
                method=method,
                lookback_days=int(lookback_days),
            )
            return SkillResult(success=True, data=result)
        except PortfolioOptimizationError as e:
            logger.warning("optimize_portfolio failed: %s", e)
            return SkillResult(success=False, error=str(e))
        except Exception as e:
            logger.error("optimize_portfolio unexpected error: %s", e)
            return SkillResult(success=False, error=f"Portfolio optimization failed: {e}")
