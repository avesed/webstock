"""News SQLAlchemy models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ContentStatus(str, Enum):
    """Status of full content fetching."""

    PENDING = "pending"           # 等待抓取
    FETCHED = "fetched"           # 已抓取全文
    EMBEDDED = "embedded"         # 已生成向量
    PARTIAL = "partial"           # 内容不完整（<500字符）
    FAILED = "failed"             # 抓取失败
    BLOCKED = "blocked"           # 域名被屏蔽
    DELETED = "deleted"           # 已删除（被过滤）
    EMBEDDING_FAILED = "embedding_failed"  # Embedding 失败 (P0)


class FilterStatus(str, Enum):
    """两阶段筛选状态追踪 (解决崩溃恢复问题)."""

    PENDING = "pending"              # 等待初筛
    INITIAL_USEFUL = "useful"        # 初筛: 有价值
    INITIAL_UNCERTAIN = "uncertain"  # 初筛: 不确定
    INITIAL_SKIPPED = "skipped"      # 初筛: 跳过(不存储)
    FINE_KEEP = "keep"               # 精筛: 保留
    FINE_DELETE = "delete"           # 精筛: 删除
    FILTER_FAILED = "failed"         # 筛选失败


class News(Base):
    """News article model for stock-related news."""

    __tablename__ = "news"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    symbol: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    summary: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    source: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    url: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )

    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    sentiment_score: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
    )

    ai_analysis: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    market: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # === 全文内容引用字段 ===
    content_file_path: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="JSON文件路径，存储全文内容",
    )

    content_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ContentStatus.PENDING.value,
        index=True,
        comment="内容状态: pending, fetched, embedded, partial, failed, blocked, deleted",
    )

    content_fetched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="全文抓取时间",
    )

    content_error: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="抓取错误信息",
    )

    language: Mapped[Optional[str]] = mapped_column(
        String(10),
        nullable=True,
        comment="文章语言: en, zh 等",
    )

    # === newspaper4k 元数据 ===
    authors: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        comment="作者列表",
    )

    keywords: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        comment="关键词列表",
    )

    top_image: Mapped[Optional[str]] = mapped_column(
        String(1024),
        nullable=True,
        comment="文章主图URL",
    )

    # === 关联实体字段（LLM提取） ===
    related_entities: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        comment="关联实体及评分，格式: [{entity, type, score}, ...]",
    )

    # === RAG 优化辅助字段 ===
    has_stock_entities: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否包含个股实体",
    )

    has_macro_entities: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否包含宏观因素",
    )

    max_entity_score: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        comment="最高实体评分，用于快速过滤高相关性新闻",
    )

    primary_entity: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="主实体（最高分股票，或最高分指数/宏观）",
    )

    primary_entity_type: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="主实体类型: stock, index, macro",
    )

    # === 两阶段筛选状态追踪 (P0: 崩溃恢复) ===
    filter_status: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        index=True,
        comment="筛选状态: pending, useful, uncertain, keep, delete, failed",
    )

    # === 精筛结果 - Tags ===
    industry_tags: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        comment='行业标签: ["tech", "finance", "healthcare", ...]',
    )

    event_tags: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        comment='事件标签: ["earnings", "merger", "regulatory", ...]',
    )

    sentiment_tag: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        index=True,
        comment="情绪标签: bullish, bearish, neutral",
    )

    # === 精筛结果 - 投资摘要 ===
    investment_summary: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="投资导向摘要 (2-3句，由精筛生成)",
    )

    # 复合索引用于 RAG 查询优化
    __table_args__ = (
        Index("ix_news_stock_entities_score", "has_stock_entities", "max_entity_score"),
        Index("ix_news_macro_entities_score", "has_macro_entities", "max_entity_score"),
        Index("ix_news_primary_entity", "primary_entity", "primary_entity_type"),
    )

    def __repr__(self) -> str:
        return f"<News(id={self.id}, symbol={self.symbol}, title={self.title[:30]}...)>"


class NewsAlert(Base):
    """News alert configuration for users."""

    __tablename__ = "news_alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    symbol: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        index=True,
    )

    keywords: Mapped[List[str]] = mapped_column(
        ARRAY(String(100)),
        nullable=False,
        default=list,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<NewsAlert(id={self.id}, user_id={self.user_id}, symbol={self.symbol})>"
