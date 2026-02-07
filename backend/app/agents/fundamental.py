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
from app.services.providers import get_provider_router
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
            institutional_holders=data.get("institutional_holders"),
            fund_holdings=data.get("fund_holdings"),
            northbound_holding=data.get("northbound_holding"),
            sector_industry=data.get("sector_industry"),
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
        - Institutional holdings (US: yfinance, A-shares: fund + northbound)
        - Sector/industry info
        """
        import asyncio

        stock_service = await get_stock_service()
        router = await get_provider_router()

        # Basic financial data tasks
        info_task = stock_service.get_info(symbol)
        financials_task = stock_service.get_financials(symbol)
        quote_task = stock_service.get_quote(symbol)

        # Institutional data based on market
        if market == "US":
            # US stocks: yfinance institutional holders
            inst_task = router.yfinance.get_institutional_holders(symbol)
            sector_task = router.yfinance.get_sector_industry(symbol)

            info, financials, quote, institutional, sector = await asyncio.gather(
                info_task,
                financials_task,
                quote_task,
                inst_task,
                sector_task,
                return_exceptions=True,
            )

            # Handle institutional exceptions
            if isinstance(institutional, Exception):
                logger.warning(f"Failed to get institutional for {symbol}: {institutional}")
                institutional = None
            if isinstance(sector, Exception):
                logger.warning(f"Failed to get sector for {symbol}: {sector}")
                sector = None

            fund_holdings = None
            northbound_holding = None

        elif market in ("CN", "A"):
            # A-share stocks: AKShare fund holdings + northbound
            stock_code = symbol.split(".")[0]
            fund_task = router.akshare.get_fund_holdings_cn(stock_code)
            northbound_task = router.akshare.get_northbound_holding(stock_code, days=30)
            industry_task = router.akshare.get_stock_industry_cn(stock_code)

            info, financials, quote, fund_holdings, northbound_holding, sector = await asyncio.gather(
                info_task,
                financials_task,
                quote_task,
                fund_task,
                northbound_task,
                industry_task,
                return_exceptions=True,
            )

            # Handle A-share specific exceptions
            if isinstance(fund_holdings, Exception):
                logger.warning(f"Failed to get fund holdings for {symbol}: {fund_holdings}")
                fund_holdings = None
            if isinstance(northbound_holding, Exception):
                logger.warning(f"Failed to get northbound for {symbol}: {northbound_holding}")
                northbound_holding = None
            if isinstance(sector, Exception):
                logger.warning(f"Failed to get industry for {symbol}: {sector}")
                sector = None

            institutional = None

        else:
            # HK or other markets: try yfinance
            inst_task = router.yfinance.get_institutional_holders(symbol)
            sector_task = router.yfinance.get_sector_industry(symbol)

            info, financials, quote, institutional, sector = await asyncio.gather(
                info_task,
                financials_task,
                quote_task,
                inst_task,
                sector_task,
                return_exceptions=True,
            )

            if isinstance(institutional, Exception):
                logger.warning(f"Failed to get institutional for {symbol}: {institutional}")
                institutional = None
            if isinstance(sector, Exception):
                logger.warning(f"Failed to get sector for {symbol}: {sector}")
                sector = None

            fund_holdings = None
            northbound_holding = None

        # Handle basic data exceptions
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
            "institutional_holders": institutional,
            "fund_holdings": fund_holdings,
            "northbound_holding": northbound_holding,
            "sector_industry": sector,
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
