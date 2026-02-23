"""Pydantic schemas for the Discussion Group feature."""

import uuid
from datetime import datetime
from typing import List, Optional

from app.schemas.base import CamelModel


class StartDiscussionRequest(CamelModel):
    """Request body for starting a new discussion."""
    language: Optional[str] = None  # defaults to "zh" on server


class DiscussionSessionResponse(CamelModel):
    """Response for a discussion session (list/create view)."""
    id: uuid.UUID
    symbol: str
    market: str
    language: str
    status: str
    discussion_rounds: int
    max_rounds: int
    total_tokens: Optional[int] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class DiscussionMessageResponse(CamelModel):
    """Response for a single discussion message."""
    id: uuid.UUID
    round: int
    agent_type: str
    content: str
    structured_data: Optional[dict] = None
    tool_calls: Optional[List[dict]] = None
    token_count: Optional[int] = None
    latency_ms: Optional[int] = None
    created_at: datetime


class DiscussionSessionDetailResponse(CamelModel):
    """Detailed response including all messages."""
    id: uuid.UUID
    symbol: str
    market: str
    language: str
    status: str
    synthesis_report: Optional[str] = None
    compact_context: Optional[str] = None
    discussion_rounds: int
    max_rounds: int
    total_tokens: Optional[int] = None
    total_latency_ms: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    messages: List[DiscussionMessageResponse] = []


class CreateDiscussionChatResponse(CamelModel):
    """Response for creating a post-discussion chat conversation."""
    conversation_id: uuid.UUID
