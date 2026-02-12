"""RSS Feed model for RSSHub integration."""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class FeedCategory(str, Enum):
    """RSS Feed 类别."""

    MEDIA = "media"        # 媒体源 (Reuters, Bloomberg, etc.)
    EXCHANGE = "exchange"  # 交易所公告
    SOCIAL = "social"      # 社交媒体


class RssFeed(Base):
    """
    Stores RSSHub feed configurations for automated news ingestion.

    Each row represents one RSS feed route (e.g., /reuters/world, /cls/telegraph).
    Admins can create, edit, enable/disable, and monitor feeds.
    The rss_monitor Celery task polls due feeds at their configured intervals.
    """

    __tablename__ = "rss_feeds"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Feed 显示名称，例如 'Reuters World News'",
    )

    rsshub_route: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        unique=True,
        comment="RSSHub 路由，例如 /reuters/world",
    )

    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Feed 描述",
    )

    category: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=FeedCategory.MEDIA.value,
        comment="类别: media, exchange, social",
    )

    symbol: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="关联股票代码（可选，用于绑定特定个股）",
    )

    market: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="US",
        comment="市场: US, HK, SH, SZ, METAL",
    )

    poll_interval_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=15,
        comment="轮询间隔（分钟），最小5分钟",
    )

    fulltext_mode: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否使用 RSSHub fulltext 模式",
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    last_polled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="上次轮询时间",
    )

    last_error: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="最近一次错误信息",
    )

    consecutive_errors: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="连续错误次数（成功时重置为0，>=10时自动禁用）",
    )

    article_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="累计抓取文章数",
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
            f"<RssFeed(id={self.id}, name={self.name}, "
            f"route={self.rsshub_route}, enabled={self.is_enabled})>"
        )
