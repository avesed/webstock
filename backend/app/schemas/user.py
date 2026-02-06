"""Pydantic schemas for user operations."""

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import UserRole
from app.schemas.base import CamelModel


class UserBase(CamelModel):
    """Base user schema with common fields."""

    email: EmailStr


class UserCreate(UserBase):
    """Schema for user registration."""

    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Password must be at least 8 characters with 1 uppercase, 1 lowercase, and 1 digit",
    )

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """Validate password meets security requirements."""
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        return v


class UserLogin(BaseModel):
    """Schema for user login."""

    email: EmailStr
    password: str


class UserResponse(UserBase):
    """Schema for user response (public data)."""

    id: int
    role: UserRole
    is_active: bool
    created_at: datetime


class UserInDB(UserBase):
    """Schema for user in database (internal use)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    password_hash: str
    is_active: bool
    is_locked: bool
    failed_login_attempts: int
    locked_until: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class TokenResponse(CamelModel):
    """Schema for token response."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenPayload(BaseModel):
    """Schema for decoded JWT token payload."""

    sub: str
    exp: datetime
    type: str


class MessageResponse(BaseModel):
    """Schema for simple message response."""

    message: str


class RefreshTokenRequest(BaseModel):
    """Schema for refresh token request (when not using cookies)."""

    refresh_token: Optional[str] = None
