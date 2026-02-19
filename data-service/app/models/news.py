"""News data models."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class NewsArticle(BaseModel):
    """A single news article from an external provider.

    Maps to the backend's NewsArticle dataclass fields.
    The 'id' is a deterministic MD5 hash of the URL for deduplication.
    """

    id: str  # MD5 hash of URL
    title: str
    url: str
    source: str  # "reuters", "cnbc", "eastmoney" ...
    published_at: Optional[str] = None
    summary: Optional[str] = None
    symbol: Optional[str] = None
    market: Optional[str] = None
    sentiment_score: Optional[float] = None
    image_url: Optional[str] = None
    provider: str  # "finnhub" / "yfinance" / "akshare"
