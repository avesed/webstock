"""System settings SQLAlchemy model for admin-configured global settings."""

import uuid
from datetime import datetime, timezone
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
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

    tavily_api_key: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="系统级 Tavily API Key（用于内容抓取兜底）",
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

    enable_llm_pipeline: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="启用LLM新闻处理流水线（3层架构：评分->抓取清洗->分析）",
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

    news_filter_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
        comment="Provider for news filter model (news_filter_model stores model name)",
    )

    # === Phase 2 Multi-Agent Architecture ===
    phase2_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Phase 2多Agent架构开关（默认关闭）",
    )

    phase2_score_threshold: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=50,
        comment="评分阈值，≥此分数进入完整5-Agent分析（默认50）",
    )

    # Phase 2 Provider FKs (5 layers)
    phase2_layer1_filter_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
        comment="Phase 2 Layer 1过滤使用的Provider（FK→llm_providers）",
    )

    phase2_layer15_cleaning_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
        comment="Phase 2 Layer 1.5清洗使用的Provider（需支持vision）",
    )

    phase2_layer2_scoring_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
        comment="Phase 2 Layer 2评分路由使用的Provider（需支持vision）",
    )

    phase2_layer2_analysis_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
        comment="Phase 2 Layer 2深度分析使用的Provider",
    )

    phase2_layer2_lightweight_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
        comment="Phase 2 Layer 2轻量处理使用的Provider",
    )

    # Phase 2 Model Names (5 layers)
    phase2_layer1_filter_model: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        default="gpt-4o-mini",
        comment="Phase 2 Layer 1初步筛选模型名称",
    )

    phase2_layer15_cleaning_model: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        default="gpt-4o",
        comment="Phase 2 Layer 1.5内容清洗模型名称（必须支持vision）",
    )

    phase2_layer2_scoring_model: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        default="gpt-4o-mini",
        comment="Phase 2 Layer 2评分路由模型名称（需支持vision）",
    )

    phase2_layer2_analysis_model: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        default="gpt-4o",
        comment="Phase 2 Layer 2深度分析模型名称",
    )

    phase2_layer2_lightweight_model: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        default="gpt-4o-mini",
        comment="Phase 2 Layer 2轻量处理模型名称",
    )

    # Phase 2 Source Tiering
    phase2_high_value_sources: Mapped[Optional[list]] = mapped_column(
        JSONB,
        nullable=True,
        default=lambda: ["reuters", "bloomberg", "sec", "company_announcement"],
        comment="高价值新闻源JSON数组，这些源使用Phase 2完整多Agent分析",
    )

    phase2_high_value_pct: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.20,
        comment="高价值源占比（0.0-1.0），占比越高Phase 2处理的文章越多",
    )

    # Phase 2 Cache Config
    phase2_cache_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Prompt Caching开关（90%成本节省）",
    )

    phase2_cache_ttl_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=60,
        comment="Prompt Cache TTL（分钟）",
    )

    # === Layer 1 Scoring Configuration ===
    layer1_discard_threshold: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=105,
        comment="Layer 1评分丢弃阈值（0-300），低于此分数不抓取不分析",
    )

    layer1_full_analysis_threshold: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=195,
        comment="Layer 1评分完整分析阈值（0-300），>=此分数进入5-Agent深度分析",
    )

    layer1_scoring_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
        comment="Layer 1评分使用的Provider（FK->llm_providers）",
    )

    layer1_scoring_model: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        default="gpt-4o-mini",
        comment="Layer 1评分模型名称",
    )

    # === MCP Content Extraction ===
    enable_mcp_extraction: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否启用 LLM+MCP 抓取新闻全文（需 Playwright MCP 服务）",
    )

    content_extraction_model: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        default="gpt-4o-mini",
        comment="MCP 内容抓取使用的 LLM 模型",
    )

    content_extraction_provider_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
        comment="Provider for MCP content extraction model",
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
    news_filter_provider = relationship("LlmProvider", foreign_keys=[news_filter_provider_id])
    content_extraction_provider = relationship("LlmProvider", foreign_keys=[content_extraction_provider_id])
    phase2_layer1_filter_provider = relationship("LlmProvider", foreign_keys=[phase2_layer1_filter_provider_id])
    phase2_layer15_cleaning_provider = relationship("LlmProvider", foreign_keys=[phase2_layer15_cleaning_provider_id])
    phase2_layer2_scoring_provider = relationship("LlmProvider", foreign_keys=[phase2_layer2_scoring_provider_id])
    phase2_layer2_analysis_provider = relationship("LlmProvider", foreign_keys=[phase2_layer2_analysis_provider_id])
    phase2_layer2_lightweight_provider = relationship("LlmProvider", foreign_keys=[phase2_layer2_lightweight_provider_id])
    layer1_scoring_provider = relationship("LlmProvider", foreign_keys=[layer1_scoring_provider_id])

    def __repr__(self) -> str:
        return f"<SystemSettings(id={self.id}, updated_at={self.updated_at})>"
