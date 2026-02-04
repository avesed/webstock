"""Pydantic schemas for price alerts and push subscriptions."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.base import CamelModel


class AlertConditionType(str, Enum):
    """Alert condition type enumeration."""

    ABOVE = "above"
    BELOW = "below"
    CHANGE_PERCENT = "change_percent"


# ============== Price Alert Schemas ==============


class PriceAlertBase(CamelModel):
    """Base schema for price alert."""

    symbol: str = Field(..., min_length=1, max_length=20)
    condition_type: AlertConditionType
    threshold: Decimal = Field(..., description="Price threshold or percentage change")
    note: Optional[str] = Field(None, max_length=500)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, v: str) -> str:
        """Normalize symbol to uppercase."""
        return v.strip().upper()

    @field_validator("threshold")
    @classmethod
    def validate_threshold(cls, v: Decimal, info) -> Decimal:
        """Validate threshold based on condition type."""
        # For price conditions, threshold must be positive
        # For change_percent, it can be any value
        if v <= 0:
            condition = info.data.get("condition_type")
            if condition != AlertConditionType.CHANGE_PERCENT:
                raise ValueError("Threshold must be positive for price alerts")
        return v


class PriceAlertCreate(PriceAlertBase):
    """Schema for creating a price alert."""

    pass


class PriceAlertUpdate(BaseModel):
    """Schema for updating a price alert."""

    threshold: Optional[Decimal] = Field(None, gt=0)
    is_active: Optional[bool] = None
    note: Optional[str] = Field(None, max_length=500)


class PriceAlertResponse(PriceAlertBase):
    """Response schema for price alert."""

    id: str
    user_id: int
    is_active: bool
    is_triggered: bool
    triggered_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class PriceAlertListResponse(BaseModel):
    """Response schema for list of price alerts."""

    alerts: List[PriceAlertResponse]
    total: int


class PriceAlertWithPrice(PriceAlertResponse):
    """Price alert response with current price information."""

    current_price: Optional[float] = None
    price_distance: Optional[float] = None  # How far from threshold
    price_distance_percent: Optional[float] = None


# ============== Push Subscription Schemas ==============


class PushSubscriptionKeys(BaseModel):
    """Web Push subscription keys."""

    p256dh: str = Field(..., min_length=1)
    auth: str = Field(..., min_length=1)


class PushSubscriptionCreate(BaseModel):
    """Schema for creating a push subscription."""

    endpoint: str = Field(..., min_length=1)
    keys: PushSubscriptionKeys
    user_agent: Optional[str] = Field(None, max_length=512)


class PushSubscriptionResponse(CamelModel):
    """Response schema for push subscription."""

    id: str
    user_id: int
    endpoint: str
    is_active: bool
    created_at: datetime


class PushSubscriptionListResponse(BaseModel):
    """Response schema for list of push subscriptions."""

    subscriptions: List[PushSubscriptionResponse]
    total: int


# ============== Notification Schemas ==============


class AlertNotification(BaseModel):
    """Notification payload for triggered alert."""

    alert_id: str
    symbol: str
    condition_type: str
    threshold: Decimal
    current_price: float
    triggered_at: datetime
    message: str


# ============== Message Response ==============


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str


class VAPIDKeysResponse(CamelModel):
    """VAPID public key response for client-side subscription."""

    public_key: str
