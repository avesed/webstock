"""Generic API response wrapper for the data-service."""
from __future__ import annotations

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Unified API response envelope.

    All data-service endpoints return responses wrapped in this envelope
    so the caller can inspect metadata (source provider, cache status,
    timing) alongside the payload.
    """

    success: bool = True
    data: Optional[T] = None
    error: Optional[str] = None
    source: Optional[str] = None  # "yfinance" / "akshare" / "finnhub" ...
    cached: bool = False
    elapsed_ms: Optional[int] = None  # External API call time
