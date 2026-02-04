"""Pydantic schemas for watchlist operations."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import CamelModel


class WatchlistItemBase(CamelModel):
    """Base schema for watchlist item."""

    symbol: str = Field(..., min_length=1, max_length=20)
    notes: Optional[str] = Field(None, max_length=500)
    alert_price_above: Optional[float] = Field(None, ge=0)
    alert_price_below: Optional[float] = Field(None, ge=0)


class WatchlistItemCreate(WatchlistItemBase):
    """Schema for creating a watchlist item."""

    pass


class WatchlistItemUpdate(BaseModel):
    """Schema for updating a watchlist item."""

    notes: Optional[str] = Field(None, max_length=500)
    alert_price_above: Optional[float] = Field(None, ge=0)
    alert_price_below: Optional[float] = Field(None, ge=0)


class WatchlistItemResponse(WatchlistItemBase):
    """Response schema for watchlist item."""

    id: int
    watchlist_id: int
    added_at: datetime


class WatchlistItemWithQuote(WatchlistItemResponse):
    """Watchlist item with current quote data."""

    name: Optional[str] = None
    price: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[int] = None


class WatchlistBase(CamelModel):
    """Base schema for watchlist."""

    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)


class WatchlistCreate(WatchlistBase):
    """Schema for creating a watchlist."""

    pass


class WatchlistUpdate(BaseModel):
    """Schema for updating a watchlist."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)


class WatchlistResponse(WatchlistBase):
    """Response schema for watchlist (without items)."""

    id: int
    user_id: int
    is_default: bool
    created_at: datetime
    updated_at: datetime
    item_count: int = 0


class WatchlistDetailResponse(WatchlistBase):
    """Response schema for watchlist with items."""

    id: int
    user_id: int
    is_default: bool
    created_at: datetime
    updated_at: datetime
    items: List[WatchlistItemResponse]


class WatchlistDetailWithQuotes(WatchlistBase):
    """Response schema for watchlist with items and quotes."""

    id: int
    user_id: int
    is_default: bool
    created_at: datetime
    updated_at: datetime
    items: List[WatchlistItemWithQuote]


class WatchlistListResponse(BaseModel):
    """Response schema for list of watchlists."""

    watchlists: List[WatchlistResponse]
    total: int


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str
