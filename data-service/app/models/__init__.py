"""Data models for the data-service API.

All models are Pydantic BaseModels used directly in API request/response schemas.
"""
from __future__ import annotations

from app.models.analysis import (
    AnalystRatings,
    FundHoldings,
    InstitutionalData,
    NorthboundData,
    SectorIndustry,
    TechnicalInfo,
)
from app.models.base import ApiResponse
from app.models.content import ContentResult
from app.models.market import (
    ForexRates,
    HSIConstituents,
    IndexBar,
    IndexData,
)
from app.models.news import NewsArticle
from app.models.reference import (
    StockListItem,
    StockListResult,
    StockProfileData,
    StockProfileResult,
)
from app.models.stock import (
    FinancialsData,
    HistoryData,
    InfoData,
    OHLCVBar,
    QuoteData,
    SearchItem,
)

__all__ = [
    # base
    "ApiResponse",
    # stock
    "QuoteData",
    "OHLCVBar",
    "HistoryData",
    "InfoData",
    "FinancialsData",
    "SearchItem",
    # market
    "IndexBar",
    "IndexData",
    "ForexRates",
    "HSIConstituents",
    # analysis
    "AnalystRatings",
    "TechnicalInfo",
    "NorthboundData",
    "InstitutionalData",
    "FundHoldings",
    "SectorIndustry",
    # news
    "NewsArticle",
    # content
    "ContentResult",
    # reference
    "StockListItem",
    "StockListResult",
    "StockProfileData",
    "StockProfileResult",
]
