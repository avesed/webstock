"""Pydantic schemas for AI Chat operations."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelModel


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class CreateConversationRequest(CamelModel):
    """Schema for creating a new chat conversation."""

    title: Optional[str] = Field(None, max_length=255)
    symbol: Optional[str] = Field(None, max_length=20)


class SendMessageRequest(CamelModel):
    """Schema for sending a message in a conversation."""

    content: str = Field(..., min_length=1, max_length=4000)
    symbol: Optional[str] = Field(None, max_length=20, description="Stock symbol for context")
    language: Optional[str] = Field(None, max_length=10, description="User language (en or zh)")


class UpdateConversationRequest(CamelModel):
    """Schema for updating an existing conversation."""

    title: Optional[str] = Field(None, max_length=255)
    is_archived: Optional[bool] = None


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ChatMessageResponse(CamelModel):
    """Response schema for a single chat message."""

    id: UUID
    conversation_id: UUID
    role: str
    content: str
    token_count: Optional[int] = None
    model: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    rag_context: Optional[List[Dict[str, Any]]] = None
    created_at: datetime


class ConversationResponse(CamelModel):
    """Response schema for a conversation summary."""

    id: UUID
    title: Optional[str] = None
    symbol: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    is_archived: bool
    last_message: Optional[str] = None
    message_count: int = 0


class ConversationListResponse(CamelModel):
    """Response schema for a paginated list of conversations."""

    conversations: List[ConversationResponse]
    total: int


class ChatStreamEvent(CamelModel):
    """Schema for server-sent events during streaming chat responses."""

    type: str = Field(
        ...,
        description="Event type: content_delta, tool_use, rag_sources, error, done",
    )
    content: Optional[str] = None
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None
    tool_call: Optional[Dict[str, Any]] = None
    rag_sources: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None
    timestamp: float
