"""Stock data providers package.

This package provides a modular architecture for fetching stock data from
multiple sources with automatic fallback support.

Architecture:
- DataProvider: Abstract base class defining the provider interface
- YFinanceProvider: US stocks, HK, precious metals
- AKShareProvider: A-shares, HK, institutional data
- TushareProvider: A-share fallback (requires API key)
- TiingoProvider: US stocks alternative (requires API key)
- ProviderRouter: Routes requests to appropriate providers with fallback

Usage:
    from app.services.providers import get_provider_router

    router = await get_provider_router()
    quote = await router.get_quote("AAPL")  # Auto-detects US market
    quote = await router.get_quote("600519.SS")  # A-share with fallback

    # Direct access to Tiingo (optional provider)
    if router.tiingo:
        fundamentals = await router.tiingo.get_fundamentals_statements("AAPL")
"""

from app.services.providers.base import DataProvider
from app.services.providers.yfinance import YFinanceProvider
from app.services.providers.akshare import AKShareProvider
from app.services.providers.tushare import TushareProvider
from app.services.providers.tiingo import TiingoProvider
from app.services.providers.router import ProviderRouter, get_provider_router

__all__ = [
    # Base class
    "DataProvider",
    # Providers
    "YFinanceProvider",
    "AKShareProvider",
    "TushareProvider",
    "TiingoProvider",
    # Router
    "ProviderRouter",
    "get_provider_router",
]
