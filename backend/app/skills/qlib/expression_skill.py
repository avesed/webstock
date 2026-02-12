"""Qlib expression evaluation skill for LLM agents.

This is the "dynamic quantitative calculator" -- LLM agents can construct
Qlib expressions on the fly to compute custom technical indicators.
"""
import logging
from typing import Any

from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult
from app.services.qlib_client import get_qlib_client, QlibServiceError

logger = logging.getLogger(__name__)


class QlibExpressionSkill(BaseSkill):
    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="qlib_evaluate_expression",
            description=(
                "Evaluate a Qlib expression for a stock. This is a dynamic quantitative calculator. "
                "Operators: Ref(x,n), Mean(x,n), Std(x,n), Corr(x,y,n), EMA(x,n), Slope(x,n), "
                "Delta(x,n), Rank(x), Abs(x), Log(x), If(cond,x,y), Greater(x,y), Less(x,y), "
                "Min(x,n), Max(x,n), Sum(x,n). "
                "Variables: $open, $high, $low, $close, $volume. "
                "Example: Corr($close,$volume,20) computes 20-day price-volume correlation."
            ),
            category="quantitative",
            parameters=[
                SkillParameter(
                    name="symbol",
                    type="string",
                    description="Stock symbol (e.g., AAPL, 600000.SS)",
                ),
                SkillParameter(
                    name="expression",
                    type="string",
                    description="Qlib expression to evaluate. Use $close, $open, $high, $low, $volume as base variables.",
                ),
                SkillParameter(
                    name="market",
                    type="string",
                    description="Market code",
                    required=False,
                    default="us",
                    enum=["us", "hk", "cn", "sh", "sz", "metal"],
                ),
                SkillParameter(
                    name="period",
                    type="string",
                    description="Lookback period",
                    required=False,
                    default="3mo",
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        symbol = kwargs.get("symbol", "")
        expression = kwargs.get("expression", "")
        market = kwargs.get("market", "us")
        period = kwargs.get("period", "3mo")

        if not symbol:
            return SkillResult(success=False, error="symbol is required")
        if not expression:
            return SkillResult(success=False, error="expression is required")

        try:
            client = await get_qlib_client()
            result = await client.evaluate_expression(symbol, expression, market, period=period)
            return SkillResult(success=True, data=result)
        except QlibServiceError as e:
            logger.warning("qlib_evaluate_expression failed: %s", e)
            return SkillResult(success=False, error=str(e))
        except Exception as e:
            logger.error("qlib_evaluate_expression unexpected: %s", e)
            return SkillResult(success=False, error=f"Expression evaluation failed: {e}")
