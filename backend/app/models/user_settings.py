"""User settings SQLAlchemy model."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class UserSettings(Base):
    """User settings model for storing preferences and API keys."""

    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Notification preferences
    notify_price_alerts: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    notify_news_alerts: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    notify_report_generation: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    notify_email: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # API Keys (encrypted)
    finnhub_api_key: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    openai_api_key: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    openai_base_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )

    # OpenAI model preference
    openai_model: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        default=None,
    )

    # OpenAI max_completion_tokens (user-configurable)
    openai_max_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        default=None,
    )

    # OpenAI temperature (0.0 - 2.0, None = use model default)
    openai_temperature: Mapped[Optional[float]] = mapped_column(
        nullable=True,
        default=None,
    )

    # Custom system prompt for AI chat
    openai_system_prompt: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )

    # News source preference
    news_source: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        default="auto",
    )

    # === 新闻全文抓取设置 ===
    full_content_source: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        default="scraper",
        comment="全文抓取来源: scraper (newspaper4k) 或 polygon",
    )

    polygon_api_key: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="用户的Polygon.io API Key（用于新闻全文）",
    )

    news_retention_days: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        default=30,
        comment="新闻内容保留天数",
    )

    # Anthropic API Key (user-level override)
    anthropic_api_key: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="用户的 Anthropic API Key（用于 Claude 模型）",
    )

    # === Admin Permission Flags ===
    can_use_custom_api_key: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="管理员授权：允许此用户使用自定义 API Key",
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

    # Relationship
    user: Mapped["User"] = relationship("User", back_populates="settings")

    def __repr__(self) -> str:
        return f"<UserSettings(user_id={self.user_id})>"
