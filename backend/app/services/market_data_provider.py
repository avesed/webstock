"""Market data provider service for institutional holdings, indices, and northbound flow.

Delegates all data fetching to the data-service microservice via DataServiceClient.
The data-service handles provider routing, caching, and rate limiting internally.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Market index symbol mapping
MARKET_INDICES = {
    "sp500": ("^GSPC", "S&P 500"),
    "hang_seng": ("^HSI", "恒生指数"),
    "shanghai": ("000001.SS", "上证综指"),
    "shenzhen": ("399001.SZ", "深证成指"),
}


class MarketDataProvider:
    """
    Provides market data via the data-service microservice.

    Data Sources (handled by data-service):
    - yfinance: Institutional holdings, sector/industry, market indices
    - AKShare: A-share fund holdings, northbound flow, stock connect, sector data

    All caching is handled by the data-service.
    """

    # ============ Institutional Holdings ============

    async def get_institutional_holders(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get institutional holders for a stock (US/HK)."""
        from app.services.data_service_client import get_data_service_client

        client = await get_data_service_client()
        return await client.get_institutional(symbol)

    # ============ Sector/Industry Info ============

    async def get_sector_industry(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get sector and industry classification for a stock."""
        from app.services.data_service_client import get_data_service_client

        client = await get_data_service_client()
        return await client.get_sector_industry(symbol)

    # ============ Analyst Ratings ============

    async def get_analyst_ratings(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get analyst ratings and price targets for a stock."""
        from app.services.data_service_client import get_data_service_client

        client = await get_data_service_client()
        return await client.get_analyst_ratings(symbol)

    async def get_technical_info(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get technical indicator data (SMA, ADTV, beta, 52-week range)."""
        from app.services.data_service_client import get_data_service_client

        client = await get_data_service_client()
        return await client.get_technical(symbol)

    # ============ Market Index Data ============

    async def get_market_index(
        self,
        index_symbol: str,
        period: str = "5d",
    ) -> Optional[Dict[str, Any]]:
        """Get market index data."""
        from app.services.data_service_client import get_data_service_client

        client = await get_data_service_client()
        result = await client.get_market_indices(period=period)
        if not result:
            return None
        # Find the specific index in the result
        for name, (sym, _) in MARKET_INDICES.items():
            if sym == index_symbol:
                return result.get(name)
        # If not a known index, return the whole result
        return result.get(index_symbol)

    async def get_all_market_indices(
        self,
        period: str = "5d",
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """Get all major market indices."""
        from app.services.data_service_client import get_data_service_client

        client = await get_data_service_client()
        result = await client.get_market_indices(period=period)
        if not result:
            return {name: None for name in MARKET_INDICES}
        return result

    # ============ A-Share Fund Holdings ============

    async def get_fund_holdings_cn(
        self,
        symbol: str,
        quarter: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get fund holdings for A-share stock."""
        from app.services.data_service_client import get_data_service_client

        code = symbol.replace(".SS", "").replace(".SZ", "")
        client = await get_data_service_client()
        return await client.get_fund_holdings(code)

    # ============ Northbound Individual Stock Holding ============

    async def get_northbound_holding(
        self,
        symbol: str,
        days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """Get northbound holding for a specific A-share stock."""
        from app.services.data_service_client import get_data_service_client

        code = symbol.replace(".SS", "").replace(".SZ", "")
        client = await get_data_service_client()
        return await client.get_northbound_holding(code, days=days)

    # ============ Northbound Capital Flow ============

    async def get_northbound_flow(
        self,
        direction: str = "北向资金",
        days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """Get northbound capital flow history."""
        from app.services.data_service_client import get_data_service_client

        client = await get_data_service_client()
        return await client.get_northbound_flow(direction, days=days)

    # ============ Industry Sector Data ============

    async def get_industry_sector_list(self) -> Optional[Dict[str, Any]]:
        """Get list of all industry sectors with real-time data.

        Note: This endpoint may not exist in data-service yet.
        Returns None if unavailable.
        """
        from app.services.data_service_client import get_data_service_client

        client = await get_data_service_client()
        # Delegate to sector_industry with a special marker
        # This maps to a data-service endpoint if available
        return await client._request("GET", "/v1/analysis/industry-sectors")

    async def get_stock_industry_cn(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """Get industry information for A-share stock."""
        from app.services.data_service_client import get_data_service_client

        client = await get_data_service_client()
        return await client.get_sector_industry(symbol, market="CN")

    async def get_sector_history(
        self,
        sector_name: str,
        period: str = "日k",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get historical data for an industry sector.

        Note: This endpoint may not exist in data-service yet.
        Returns None if unavailable.
        """
        from app.services.data_service_client import get_data_service_client

        client = await get_data_service_client()
        params = {
            "sector_name": sector_name,
            "period": period,
        }
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return await client._request(
            "GET", "/v1/analysis/sector-history", params=params
        )

    # ============ Hong Kong Stock History ============

    async def get_hk_stock_history(
        self,
        symbol: str,
        days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """Get Hong Kong stock historical data."""
        from app.services.data_service_client import get_data_service_client

        client = await get_data_service_client()
        return await client.get_history(
            symbol, period=f"{days}d", interval="1d", market="hk"
        )

    # ============ Aggregated Market Context ============

    async def get_market_context(self) -> Dict[str, Any]:
        """Get aggregated market context for sentiment analysis."""
        from app.services.data_service_client import get_data_service_client

        client = await get_data_service_client()
        result = await client.get_market_context()
        if result:
            return result
        # Return empty context on failure
        return {
            "sp500": None,
            "hang_seng": None,
            "shanghai_composite": None,
            "shenzhen_component": None,
            "northbound_summary": None,
            "fetched_at": None,
            "source": "data-service",
        }


# Singleton instance
_market_data_provider: Optional[MarketDataProvider] = None
_provider_lock = asyncio.Lock()


async def get_market_data_provider() -> MarketDataProvider:
    """Get singleton MarketDataProvider instance."""
    global _market_data_provider
    if _market_data_provider is None:
        async with _provider_lock:
            if _market_data_provider is None:
                _market_data_provider = MarketDataProvider()
    return _market_data_provider
