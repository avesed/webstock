"""Pydantic schemas for portfolio operations."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.base import CamelModel


class TransactionTypeEnum(str, Enum):
    """Transaction type enumeration."""

    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"


class CurrencyEnum(str, Enum):
    """Supported currencies."""

    USD = "USD"
    HKD = "HKD"
    CNY = "CNY"


# ============== Portfolio Schemas ==============


class PortfolioBase(CamelModel):
    """Base schema for portfolio."""

    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    currency: CurrencyEnum = Field(default=CurrencyEnum.USD)


class PortfolioCreate(PortfolioBase):
    """Schema for creating a portfolio."""

    pass


class PortfolioUpdate(BaseModel):
    """Schema for updating a portfolio."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    currency: Optional[CurrencyEnum] = None


class PortfolioResponse(PortfolioBase):
    """Response schema for portfolio."""

    id: str
    user_id: int
    is_default: bool
    created_at: datetime
    updated_at: datetime
    holdings_count: int = 0


class PortfolioListResponse(BaseModel):
    """Response schema for list of portfolios."""

    portfolios: List[PortfolioResponse]
    total: int


# ============== Holding Schemas ==============


class HoldingResponse(CamelModel):
    """Response schema for holding."""

    id: str
    portfolio_id: str
    symbol: str
    quantity: Decimal
    average_cost: Decimal
    total_cost: Decimal
    created_at: datetime
    updated_at: datetime


class HoldingWithQuote(HoldingResponse):
    """Holding with live price data and profit/loss calculation."""

    # Live quote data
    name: Optional[str] = None
    current_price: Optional[float] = None

    # Calculated fields
    market_value: Optional[Decimal] = None  # quantity * current_price
    profit_loss: Optional[Decimal] = None  # market_value - total_cost
    profit_loss_percent: Optional[float] = None  # (profit_loss / total_cost) * 100
    day_change: Optional[float] = None  # Price change today
    day_change_percent: Optional[float] = None  # Price change % today


class HoldingsListResponse(BaseModel):
    """Response schema for list of holdings."""

    holdings: List[HoldingWithQuote]
    total: int


class HoldingDetailResponse(HoldingWithQuote):
    """Detailed holding response with additional info."""

    # Additional stock info
    sector: Optional[str] = None
    industry: Optional[str] = None
    exchange: Optional[str] = None
    currency: Optional[str] = None


# ============== Transaction Schemas ==============


class TransactionBase(CamelModel):
    """Base schema for transaction."""

    symbol: str = Field(..., min_length=1, max_length=20)
    type: TransactionTypeEnum
    quantity: Decimal = Field(..., gt=0)
    price: Decimal = Field(..., ge=0)
    fee: Decimal = Field(default=Decimal("0"), ge=0)
    date: datetime
    notes: Optional[str] = Field(None, max_length=500)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, v: str) -> str:
        """Normalize symbol to uppercase."""
        return v.strip().upper()


class TransactionCreate(TransactionBase):
    """Schema for creating a transaction."""

    pass


class TransactionResponse(TransactionBase):
    """Response schema for transaction."""

    id: str
    portfolio_id: str
    total: Decimal
    created_at: datetime


class TransactionListResponse(BaseModel):
    """Response schema for list of transactions."""

    transactions: List[TransactionResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class TransactionFilters(BaseModel):
    """Filters for transaction queries."""

    symbol: Optional[str] = None
    type: Optional[TransactionTypeEnum] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


# ============== Portfolio Summary Schemas ==============


class PortfolioSummary(BaseModel):
    """Summary statistics for a portfolio."""

    portfolio_id: str
    portfolio_name: str
    currency: str

    # Cost basis
    total_cost: Decimal = Field(default=Decimal("0"))

    # Current values (requires live prices)
    total_market_value: Optional[Decimal] = None

    # Profit/Loss
    total_profit_loss: Optional[Decimal] = None
    total_profit_loss_percent: Optional[float] = None

    # Day performance
    day_change: Optional[Decimal] = None
    day_change_percent: Optional[float] = None

    # Holdings breakdown
    holdings_count: int = 0

    # Best/Worst performers
    best_performer: Optional[HoldingWithQuote] = None
    worst_performer: Optional[HoldingWithQuote] = None


class PortfolioDetailResponse(PortfolioResponse):
    """Detailed portfolio response with holdings."""

    holdings: List[HoldingResponse]


class PortfolioDetailWithQuotes(PortfolioResponse):
    """Portfolio response with holdings and live quotes."""

    holdings: List[HoldingWithQuote]
    summary: Optional[PortfolioSummary] = None


# ============== Message Response ==============


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str
