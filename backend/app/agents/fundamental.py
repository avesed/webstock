"""Fundamental analysis agent."""

import logging
from typing import Any, Dict, Optional

from app.agents.base import AgentType, BaseAgent
from app.agents.prompts.fundamental_prompt import (
    build_fundamental_prompt,
    get_system_prompt,
)
from app.core.circuit_breaker import CircuitBreaker
from app.core.token_bucket import TokenBucket
from app.services.stock_service import get_stock_service

logger = logging.getLogger(__name__)


class FundamentalAgent(BaseAgent):
    """
    Agent for fundamental analysis of stocks.

    Analyzes:
    - Valuation metrics (P/E, P/B, EV/EBITDA)
    - Profitability (margins, ROE, ROA)
    - Growth (revenue, earnings)
    - Balance sheet health (debt ratios)
    - Dividend analysis
    """

    def __init__(
        self,
        rate_limiter: Optional[TokenBucket] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        super().__init__(rate_limiter, circuit_breaker)

    @property
    def agent_type(self) -> AgentType:
        """Return the agent type."""
        return AgentType.FUNDAMENTAL

    def get_system_prompt(self, market: str, language: str = "en") -> str:
        """Get the system prompt for fundamental analysis."""
        return get_system_prompt(market, language)

    async def build_user_prompt(
        self,
        symbol: str,
        market: str,
        data: Dict[str, Any],
        language: str = "en",
    ) -> str:
        """Build the user prompt with financial data."""
        return build_fundamental_prompt(
            symbol=symbol,
            market=market,
            info=data.get("info"),
            financials=data.get("financials"),
            quote=data.get("quote"),
            language=language,
        )

    async def prepare_data(
        self,
        symbol: str,
        market: str,
    ) -> Dict[str, Any]:
        """
        Prepare financial data for analysis.

        Fetches:
        - Company information
        - Financial metrics
        - Current quote
        """
        stock_service = await get_stock_service()

        # Fetch data in parallel
        import asyncio

        info_task = stock_service.get_info(symbol)
        financials_task = stock_service.get_financials(symbol)
        quote_task = stock_service.get_quote(symbol)

        info, financials, quote = await asyncio.gather(
            info_task,
            financials_task,
            quote_task,
            return_exceptions=True,
        )

        # Handle exceptions
        if isinstance(info, Exception):
            logger.warning(f"Failed to get info for {symbol}: {info}")
            info = None
        if isinstance(financials, Exception):
            logger.warning(f"Failed to get financials for {symbol}: {financials}")
            financials = None
        if isinstance(quote, Exception):
            logger.warning(f"Failed to get quote for {symbol}: {quote}")
            quote = None

        return {
            "info": info,
            "financials": financials,
            "quote": quote,
        }


# Factory function for creating agent with shared resources
async def create_fundamental_agent(
    rate_limiter: Optional[TokenBucket] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
) -> FundamentalAgent:
    """Create a fundamental analysis agent."""
    return FundamentalAgent(
        rate_limiter=rate_limiter,
        circuit_breaker=circuit_breaker,
    )
