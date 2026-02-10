"""Pydantic schemas for LLM Provider CRUD."""

from datetime import datetime
from typing import List, Optional

from pydantic import Field

from app.schemas.base import CamelModel


class LlmProviderCreate(CamelModel):
    """Request to create a new LLM provider."""

    name: str = Field(..., min_length=1, max_length=100)
    provider_type: str = Field(..., pattern=r"^(openai|anthropic)$")
    api_key: Optional[str] = Field(None, max_length=500)
    base_url: Optional[str] = Field(None, max_length=500)
    models: List[str] = Field(default_factory=list)


class LlmProviderUpdate(CamelModel):
    """Request to update an LLM provider."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    api_key: Optional[str] = Field(None, max_length=500)  # "***" = no change
    base_url: Optional[str] = Field(None, max_length=500)  # "" = clear
    models: Optional[List[str]] = None
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = None


class LlmProviderResponse(CamelModel):
    """Response for a single LLM provider."""

    id: str
    name: str
    provider_type: str
    api_key_set: bool  # True if api_key is non-empty (never expose actual key)
    base_url: Optional[str] = None
    models: List[str] = Field(default_factory=list)
    is_enabled: bool = True
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime


class LlmProviderListResponse(CamelModel):
    """Response for listing all providers."""

    providers: List[LlmProviderResponse]


class ModelAssignment(CamelModel):
    """A single model assignment (provider + model name)."""

    provider_id: Optional[str] = None
    model: str


class ModelAssignmentsConfig(CamelModel):
    """All model assignments for system settings."""

    chat: ModelAssignment
    analysis: ModelAssignment
    synthesis: ModelAssignment
    embedding: ModelAssignment
