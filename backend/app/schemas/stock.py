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
    TWO_MINUTES = "2m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    THIRTY_MINUTES = "30m"
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
    volume: Optional[int] = None
    market_cap: Optional[float] = None
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    open: Optional[float] = None
    previous_close: Optional[float] = None
    timestamp: Optional[str] = None
    market: str
    source: Optional[str] = None


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
    source: Optional[str] = None


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
    currency: Optional[str] = None
    exchange: Optional[str] = None
    market: str
    source: Optional[str] = None


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
    source: Optional[str] = None


class SearchResultResponse(CamelModel):
    """Stock search result response."""

    symbol: str
    name: str
    exchange: Optional[str] = None
    market: str
    match_field: Optional[str] = None  # Which field matched (for highlighting)
    name_zh: Optional[str] = None  # Chinese name (for display)


class SearchResponse(BaseModel):
    """Search results response."""

    results: List[SearchResultResponse]
    count: int
    source: str = "api"  # "local" or "api" (indicates search source)


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


# =============================================================================
# Technical indicator schemas
# =============================================================================


class IndicatorDataPoint(CamelModel):
    """Single data point in a technical indicator time series."""

    time: str
    value: float


class MAIndicatorResponse(CamelModel):
    """Response for a moving average indicator (SMA or EMA)."""

    series: List[IndicatorDataPoint]
    metadata: Dict[str, Any]


class MACDIndicatorResponse(CamelModel):
    """Response for MACD indicator with three lines."""

    macd_line: List[IndicatorDataPoint]
    signal_line: List[IndicatorDataPoint]
    histogram: List[IndicatorDataPoint]
    metadata: Dict[str, Any]


class BollingerBandsResponse(CamelModel):
    """Response for Bollinger Bands indicator with three bands."""

    upper: List[IndicatorDataPoint]
    middle: List[IndicatorDataPoint]
    lower: List[IndicatorDataPoint]
    metadata: Dict[str, Any]


class ATRIndicatorResponse(CamelModel):
    """Response for ATR indicator."""
    series: List[IndicatorDataPoint]
    metadata: Dict[str, Any]


class OBVIndicatorResponse(CamelModel):
    """Response for On-Balance Volume indicator."""
    series: List[IndicatorDataPoint]
    metadata: Dict[str, Any]


class KDJIndicatorResponse(CamelModel):
    """Response for KDJ (Stochastic) indicator with three lines."""
    k_line: List[IndicatorDataPoint]
    d_line: List[IndicatorDataPoint]
    j_line: List[IndicatorDataPoint]
    metadata: Dict[str, Any]


class WilliamsRResponse(CamelModel):
    """Response for Williams %R indicator."""
    series: List[IndicatorDataPoint]
    metadata: Dict[str, Any]


class CCIIndicatorResponse(CamelModel):
    """Response for CCI indicator."""
    series: List[IndicatorDataPoint]
    metadata: Dict[str, Any]


class VWAPIndicatorResponse(CamelModel):
    """Response for VWAP indicator."""
    series: List[IndicatorDataPoint]
    metadata: Dict[str, Any]


class SARIndicatorResponse(CamelModel):
    """Response for Parabolic SAR indicator."""
    series: List[IndicatorDataPoint]
    metadata: Dict[str, Any]


class TechnicalIndicatorsResponse(CamelModel):
    """Full technical indicators response for a symbol."""

    symbol: str
    interval: str
    ma: Optional[Dict[str, MAIndicatorResponse]] = None
    rsi: Optional[MAIndicatorResponse] = None
    macd: Optional[MACDIndicatorResponse] = None
    bb: Optional[BollingerBandsResponse] = None
    atr: Optional[ATRIndicatorResponse] = None
    obv: Optional[OBVIndicatorResponse] = None
    kdj: Optional[KDJIndicatorResponse] = None
    williams_r: Optional[WilliamsRResponse] = None
    cci: Optional[CCIIndicatorResponse] = None
    vwap: Optional[VWAPIndicatorResponse] = None
    sar: Optional[SARIndicatorResponse] = None
    warnings: List[str] = []
