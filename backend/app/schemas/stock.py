"""Pydantic schemas for stock data."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import CamelModel


class MarketType(str, Enum):
    """Stock market types."""

    US = "us"
    HK = "hk"
    SH = "sh"
    SZ = "sz"
    METAL = "metal"  # Precious metals futures (COMEX/NYMEX)


class HistoryInterval(str, Enum):
    """Historical data intervals."""

    ONE_MINUTE = "1m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    HOURLY = "1h"
    DAILY = "1d"
    WEEKLY = "1wk"
    MONTHLY = "1mo"


class HistoryPeriod(str, Enum):
    """Historical data periods."""

    ONE_DAY = "1d"
    FIVE_DAYS = "5d"
    ONE_MONTH = "1mo"
    THREE_MONTHS = "3mo"
    SIX_MONTHS = "6mo"
    ONE_YEAR = "1y"
    TWO_YEARS = "2y"
    FIVE_YEARS = "5y"
    MAX = "max"


class StockQuoteResponse(CamelModel):
    """Real-time stock quote response."""

    symbol: str
    name: Optional[str] = None
    price: float
    change: float
    change_percent: float
    volume: int
    market_cap: Optional[float] = None
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    open: Optional[float] = None
    previous_close: Optional[float] = None
    timestamp: str
    market: str
    source: str


class OHLCVBarResponse(CamelModel):
    """Single OHLCV bar response."""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class StockHistoryResponse(CamelModel):
    """Historical OHLCV data response."""

    symbol: str
    interval: str
    bars: List[OHLCVBarResponse]
    market: str
    source: str


class StockInfoResponse(CamelModel):
    """Company information response."""

    symbol: str
    name: str
    description: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    website: Optional[str] = None
    employees: Optional[int] = None
    market_cap: Optional[float] = None
    currency: str
    exchange: str
    market: str
    source: str


class StockFinancialsResponse(CamelModel):
    """Financial metrics response."""

    symbol: str
    pe_ratio: Optional[float] = None
    forward_pe: Optional[float] = None
    eps: Optional[float] = None
    dividend_yield: Optional[float] = None
    dividend_rate: Optional[float] = None
    book_value: Optional[float] = None
    price_to_book: Optional[float] = None
    revenue: Optional[float] = None
    revenue_growth: Optional[float] = None
    net_income: Optional[float] = None
    profit_margin: Optional[float] = None
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    roe: Optional[float] = None
    roa: Optional[float] = None
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    eps_growth: Optional[float] = None
    payout_ratio: Optional[float] = None
    market: str
    source: str


class SearchResultResponse(CamelModel):
    """Stock search result response."""

    symbol: str
    name: str
    exchange: str
    market: str


class SearchResponse(BaseModel):
    """Search results response."""

    results: List[SearchResultResponse]
    count: int


class BatchQuoteRequest(BaseModel):
    """Request for batch quotes."""

    symbols: List[str] = Field(..., min_length=1, max_length=50)


class BatchQuoteResponse(BaseModel):
    """Response for batch quotes."""

    quotes: Dict[str, Optional[StockQuoteResponse]]


class ErrorResponse(BaseModel):
    """Error response."""

    detail: str
    code: Optional[str] = None
