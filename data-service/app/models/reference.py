"""Reference data models — stock lists, profiles."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class StockListItem(BaseModel):
    """A single entry in the stock list / symbol directory."""

    symbol: str
    name: str
    name_zh: Optional[str] = None
    exchange: Optional[str] = None
    market: str
    pinyin: Optional[str] = None
    pinyin_initials: Optional[str] = None


class StockListResult(BaseModel):
    """Paginated / filtered stock list response."""

    items: list[StockListItem]
    count: int


class StockProfileData(BaseModel):
    """Detailed stock profile for knowledge base construction."""

    symbol: str
    market: str
    name: Optional[str] = None
    name_zh: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    concepts: list[str] = []
    main_business: str = ""
    description: Optional[str] = None


class StockProfileResult(BaseModel):
    """Batch stock profile response."""

    profiles: list[StockProfileData]
    count: int
    market: str
