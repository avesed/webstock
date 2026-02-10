"""System settings SQLAlchemy model for admin-configured global settings."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
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

    news_use_llm_config: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="新闻处理是否使用 LLM 配置的 API 设置",
    )

    news_openai_base_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="新闻处理专用 OpenAI API 地址",
    )

    news_openai_api_key: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="新闻处理专用 OpenAI API Key",
    )

    # === Anthropic Configuration ===
    anthropic_api_key: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="系统级 Anthropic API Key（加密存储）",
    )

    anthropic_base_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="自定义 Anthropic API 地址（用于代理）",
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

    require_registration_approval: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否要求新用户注册后等待管理员审批",
    )

    # === Feature Toggles ===
    enable_news_analysis: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="是否启用新闻分析功能",
    )

    enable_stock_analysis: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="是否启用股票分析功能",
    )

    use_two_phase_filter: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="启用两阶段新闻筛选（渐进式发布）",
    )

    # === OpenAI Compatible / Local Model Configuration ===
    local_llm_base_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        default=None,
        comment="OpenAI 兼容端点地址（支持 vLLM, Ollama, LMStudio 等）",
    )

    analysis_model: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        default="gpt-4o-mini",
        comment="分析层模型（支持本地模型如 Qwen2.5-14B-Instruct）",
    )

    synthesis_model: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        default="gpt-4o",
        comment="综合层模型（用于最终综合分析和用户交互）",
    )

    use_local_models: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否使用 OpenAI 兼容的本地模型进行分析",
    )

    # === Clarification Settings ===
    max_clarification_rounds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=2,
        comment="最大追问轮次（综合层向分析层追问的最大次数）",
    )

    clarification_confidence_threshold: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.6,
        comment="触发追问的置信度阈值（低于此值时可能触发追问）",
    )

    # === LLM Provider Assignments ===
    chat_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
        comment="Provider for chat model (openai_model stores model name)",
    )

    analysis_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
        comment="Provider for analysis model (analysis_model stores model name)",
    )

    synthesis_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
        comment="Provider for synthesis model (synthesis_model stores model name)",
    )

    embedding_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
        comment="Provider for embedding model (embedding_model stores model name)",
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

    # Relationships
    updater = relationship("User", foreign_keys=[updated_by])
    chat_provider = relationship("LlmProvider", foreign_keys=[chat_provider_id])
    analysis_provider = relationship("LlmProvider", foreign_keys=[analysis_provider_id])
    synthesis_provider = relationship("LlmProvider", foreign_keys=[synthesis_provider_id])
    embedding_provider = relationship("LlmProvider", foreign_keys=[embedding_provider_id])

    def __repr__(self) -> str:
        return f"<SystemSettings(id={self.id}, updated_at={self.updated_at})>"
