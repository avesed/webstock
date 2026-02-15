"""Admin system settings, configuration, monitoring, and LLM provider endpoints."""

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import rate_limit
from app.core.security import require_admin
from app.db.database import get_db
from app.db.redis import get_redis
from app.models.llm_provider import LlmProvider
from app.models.user import AccountStatus, User, UserRole
from app.schemas.admin import (
    ActivityStats,
    ApiCallStats,
    ApiStats,
    FeaturesConfig,
    LangGraphConfig,
    LlmConfig,
    ModelAssignment,
    ModelAssignmentsConfig,
    NewsConfig,
    Phase2Config,
    Phase2ModelAssignment,
    SystemConfigResponse,
    SystemMonitorStatsResponse,
    SystemResourceStats,
    SystemSettingsResponse,
    SystemStatsResponse,
    UpdateSystemConfigRequest,
    UpdateSystemSettingsRequest,
    UserStats,
)
from app.schemas.llm_provider import (
    LlmProviderCreate,
    LlmProviderListResponse,
    LlmProviderResponse,
    LlmProviderUpdate,
)
from app.services.pending_token_service import clear_pending_token

from app.api.v1.admin._helpers import get_or_create_system_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin - Settings"])


# ============== System Settings Endpoints ==============


@router.get(
    "/settings",
    response_model=SystemSettingsResponse,
    summary="Get system settings",
    description="Get current system-wide settings.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def get_system_settings(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get system settings."""
    logger.info(f"Admin {admin.id} ({admin.email}) viewing system settings")

    settings = await get_or_create_system_settings(db)

    return SystemSettingsResponse(
        openai_api_key_set=bool(settings.openai_api_key),
        openai_base_url=settings.openai_base_url,
        openai_model=settings.openai_model or "gpt-4o-mini",
        openai_max_tokens=settings.openai_max_tokens,
        openai_temperature=settings.openai_temperature,
        # Anthropic settings
        anthropic_api_key_set=bool(settings.anthropic_api_key),
        anthropic_base_url=settings.anthropic_base_url,
        embedding_model=settings.embedding_model or "text-embedding-3-small",
        news_filter_model=settings.news_filter_model or "gpt-4o-mini",
        news_retention_days=settings.news_retention_days,
        finnhub_api_key_set=bool(settings.finnhub_api_key),
        polygon_api_key_set=bool(settings.polygon_api_key),
        tavily_api_key_set=bool(settings.tavily_api_key),
        allow_user_custom_api_keys=settings.allow_user_custom_api_keys,
        require_registration_approval=settings.require_registration_approval,
        # LangGraph settings
        local_llm_base_url=settings.local_llm_base_url,
        analysis_model=settings.analysis_model or "gpt-4o-mini",
        synthesis_model=settings.synthesis_model or "gpt-4o",
        use_local_models=settings.use_local_models,
        max_clarification_rounds=settings.max_clarification_rounds,
        clarification_confidence_threshold=settings.clarification_confidence_threshold,
        updated_at=settings.updated_at,
        updated_by=settings.updated_by,
    )


@router.put(
    "/settings",
    response_model=SystemSettingsResponse,
    summary="Update system settings",
    description="Update system-wide settings.",
    dependencies=[Depends(rate_limit(max_requests=20, window_seconds=60))],
)
async def update_system_settings(
    data: UpdateSystemSettingsRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update system settings."""
    update_fields = data.model_dump(exclude_none=True)
    logger.info(
        f"Admin {admin.id} ({admin.email}) updating system settings: "
        f"{list(update_fields.keys())}"
    )

    settings = await get_or_create_system_settings(db)

    # Update fields
    if data.openai_api_key is not None:
        settings.openai_api_key = data.openai_api_key or None
    if data.openai_base_url is not None:
        settings.openai_base_url = data.openai_base_url or None
    if data.openai_model is not None:
        settings.openai_model = data.openai_model or None
    if data.openai_max_tokens is not None:
        settings.openai_max_tokens = data.openai_max_tokens
    if data.openai_temperature is not None:
        settings.openai_temperature = data.openai_temperature
    if data.anthropic_api_key is not None:
        settings.anthropic_api_key = data.anthropic_api_key or None
    if data.anthropic_base_url is not None:
        settings.anthropic_base_url = data.anthropic_base_url or None
    if data.embedding_model is not None:
        settings.embedding_model = data.embedding_model or None
    if data.news_filter_model is not None:
        settings.news_filter_model = data.news_filter_model or None
    if data.news_retention_days is not None:
        settings.news_retention_days = data.news_retention_days
    if data.finnhub_api_key is not None:
        settings.finnhub_api_key = data.finnhub_api_key or None
    if data.polygon_api_key is not None:
        settings.polygon_api_key = data.polygon_api_key or None
    if data.tavily_api_key is not None:
        settings.tavily_api_key = data.tavily_api_key or None
    if data.allow_user_custom_api_keys is not None:
        settings.allow_user_custom_api_keys = data.allow_user_custom_api_keys

    # LangGraph settings
    if data.local_llm_base_url is not None:
        settings.local_llm_base_url = data.local_llm_base_url or None
    if data.analysis_model is not None:
        settings.analysis_model = data.analysis_model or None
    if data.synthesis_model is not None:
        settings.synthesis_model = data.synthesis_model or None
    if data.use_local_models is not None:
        settings.use_local_models = data.use_local_models
    if data.max_clarification_rounds is not None:
        settings.max_clarification_rounds = data.max_clarification_rounds
    if data.clarification_confidence_threshold is not None:
        settings.clarification_confidence_threshold = data.clarification_confidence_threshold

    # Handle require_registration_approval setting
    # When turning OFF approval requirement, batch-promote all pending users
    if data.require_registration_approval is not None:
        old_value = settings.require_registration_approval
        settings.require_registration_approval = data.require_registration_approval

        # If turning OFF approval requirement, promote all pending users
        if old_value and not data.require_registration_approval:
            pending_users_result = await db.execute(
                select(User).where(User.account_status == AccountStatus.PENDING_APPROVAL)
            )
            pending_users = pending_users_result.scalars().all()

            promoted_count = 0
            for user in pending_users:
                user.account_status = AccountStatus.ACTIVE
                # Clear any pending tokens
                await clear_pending_token(user.id)
                promoted_count += 1

            if promoted_count > 0:
                logger.info(
                    f"[AUDIT] Admin {admin.id} disabled registration approval - "
                    f"auto-promoted {promoted_count} pending users to active"
                )

    # Record who made the update
    settings.updated_by = admin.id

    await db.commit()
    await db.refresh(settings)

    logger.info(
        f"[AUDIT] Admin {admin.id} updated system settings: {list(update_fields.keys())}"
    )

    return SystemSettingsResponse(
        openai_api_key_set=bool(settings.openai_api_key),
        openai_base_url=settings.openai_base_url,
        openai_model=settings.openai_model or "gpt-4o-mini",
        openai_max_tokens=settings.openai_max_tokens,
        openai_temperature=settings.openai_temperature,
        # Anthropic settings
        anthropic_api_key_set=bool(settings.anthropic_api_key),
        anthropic_base_url=settings.anthropic_base_url,
        embedding_model=settings.embedding_model or "text-embedding-3-small",
        news_filter_model=settings.news_filter_model or "gpt-4o-mini",
        news_retention_days=settings.news_retention_days,
        finnhub_api_key_set=bool(settings.finnhub_api_key),
        polygon_api_key_set=bool(settings.polygon_api_key),
        tavily_api_key_set=bool(settings.tavily_api_key),
        allow_user_custom_api_keys=settings.allow_user_custom_api_keys,
        require_registration_approval=settings.require_registration_approval,
        # LangGraph settings
        local_llm_base_url=settings.local_llm_base_url,
        analysis_model=settings.analysis_model or "gpt-4o-mini",
        synthesis_model=settings.synthesis_model or "gpt-4o",
        use_local_models=settings.use_local_models,
        max_clarification_rounds=settings.max_clarification_rounds,
        clarification_confidence_threshold=settings.clarification_confidence_threshold,
        updated_at=settings.updated_at,
        updated_by=settings.updated_by,
    )


# ============== System Statistics Endpoint ==============


@router.get(
    "/stats",
    response_model=SystemStatsResponse,
    summary="Get system statistics",
    description="Get system-wide statistics including user counts and API usage.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_system_stats(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get system statistics."""
    logger.info(f"Admin {admin.id} ({admin.email}) viewing system stats")

    # Get user counts
    total_users_result = await db.execute(
        select(func.count()).select_from(User)
    )
    total_users = total_users_result.scalar() or 0

    total_admins_result = await db.execute(
        select(func.count()).select_from(User).where(User.role == UserRole.ADMIN)
    )
    total_admins = total_admins_result.scalar() or 0

    active_users_result = await db.execute(
        select(func.count()).select_from(User).where(User.is_active == True)
    )
    active_users = active_users_result.scalar() or 0

    # Get logins in last 24 hours (users with updated_at in last 24h as proxy)
    # Note: For accurate login tracking, you would need a dedicated login log table
    twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    logins_result = await db.execute(
        select(func.count())
        .select_from(User)
        .where(User.updated_at >= twenty_four_hours_ago)
    )
    logins_24h = logins_result.scalar() or 0

    # Get API stats from Redis (if tracked)
    # This is a placeholder - actual implementation would depend on how API calls are tracked
    api_stats = ApiCallStats(
        chat_requests_today=0,
        analysis_requests_today=0,
        total_tokens_today=0,
    )

    # Try to get stats from Redis
    try:
        redis = await get_redis()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        chat_count = await redis.get(f"stats:chat:{today}")
        analysis_count = await redis.get(f"stats:analysis:{today}")
        tokens_count = await redis.get(f"stats:tokens:{today}")

        api_stats = ApiCallStats(
            chat_requests_today=int(chat_count) if chat_count else 0,
            analysis_requests_today=int(analysis_count) if analysis_count else 0,
            total_tokens_today=int(tokens_count) if tokens_count else 0,
        )
    except Exception as e:
        logger.warning(f"Failed to get API stats from Redis: {e}")

    return SystemStatsResponse(
        total_users=total_users,
        total_admins=total_admins,
        active_users=active_users,
        logins_24h=logins_24h,
        api_stats=api_stats,
    )


# ============== System Config Endpoints (Frontend Compatibility) ==============


@router.get(
    "/system/config",
    response_model=SystemConfigResponse,
    summary="Get system configuration",
    description="Get system configuration in frontend-compatible format.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def get_system_config(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get system configuration matching frontend SystemConfig type."""
    logger.info(f"Admin {admin.id} ({admin.email}) viewing system config")

    settings = await get_or_create_system_settings(db)

    # Build model assignments from provider FKs
    model_assignments = ModelAssignmentsConfig(
        chat=ModelAssignment(
            provider_id=str(settings.chat_provider_id) if settings.chat_provider_id else None,
            model=settings.openai_model or "gpt-4o-mini",
        ),
        analysis=ModelAssignment(
            provider_id=str(settings.analysis_provider_id) if settings.analysis_provider_id else None,
            model=settings.analysis_model or "gpt-4o-mini",
        ),
        synthesis=ModelAssignment(
            provider_id=str(settings.synthesis_provider_id) if settings.synthesis_provider_id else None,
            model=settings.synthesis_model or "gpt-4o",
        ),
        embedding=ModelAssignment(
            provider_id=str(settings.embedding_provider_id) if settings.embedding_provider_id else None,
            model=settings.embedding_model or "text-embedding-3-small",
        ),
        news_filter=ModelAssignment(
            provider_id=str(settings.news_filter_provider_id) if settings.news_filter_provider_id else None,
            model=settings.news_filter_model or "gpt-4o-mini",
        ),
        content_extraction=ModelAssignment(
            provider_id=str(settings.content_extraction_provider_id) if settings.content_extraction_provider_id else None,
            model=settings.content_extraction_model or "gpt-4o-mini",
        ),
    )

    return SystemConfigResponse(
        llm=LlmConfig(
            api_key="***" if settings.openai_api_key else None,
            base_url=settings.openai_base_url or "https://api.openai.com/v1",
            model=settings.openai_model or "gpt-4o-mini",
            max_tokens=settings.openai_max_tokens,
            temperature=settings.openai_temperature,
            anthropic_api_key="***" if settings.anthropic_api_key else None,
            anthropic_base_url=settings.anthropic_base_url,
        ),
        news=NewsConfig(
            default_source="trafilatura",
            retention_days=settings.news_retention_days,
            embedding_model=settings.embedding_model or "text-embedding-3-small",
            filter_model=settings.news_filter_model or "gpt-4o-mini",
            auto_fetch_enabled=True,
            finnhub_api_key="***" if settings.finnhub_api_key else None,
            tavily_api_key="***" if settings.tavily_api_key else None,
            enable_mcp_extraction=settings.enable_mcp_extraction,
        ),
        features=FeaturesConfig(
            allow_user_api_keys=settings.allow_user_custom_api_keys,
            allow_user_custom_models=False,
            enable_news_analysis=settings.enable_news_analysis,
            enable_stock_analysis=settings.enable_stock_analysis,
            require_registration_approval=settings.require_registration_approval,
            enable_mcp_extraction=settings.enable_mcp_extraction,
        ),
        langgraph=LangGraphConfig(
            local_llm_base_url=settings.local_llm_base_url,
            analysis_model=settings.analysis_model or "gpt-4o-mini",
            synthesis_model=settings.synthesis_model or "gpt-4o",
            use_local_models=settings.use_local_models,
            max_clarification_rounds=settings.max_clarification_rounds,
            clarification_confidence_threshold=settings.clarification_confidence_threshold,
        ),
        model_assignments=model_assignments,
        phase2=Phase2Config(
            enable_llm_pipeline=settings.enable_llm_pipeline,
            discard_threshold=getattr(settings, 'layer1_discard_threshold', 105),
            full_analysis_threshold=getattr(settings, 'layer1_full_analysis_threshold', 195),
            layer1_scoring=Phase2ModelAssignment(
                provider_id=str(settings.layer1_scoring_provider_id) if getattr(settings, 'layer1_scoring_provider_id', None) else None,
                model=getattr(settings, 'layer1_scoring_model', '') or 'gpt-4o-mini',
            ),
            layer15_cleaning=Phase2ModelAssignment(
                provider_id=str(settings.phase2_layer15_cleaning_provider_id) if getattr(settings, 'phase2_layer15_cleaning_provider_id', None) else None,
                model=getattr(settings, 'phase2_layer15_cleaning_model', '') or 'gpt-4o',
            ),
            layer2_scoring=Phase2ModelAssignment(
                provider_id=str(settings.phase2_layer2_scoring_provider_id) if getattr(settings, 'phase2_layer2_scoring_provider_id', None) else None,
                model=getattr(settings, 'phase2_layer2_scoring_model', '') or 'gpt-4o-mini',
            ),
            layer2_analysis=Phase2ModelAssignment(
                provider_id=str(settings.phase2_layer2_analysis_provider_id) if getattr(settings, 'phase2_layer2_analysis_provider_id', None) else None,
                model=getattr(settings, 'phase2_layer2_analysis_model', '') or 'gpt-4o',
            ),
            layer2_lightweight=Phase2ModelAssignment(
                provider_id=str(settings.phase2_layer2_lightweight_provider_id) if getattr(settings, 'phase2_layer2_lightweight_provider_id', None) else None,
                model=getattr(settings, 'phase2_layer2_lightweight_model', '') or 'gpt-4o-mini',
            ),
            cache_enabled=getattr(settings, 'phase2_cache_enabled', True),
            cache_ttl_minutes=getattr(settings, 'phase2_cache_ttl_minutes', 60),
        ),
    )


@router.put(
    "/system/config",
    response_model=SystemConfigResponse,
    summary="Update system configuration",
    description="Update system configuration from frontend format.",
    dependencies=[Depends(rate_limit(max_requests=20, window_seconds=60))],
)
async def update_system_config(
    data: UpdateSystemConfigRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update system configuration from frontend format."""
    logger.info(f"Admin {admin.id} ({admin.email}) updating system config")

    settings = await get_or_create_system_settings(db)

    # Update LLM settings
    if data.llm:
        if data.llm.api_key and data.llm.api_key != "***":
            settings.openai_api_key = data.llm.api_key
        if data.llm.base_url:
            settings.openai_base_url = data.llm.base_url
        if data.llm.model:
            settings.openai_model = data.llm.model
        # Allow max_tokens to be cleared (set to null)
        settings.openai_max_tokens = data.llm.max_tokens
        # Allow temperature to be cleared (set to null)
        settings.openai_temperature = data.llm.temperature
        if data.llm.anthropic_api_key and data.llm.anthropic_api_key != "***":
            settings.anthropic_api_key = data.llm.anthropic_api_key
        if data.llm.anthropic_base_url is not None:
            settings.anthropic_base_url = data.llm.anthropic_base_url or None

    # Update news settings
    if data.news:
        if data.news.retention_days:
            settings.news_retention_days = data.news.retention_days
        if data.news.embedding_model:
            settings.embedding_model = data.news.embedding_model
        if data.news.filter_model:
            settings.news_filter_model = data.news.filter_model
        # Handle finnhub_api_key - only update if not masked
        if data.news.finnhub_api_key and data.news.finnhub_api_key != "***":
            settings.finnhub_api_key = data.news.finnhub_api_key or None
        # Handle tavily_api_key - only update if not masked
        if data.news.tavily_api_key and data.news.tavily_api_key != "***":
            settings.tavily_api_key = data.news.tavily_api_key or None
        if data.news.enable_mcp_extraction is not None:
            settings.enable_mcp_extraction = data.news.enable_mcp_extraction

    # Update feature flags
    if data.features:
        if data.features.allow_user_api_keys is not None:
            settings.allow_user_custom_api_keys = data.features.allow_user_api_keys
        if data.features.enable_news_analysis is not None:
            settings.enable_news_analysis = data.features.enable_news_analysis
        if data.features.enable_stock_analysis is not None:
            settings.enable_stock_analysis = data.features.enable_stock_analysis
        if data.features.require_registration_approval is not None:
            settings.require_registration_approval = data.features.require_registration_approval
        if data.features.enable_mcp_extraction is not None:
            settings.enable_mcp_extraction = data.features.enable_mcp_extraction

    # Update LangGraph settings
    if data.langgraph:
        if data.langgraph.local_llm_base_url is not None:
            settings.local_llm_base_url = data.langgraph.local_llm_base_url or None
        if data.langgraph.analysis_model:
            settings.analysis_model = data.langgraph.analysis_model
        if data.langgraph.synthesis_model:
            settings.synthesis_model = data.langgraph.synthesis_model
        if data.langgraph.use_local_models is not None:
            settings.use_local_models = data.langgraph.use_local_models
        if data.langgraph.max_clarification_rounds is not None:
            settings.max_clarification_rounds = data.langgraph.max_clarification_rounds
        if data.langgraph.clarification_confidence_threshold is not None:
            settings.clarification_confidence_threshold = data.langgraph.clarification_confidence_threshold

    # Update model assignments
    if data.model_assignments:
        ma = data.model_assignments
        if ma.chat:
            settings.openai_model = ma.chat.model or None
            settings.chat_provider_id = UUID(ma.chat.provider_id) if ma.chat.provider_id else None
        if ma.analysis:
            settings.analysis_model = ma.analysis.model or None
            settings.analysis_provider_id = UUID(ma.analysis.provider_id) if ma.analysis.provider_id else None
        if ma.synthesis:
            settings.synthesis_model = ma.synthesis.model or None
            settings.synthesis_provider_id = UUID(ma.synthesis.provider_id) if ma.synthesis.provider_id else None
        if ma.embedding:
            settings.embedding_model = ma.embedding.model or None
            settings.embedding_provider_id = UUID(ma.embedding.provider_id) if ma.embedding.provider_id else None
        if ma.news_filter:
            settings.news_filter_model = ma.news_filter.model or None
            settings.news_filter_provider_id = UUID(ma.news_filter.provider_id) if ma.news_filter.provider_id else None
        if ma.content_extraction:
            settings.content_extraction_model = ma.content_extraction.model or None
            settings.content_extraction_provider_id = UUID(ma.content_extraction.provider_id) if ma.content_extraction.provider_id else None

    # Update Phase 2 settings
    if data.phase2:
        p2 = data.phase2
        settings.enable_llm_pipeline = p2.enable_llm_pipeline
        settings.layer1_discard_threshold = p2.discard_threshold
        settings.layer1_full_analysis_threshold = p2.full_analysis_threshold
        if p2.layer1_scoring:
            settings.layer1_scoring_model = p2.layer1_scoring.model or None
            settings.layer1_scoring_provider_id = UUID(p2.layer1_scoring.provider_id) if p2.layer1_scoring.provider_id else None
        if p2.layer15_cleaning:
            settings.phase2_layer15_cleaning_model = p2.layer15_cleaning.model or None
            settings.phase2_layer15_cleaning_provider_id = UUID(p2.layer15_cleaning.provider_id) if p2.layer15_cleaning.provider_id else None
        if p2.layer2_scoring:
            settings.phase2_layer2_scoring_model = p2.layer2_scoring.model or None
            settings.phase2_layer2_scoring_provider_id = UUID(p2.layer2_scoring.provider_id) if p2.layer2_scoring.provider_id else None
        if p2.layer2_analysis:
            settings.phase2_layer2_analysis_model = p2.layer2_analysis.model or None
            settings.phase2_layer2_analysis_provider_id = UUID(p2.layer2_analysis.provider_id) if p2.layer2_analysis.provider_id else None
        if p2.layer2_lightweight:
            settings.phase2_layer2_lightweight_model = p2.layer2_lightweight.model or None
            settings.phase2_layer2_lightweight_provider_id = UUID(p2.layer2_lightweight.provider_id) if p2.layer2_lightweight.provider_id else None
        if p2.cache_enabled is not None:
            settings.phase2_cache_enabled = p2.cache_enabled
        if p2.cache_ttl_minutes is not None:
            settings.phase2_cache_ttl_minutes = p2.cache_ttl_minutes

    settings.updated_at = datetime.now(timezone.utc)
    settings.updated_by = admin.id
    await db.commit()
    await db.refresh(settings)

    # Return updated config
    return await get_system_config(admin, db)


@router.get(
    "/system/stats",
    response_model=SystemMonitorStatsResponse,
    summary="Get system monitor statistics",
    description="Get detailed system statistics for monitoring dashboard.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_system_monitor_stats(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get system monitor statistics matching frontend SystemMonitorStats type."""
    logger.info(f"Admin {admin.id} ({admin.email}) viewing system monitor stats")

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)

    # User stats
    total_users_result = await db.execute(select(func.count()).select_from(User))
    total_users = total_users_result.scalar() or 0

    active_users_result = await db.execute(
        select(func.count()).select_from(User).where(User.is_active == True)
    )
    active_users = active_users_result.scalar() or 0

    new_today_result = await db.execute(
        select(func.count()).select_from(User).where(User.created_at >= today_start)
    )
    new_today = new_today_result.scalar() or 0

    new_week_result = await db.execute(
        select(func.count()).select_from(User).where(User.created_at >= week_start)
    )
    new_this_week = new_week_result.scalar() or 0

    # Activity stats from Redis
    today_logins = 0
    api_calls_today = 0
    try:
        redis = await get_redis()
        today_str = now.strftime("%Y-%m-%d")
        logins = await redis.get(f"stats:logins:{today_str}")
        today_logins = int(logins) if logins else 0
        api_calls = await redis.get(f"stats:api:{today_str}")
        api_calls_today = int(api_calls) if api_calls else 0
    except Exception as e:
        logger.warning(f"Failed to get activity stats from Redis: {e}")

    return SystemMonitorStatsResponse(
        users=UserStats(
            total=total_users,
            active=active_users,
            new_today=new_today,
            new_this_week=new_this_week,
        ),
        activity=ActivityStats(
            today_logins=today_logins,
            active_conversations=0,  # TODO: Implement if needed
            reports_generated=0,  # TODO: Implement if needed
            api_calls_today=api_calls_today,
        ),
        system=SystemResourceStats(
            cpu_usage=0.0,  # TODO: Implement with psutil if needed
            memory_usage=0.0,
            disk_usage=0.0,
            uptime=0,
        ),
        api=ApiStats(
            total_requests=0,  # TODO: Implement if needed
            average_latency=0.0,
            error_rate=0.0,
            rate_limit_hits=0,
        ),
    )


# ============== LLM Provider CRUD Endpoints ==============


def _provider_to_response(provider: LlmProvider) -> LlmProviderResponse:
    """Convert LlmProvider model to response schema."""
    return LlmProviderResponse(
        id=str(provider.id),
        name=provider.name,
        provider_type=provider.provider_type,
        api_key_set=bool(provider.api_key),
        base_url=provider.base_url,
        models=provider.models or [],
        is_enabled=provider.is_enabled,
        sort_order=provider.sort_order,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


@router.get(
    "/llm-providers",
    response_model=LlmProviderListResponse,
    summary="List all LLM providers",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def list_llm_providers(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get all configured LLM providers."""
    logger.info("Admin %d listing LLM providers", admin.id)
    result = await db.execute(
        select(LlmProvider).order_by(LlmProvider.sort_order, LlmProvider.created_at)
    )
    providers = result.scalars().all()
    return LlmProviderListResponse(
        providers=[_provider_to_response(p) for p in providers]
    )


@router.post(
    "/llm-providers",
    response_model=LlmProviderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new LLM provider",
    dependencies=[Depends(rate_limit(max_requests=20, window_seconds=60))],
)
async def create_llm_provider(
    data: LlmProviderCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new LLM provider configuration."""
    logger.info(
        "Admin %d creating LLM provider: name=%s, type=%s",
        admin.id, data.name, data.provider_type,
    )

    # Check unique name
    existing = await db.execute(
        select(LlmProvider).where(LlmProvider.name == data.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Provider with name '{data.name}' already exists",
        )

    provider = LlmProvider(
        name=data.name,
        provider_type=data.provider_type,
        api_key=data.api_key or None,
        base_url=data.base_url or None,
        models=data.models or [],
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)

    logger.info(
        "[AUDIT] Admin %d created LLM provider %s (%s)",
        admin.id, provider.id, provider.name,
    )
    return _provider_to_response(provider)


@router.put(
    "/llm-providers/{provider_id}",
    response_model=LlmProviderResponse,
    summary="Update an LLM provider",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def update_llm_provider(
    provider_id: UUID,
    data: LlmProviderUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing LLM provider configuration."""
    result = await db.execute(
        select(LlmProvider).where(LlmProvider.id == provider_id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider not found",
        )

    update_fields = data.model_dump(exclude_none=True)
    logger.info(
        "Admin %d updating LLM provider %s: %s",
        admin.id, provider_id, list(update_fields.keys()),
    )

    if data.name is not None and data.name != provider.name:
        # Check unique name
        existing = await db.execute(
            select(LlmProvider).where(
                LlmProvider.name == data.name,
                LlmProvider.id != provider_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Provider with name '{data.name}' already exists",
            )
        provider.name = data.name

    if data.api_key is not None and data.api_key != "***":
        provider.api_key = data.api_key or None

    if data.base_url is not None:
        provider.base_url = data.base_url or None

    if data.models is not None:
        provider.models = data.models

    if data.is_enabled is not None:
        provider.is_enabled = data.is_enabled

    if data.sort_order is not None:
        provider.sort_order = data.sort_order

    await db.commit()
    await db.refresh(provider)

    logger.info(
        "[AUDIT] Admin %d updated LLM provider %s (%s)",
        admin.id, provider.id, provider.name,
    )
    return _provider_to_response(provider)


@router.delete(
    "/llm-providers/{provider_id}",
    summary="Delete an LLM provider",
    dependencies=[Depends(rate_limit(max_requests=10, window_seconds=60))],
)
async def delete_llm_provider(
    provider_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete an LLM provider. Fails if actively assigned."""
    result = await db.execute(
        select(LlmProvider).where(LlmProvider.id == provider_id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider not found",
        )

    # Check if any model assignment references this provider
    settings = await get_or_create_system_settings(db)
    active_assignments = []
    if settings.chat_provider_id == provider_id:
        active_assignments.append("chat")
    if settings.analysis_provider_id == provider_id:
        active_assignments.append("analysis")
    if settings.synthesis_provider_id == provider_id:
        active_assignments.append("synthesis")
    if settings.embedding_provider_id == provider_id:
        active_assignments.append("embedding")
    if settings.news_filter_provider_id == provider_id:
        active_assignments.append("news_filter")
    if settings.content_extraction_provider_id == provider_id:
        active_assignments.append("content_extraction")

    if active_assignments:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Provider is actively assigned to: {', '.join(active_assignments)}. "
            f"Reassign these roles before deleting.",
        )

    logger.info(
        "[AUDIT] Admin %d deleting LLM provider %s (%s)",
        admin.id, provider.id, provider.name,
    )
    await db.delete(provider)
    await db.commit()

    return {"message": f"Provider '{provider.name}' deleted"}
