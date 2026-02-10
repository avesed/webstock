"""LLM Provider model for multi-provider configuration."""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class LlmProvider(Base):
    """
    Stores LLM provider configurations (API keys, endpoints, models).

    Each row represents one provider configuration (e.g., "OpenAI Official",
    "My vLLM Proxy", "Anthropic Official"). Admins can create, edit, and
    delete providers, and assign them to different model roles (analysis,
    synthesis, chat, embedding) in system_settings.
    """

    __tablename__ = "llm_providers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        comment="Display name, e.g. 'OpenAI Official', 'My Proxy'",
    )

    provider_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Provider type: 'openai' or 'anthropic'",
    )

    api_key: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="API key (may be empty for local models)",
    )

    base_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Custom base URL; NULL = use official endpoint",
    )

    models: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        comment="List of available model names, e.g. ['gpt-4o', 'gpt-4o-mini']",
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<LlmProvider(id={self.id}, name={self.name}, "
            f"type={self.provider_type}, enabled={self.is_enabled})>"
        )
