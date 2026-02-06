"""System settings SQLAlchemy model for admin-configured global settings."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class SystemSettings(Base):
    """
    System-wide settings configured by admin.

    This is a singleton table - only one row with id=1 should exist.
    Admin users can modify these settings to control system-wide behavior.
    """

    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        default=1,
        comment="Singleton ID, always 1",
    )

    # === OpenAI Configuration ===
    openai_api_key: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="系统级 OpenAI API Key（加密存储）",
    )

    openai_base_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="自定义 OpenAI API 地址",
    )

    openai_model: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        default="gpt-4o-mini",
        comment="默认对话模型",
    )

    openai_max_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        default=4096,
        comment="默认最大输出 token 数",
    )

    openai_temperature: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        default=0.7,
        comment="默认温度参数 (0.0-2.0)",
    )

    # === Embedding & News Processing ===
    embedding_model: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        default="text-embedding-3-small",
        comment="向量嵌入模型",
    )

    news_filter_model: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        default="gpt-4o-mini",
        comment="新闻筛选模型",
    )

    news_retention_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=30,
        comment="新闻内容保留天数",
    )

    # === External API Keys ===
    finnhub_api_key: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="系统级 Finnhub API Key（加密存储）",
    )

    polygon_api_key: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="系统级 Polygon.io API Key（加密存储）",
    )

    # === User Permission Settings ===
    allow_user_custom_api_keys: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否允许用户使用自定义 API Key（全局开关）",
    )

    # === Audit Fields ===
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_by: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="最后更新者（管理员用户 ID）",
    )

    # Relationship
    updater = relationship("User", foreign_keys=[updated_by])

    def __repr__(self) -> str:
        return f"<SystemSettings(id={self.id}, updated_at={self.updated_at})>"
