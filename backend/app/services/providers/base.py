"""Abstract base class for stock data providers."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set

from app.services.stock_service import (
    DataSource,
    HistoryInterval,
    HistoryPeriod,
    Market,
    OHLCVBar,
    SearchResult,
    StockFinancials,
    StockHistory,
    StockInfo,
    StockQuote,
)


class DataProvider(ABC):
    """
    Abstract base class for all stock data providers.

    Subclasses must implement core methods (get_quote, get_history, search)
    and may optionally override extended methods (get_info, get_financials, etc.)

    Design principles:
    - Return None on error (no exceptions propagated)
    - Log errors internally
    - Support market-specific routing via supported_markets property
    """

    @property
    @abstractmethod
    def source(self) -> DataSource:
        """Return the data source identifier."""
        pass

    @property
    @abstractmethod
    def supported_markets(self) -> Set[Market]:
        """Return the set of markets this provider supports."""
        pass

    # === Core Abstract Methods (must implement) ===

    @abstractmethod
    async def get_quote(
        self,
        symbol: str,
        market: Market,
    ) -> Optional[StockQuote]:
        """
        Get real-time quote for a symbol.

        Args:
            symbol: Stock symbol (may need normalization)
            market: Market the symbol belongs to

        Returns:
            StockQuote or None if unavailable
        """
        pass

    @abstractmethod
    async def get_history(
        self,
        symbol: str,
        market: Market,
        period: HistoryPeriod,
        interval: HistoryInterval,
    ) -> Optional[StockHistory]:
        """
        Get historical OHLCV data.

        Args:
            symbol: Stock symbol
            market: Market the symbol belongs to
            period: Time period (1mo, 1y, etc.)
            interval: Data interval (1d, 1wk, etc.)

        Returns:
            StockHistory or None if unavailable
        """
        pass

    @abstractmethod
    async def search(
        self,
        query: str,
        markets: Optional[Set[Market]] = None,
    ) -> List[SearchResult]:
        """
        Search for stocks matching query.

        Args:
            query: Search query (symbol or name)
            markets: Optional filter for specific markets

        Returns:
            List of matching SearchResult objects (empty list on error)
        """
        pass

    # === Optional Methods (default to None) ===

    async def get_info(
        self,
        symbol: str,
        market: Market,
    ) -> Optional[StockInfo]:
        """
        Get company/asset information. Optional - returns None by default.
        """
        return None

    async def get_financials(
        self,
        symbol: str,
        market: Market,
    ) -> Optional[StockFinancials]:
        """
        Get financial metrics. Optional - returns None by default.
        """
        return None

    async def get_analyst_ratings(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get analyst ratings and price targets. Optional.
        """
        return None

    async def get_technical_info(
        self,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get pre-calculated technical data (SMA, ADTV, beta). Optional.
        """
        return None

    # === Utility Methods ===

    def supports_market(self, market: Market) -> bool:
        """Check if this provider supports the given market."""
        return market in self.supported_markets

    @classmethod
    def is_available(cls) -> bool:
        """
        Check if this provider is available (e.g., API key configured).
        Override in subclasses that require configuration.
        """
        return True
