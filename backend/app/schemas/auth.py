"""Authentication Pydantic schemas for registration approval flow."""

from typing import Literal

from app.schemas.base import CamelModel
from app.schemas.user import UserResponse


class RegisterResponse(CamelModel):
    """Response for user registration."""

    user: UserResponse
    requires_approval: bool


class PendingApprovalResponse(CamelModel):
    """Response when user login is blocked due to pending approval status."""

    status: Literal["pending_approval"] = "pending_approval"
    message: str
    pending_token: str
    email: str


class CheckStatusRequest(CamelModel):
    """Request to check account approval status."""

    email: str
    pending_token: str


class CheckStatusResponse(CamelModel):
    """Response for account status check."""

    status: str  # "pending_approval" | "active" | "rejected"
    message: str
