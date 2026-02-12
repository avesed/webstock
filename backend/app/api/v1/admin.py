"""Admin API endpoints for user and system management."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import rate_limit
from app.core.security import hash_password, require_admin
from app.db.database import get_db
from app.db.redis import get_redis
from app.models.llm_provider import LlmProvider
from app.models.system_settings import SystemSettings
from app.models.user import AccountStatus, User, UserRole
from app.services.pending_token_service import clear_pending_token
from app.models.user_settings import UserSettings
from app.schemas.admin import (
    ActivityStats,
    ApiCallStats,
    ApiStats,
    ApproveUserRequest,
    ArticleTimelineResponse,
    CreateUserRequest,
    DailyFilterStatsResponse,
    FeaturesConfig,
    FilterStatsResponse,
    LangGraphConfig,
    LlmConfig,
    ModelAssignment,
    ModelAssignmentsConfig,
    NewsConfig,
    NodeStatsResponse,
    PipelineEventResponse,
    PipelineEventSearchResponse,
    PipelineStatsResponse,
    RejectUserRequest,
    ResetPasswordRequest,
    SourceStatsItemResponse,
    SourceStatsResponse,
    SystemConfigResponse,
    SystemMonitorStatsResponse,
    SystemResourceStats,
    SystemSettingsResponse,
    SystemStatsResponse,
    UpdateSystemConfigRequest,
    UpdateSystemSettingsRequest,
    UpdateUserRequest,
    UserAdminResponse,
    UserListResponse,
    UserStats,
)
from app.schemas.llm_provider import (
    LlmProviderCreate,
    LlmProviderListResponse,
    LlmProviderResponse,
    LlmProviderUpdate,
)
from app.schemas.rss_feed import (
    RssFeedCreate,
    RssFeedListResponse,
    RssFeedResponse,
    RssFeedStatsResponse,
    RssFeedStatsItem,
    RssFeedTestRequest,
    RssFeedTestResponse,
    RssFeedTestArticle,
    RssFeedUpdate,
)
from app.schemas.user import MessageResponse
from app.models.news import News
from app.models.rss_feed import RssFeed
from app.services.pipeline_trace_service import PipelineTraceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])


# ============== Helper Functions ==============


async def get_or_create_system_settings(db: AsyncSession) -> SystemSettings:
    """Get system settings or create default if not exists."""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.id == 1)
    )
    settings = result.scalar_one_or_none()

    if not settings:
        settings = SystemSettings(id=1)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
        logger.info("Created default system settings")

    return settings


async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    """Get user by ID with settings."""
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    return result.scalar_one_or_none()


async def get_admin_count(db: AsyncSession) -> int:
    """Get the count of admin users."""
    result = await db.execute(
        select(func.count()).select_from(User).where(User.role == UserRole.ADMIN)
    )
    return result.scalar() or 0


async def get_user_can_use_custom_api_key(
    db: AsyncSession, user_id: int
) -> bool:
    """Check if user has custom API key permission."""
    result = await db.execute(
        select(UserSettings.can_use_custom_api_key).where(
            UserSettings.user_id == user_id
        )
    )
    row = result.first()
    return row[0] if row else False


def build_user_admin_response(
    user: User, can_use_custom_api_key: bool = False
) -> UserAdminResponse:
    """Build UserAdminResponse from User model."""
    return UserAdminResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        account_status=user.account_status,
        is_active=user.is_active,
        is_locked=user.is_locked,
        failed_login_attempts=user.failed_login_attempts,
        locked_until=user.locked_until,
        created_at=user.created_at,
        updated_at=user.updated_at,
        can_use_custom_api_key=can_use_custom_api_key,
    )


# ============== User Management Endpoints ==============


@router.get(
    "/users",
    response_model=UserListResponse,
    summary="List all users",
    description="Get paginated list of all users with optional filters.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def list_users(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search by email"),
    role: Optional[UserRole] = Query(None, description="Filter by role"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    is_locked: Optional[bool] = Query(None, description="Filter by locked status"),
):
    """List all users with pagination and filters."""
    logger.info(
        f"Admin {admin.id} ({admin.email}) listing users - "
        f"page={page}, search={search}, role={role}"
    )

    # Build query conditions
    conditions = []
    if search:
        conditions.append(User.email.ilike(f"%{search}%"))
    if role is not None:
        conditions.append(User.role == role)
    if is_active is not None:
        conditions.append(User.is_active == is_active)
    if is_locked is not None:
        conditions.append(User.is_locked == is_locked)

    # Get total count
    count_query = select(func.count()).select_from(User)
    if conditions:
        count_query = count_query.where(and_(*conditions))
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Get users with pagination
    query = select(User)
    if conditions:
        query = query.where(and_(*conditions))
    query = query.order_by(User.id.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    users = result.scalars().all()

    # Get custom API key permissions for all users
    user_ids = [u.id for u in users]
    if user_ids:
        settings_result = await db.execute(
            select(UserSettings.user_id, UserSettings.can_use_custom_api_key).where(
                UserSettings.user_id.in_(user_ids)
            )
        )
        permissions = {row[0]: row[1] for row in settings_result.all()}
    else:
        permissions = {}

    # Build response
    user_responses = [
        build_user_admin_response(u, permissions.get(u.id, False))
        for u in users
    ]

    return UserListResponse(users=user_responses, total=total)


@router.post(
    "/users",
    response_model=UserAdminResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user",
    description="Create a new user account with specified email, password, and role.",
    dependencies=[Depends(rate_limit(max_requests=20, window_seconds=60))],
)
async def create_user(
    data: CreateUserRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new user account.

    Admin can create users with any role. Created users bypass registration
    approval requirement and are immediately active.
    """
    logger.info(
        f"Admin {admin.id} ({admin.email}) creating user: "
        f"email={data.email}, role={data.role.value}"
    )

    # Check if email already exists
    result = await db.execute(
        select(User).where(User.email == data.email)
    )
    existing_user = result.scalar_one_or_none()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Create new user (always active, bypasses approval)
    user = User(
        email=data.email,
        password_hash=hash_password(data.password),
        role=data.role,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )

    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info(
        f"[AUDIT] Admin {admin.id} created user {user.id} ({user.email}) "
        f"with role {user.role.value}"
    )

    return build_user_admin_response(user, False)


@router.get(
    "/users/{user_id}",
    response_model=UserAdminResponse,
    summary="Get user details",
    description="Get detailed information about a specific user.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def get_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get user details by ID."""
    logger.info(f"Admin {admin.id} ({admin.email}) viewing user {user_id}")

    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    can_use_custom_api_key = await get_user_can_use_custom_api_key(db, user_id)

    return build_user_admin_response(user, can_use_custom_api_key)


@router.put(
    "/users/{user_id}",
    response_model=UserAdminResponse,
    summary="Update user",
    description="Update user role, status, or permissions.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def update_user(
    user_id: int,
    data: UpdateUserRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update user attributes."""
    logger.info(
        f"Admin {admin.id} ({admin.email}) updating user {user_id}: {data.model_dump(exclude_none=True)}"
    )

    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent admin from demoting themselves
    if user_id == admin.id:
        if data.role is not None and data.role != UserRole.ADMIN:
            logger.warning(
                f"Admin {admin.id} attempted to demote themselves"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot demote yourself from admin role",
            )
        if data.is_active is False:
            logger.warning(
                f"Admin {admin.id} attempted to deactivate themselves"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deactivate your own account",
            )

    # Prevent demoting the last admin
    if (
        data.role is not None
        and data.role != UserRole.ADMIN
        and user.role == UserRole.ADMIN
    ):
        admin_count = await get_admin_count(db)
        if admin_count <= 1:
            logger.warning(
                f"Attempted to demote last admin user {user_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot demote the last administrator",
            )

    # Update user fields
    if data.role is not None:
        old_role = user.role
        user.role = data.role
        logger.info(
            f"[AUDIT] Admin {admin.id} changed user {user_id} role: "
            f"{old_role.value} -> {data.role.value}"
        )

    if data.is_active is not None:
        user.is_active = data.is_active
        logger.info(
            f"[AUDIT] Admin {admin.id} set user {user_id} is_active={data.is_active}"
        )

    if data.is_locked is not None:
        user.is_locked = data.is_locked
        if not data.is_locked:
            # Unlock: reset failed attempts and locked_until
            user.failed_login_attempts = 0
            user.locked_until = None
        logger.info(
            f"[AUDIT] Admin {admin.id} set user {user_id} is_locked={data.is_locked}"
        )

    # Update can_use_custom_api_key in UserSettings
    can_use_custom_api_key = await get_user_can_use_custom_api_key(db, user_id)
    if data.can_use_custom_api_key is not None:
        # Get or create user settings
        settings_result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        )
        settings = settings_result.scalar_one_or_none()

        if settings:
            settings.can_use_custom_api_key = data.can_use_custom_api_key
        else:
            settings = UserSettings(
                user_id=user_id,
                can_use_custom_api_key=data.can_use_custom_api_key,
            )
            db.add(settings)

        can_use_custom_api_key = data.can_use_custom_api_key
        logger.info(
            f"[AUDIT] Admin {admin.id} set user {user_id} "
            f"can_use_custom_api_key={data.can_use_custom_api_key}"
        )

    await db.commit()
    await db.refresh(user)

    return build_user_admin_response(user, can_use_custom_api_key)


@router.post(
    "/users/{user_id}/reset-password",
    response_model=UserAdminResponse,
    summary="Reset user password",
    description="Reset a user's password to a new value.",
    dependencies=[Depends(rate_limit(max_requests=10, window_seconds=60))],
)
async def reset_user_password(
    user_id: int,
    data: ResetPasswordRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Reset a user's password."""
    logger.info(f"Admin {admin.id} ({admin.email}) resetting password for user {user_id}")

    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Hash and update password
    user.password_hash = hash_password(data.new_password)
    # Reset login attempts on password reset
    user.failed_login_attempts = 0
    user.is_locked = False
    user.locked_until = None

    await db.commit()
    await db.refresh(user)

    logger.info(
        f"[AUDIT] Admin {admin.id} reset password for user {user_id} ({user.email})"
    )

    can_use_custom_api_key = await get_user_can_use_custom_api_key(db, user_id)
    return build_user_admin_response(user, can_use_custom_api_key)


# ============== User Approval Endpoints ==============


@router.post(
    "/users/{user_id}/approve",
    response_model=UserAdminResponse,
    summary="Approve pending user",
    description="Approve a user account that is pending approval.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def approve_user(
    user_id: int,
    data: ApproveUserRequest = ApproveUserRequest(),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Approve a pending user account.

    Changes the user's account_status from PENDING_APPROVAL to ACTIVE,
    allowing them to log in normally.
    """
    logger.info(f"Admin {admin.id} ({admin.email}) approving user {user_id}")

    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if user.account_status != AccountStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User is not pending approval (current status: {user.account_status.value})",
        )

    # Approve the user
    user.account_status = AccountStatus.ACTIVE

    # Clear any pending tokens from Redis
    await clear_pending_token(user.id)

    await db.commit()
    await db.refresh(user)

    logger.info(
        f"[AUDIT] Admin {admin.id} approved user {user_id} ({user.email})"
    )

    # TODO: If data.send_notification is True, send email notification to user

    can_use_custom_api_key = await get_user_can_use_custom_api_key(db, user_id)
    return build_user_admin_response(user, can_use_custom_api_key)


@router.post(
    "/users/{user_id}/reject",
    response_model=MessageResponse,
    summary="Reject pending user",
    description="Reject a user account that is pending approval.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def reject_user(
    user_id: int,
    data: RejectUserRequest = RejectUserRequest(),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Reject a pending user account.

    By default, performs a soft delete (is_active=False).
    If delete_account is True, the account will be permanently deleted.
    """
    logger.info(
        f"Admin {admin.id} ({admin.email}) rejecting user {user_id}, "
        f"reason={data.reason}, delete={data.delete_account}"
    )

    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if user.account_status != AccountStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User is not pending approval (current status: {user.account_status.value})",
        )

    # Clear any pending tokens from Redis
    await clear_pending_token(user.id)

    if data.delete_account:
        # Hard delete
        await db.delete(user)
        await db.commit()

        logger.info(
            f"[AUDIT] Admin {admin.id} rejected and deleted user {user_id} ({user.email}), "
            f"reason: {data.reason or 'not specified'}"
        )

        return MessageResponse(message=f"User {user.email} has been rejected and deleted")
    else:
        # Soft delete - disable account
        user.is_active = False
        await db.commit()
        await db.refresh(user)

        logger.info(
            f"[AUDIT] Admin {admin.id} rejected user {user_id} ({user.email}), "
            f"reason: {data.reason or 'not specified'}"
        )

        return MessageResponse(message=f"User {user.email} has been rejected and disabled")


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
            use_two_phase_filter=settings.use_two_phase_filter,
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
        if data.features.use_two_phase_filter is not None:
            settings.use_two_phase_filter = data.features.use_two_phase_filter
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


# ============== News Filter Statistics Endpoints ==============


@router.get(
    "/news/filter-stats",
    response_model=FilterStatsResponse,
    summary="Get news filter statistics",
    description="Get comprehensive statistics for two-phase news filtering including pass rates and token usage.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_news_filter_stats(
    admin: User = Depends(require_admin),
    days: int = Query(7, ge=1, le=30, description="Number of days to aggregate"),
) -> FilterStatsResponse:
    """
    Get comprehensive news filter statistics.

    Returns:
    - counts: Initial filter (useful/uncertain/skip) and deep filter (keep/delete) counts
    - rates: Pass/skip/delete rates as percentages
    - tokens: Token usage with input/output breakdown and cost estimates
    - alerts: Any threshold violations
    """
    logger.info(f"Admin {admin.id} ({admin.email}) viewing news filter stats for {days} days")

    from app.services.filter_stats_service import get_filter_stats_service

    stats_service = get_filter_stats_service()
    stats = await stats_service.get_comprehensive_stats(days)

    return FilterStatsResponse(**stats)


@router.get(
    "/news/filter-stats/daily",
    response_model=DailyFilterStatsResponse,
    summary="Get daily filter statistics",
    description="Get day-by-day filter statistics for charting.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_daily_filter_stats(
    admin: User = Depends(require_admin),
    days: int = Query(7, ge=1, le=30, description="Number of days to retrieve"),
) -> DailyFilterStatsResponse:
    """
    Get daily filter statistics for time-series charts.

    Returns dict mapping date (YYYYMMDD) to daily stats.
    """
    logger.info(f"Admin {admin.id} ({admin.email}) viewing daily filter stats for {days} days")

    from app.services.filter_stats_service import get_filter_stats_service

    stats_service = get_filter_stats_service()
    daily_stats = await stats_service.get_stats_range(days)

    # Convert to list format with dates for easier frontend consumption
    result = []
    for date, stats in sorted(daily_stats.items(), reverse=True):
        result.append({
            "date": date,
            "initial_useful": stats.get("initial_useful", 0),
            "initial_uncertain": stats.get("initial_uncertain", 0),
            "initial_skip": stats.get("initial_skip", 0),
            "fine_keep": stats.get("fine_keep", 0),
            "fine_delete": stats.get("fine_delete", 0),
            "filter_error": stats.get("filter_error", 0),
            "embedding_success": stats.get("embedding_success", 0),
            "embedding_error": stats.get("embedding_error", 0),
            "initial_input_tokens": stats.get("initial_input_tokens", 0),
            "initial_output_tokens": stats.get("initial_output_tokens", 0),
            "deep_input_tokens": stats.get("deep_input_tokens", 0),
            "deep_output_tokens": stats.get("deep_output_tokens", 0),
        })

    return DailyFilterStatsResponse(days=days, data=result)


@router.post(
    "/news/trigger-monitor",
    summary="Trigger news monitor task",
    description="Manually trigger the news monitoring pipeline (fetch + filter + process).",
)
async def trigger_news_monitor(
    admin: User = Depends(require_admin),
):
    """Manually trigger the news monitor Celery task."""
    logger.info(f"Admin {admin.id} ({admin.email}) manually triggering news monitor")

    from worker.tasks.news_monitor import monitor_news
    task = monitor_news.delay()

    return {"message": "News monitor task triggered", "task_id": str(task.id)}


@router.get(
    "/news/monitor-status",
    summary="Get news monitor status",
    description="Get current news monitor execution status, progress, and schedule info.",
)
async def get_monitor_status(
    admin: User = Depends(require_admin),
):
    """Get news monitor task progress and schedule status."""
    import json
    from datetime import timedelta

    redis = await get_redis()

    # Get current status
    status = await redis.get("news:monitor:status")
    status = status if status else "idle"

    # Get progress (if running)
    progress = None
    progress_raw = await redis.get("news:monitor:progress")
    if progress_raw:
        try:
            progress = json.loads(progress_raw)
        except Exception:
            pass

    # Get last run info
    last_run = None
    last_run_raw = await redis.get("news:monitor:last_run")
    if last_run_raw:
        try:
            last_run = json.loads(last_run_raw)
        except Exception:
            pass

    # Calculate next run time (every 15 minutes from last run)
    next_run_at = None
    if last_run and last_run.get("finished_at"):
        try:
            finished = datetime.fromisoformat(last_run["finished_at"])
            next_run_at = (finished + timedelta(minutes=15)).isoformat()
        except Exception:
            pass

    return {
        "status": status,
        "progress": progress,
        "last_run": last_run,
        "next_run_at": next_run_at,
    }


# ============== Pipeline Tracing Endpoints ==============


@router.get(
    "/pipeline/article/{news_id}",
    response_model=ArticleTimelineResponse,
    summary="Get article pipeline timeline",
    description="Get the full pipeline execution timeline for a specific article.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def get_article_timeline(
    news_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get pipeline timeline for a single article."""
    logger.info("Admin %d viewing pipeline timeline for article %s", admin.id, news_id)

    events = await PipelineTraceService.get_article_timeline(db, news_id)

    # Try to get article title and symbol from News table
    title = None
    symbol = None
    try:
        result = await db.execute(
            select(News.title, News.symbol).where(News.id == news_id)
        )
        row = result.first()
        if row:
            title = row.title
            symbol = row.symbol
    except Exception:
        pass  # News may have been deleted

    # Calculate total duration
    total_duration_ms = None
    if events:
        durations = [e.duration_ms for e in events if e.duration_ms is not None]
        if durations:
            total_duration_ms = round(sum(durations), 1)

    return ArticleTimelineResponse(
        news_id=news_id,
        title=title,
        symbol=symbol,
        events=[
            PipelineEventResponse(
                id=e.id,
                news_id=e.news_id,
                layer=e.layer,
                node=e.node,
                status=e.status,
                duration_ms=e.duration_ms,
                metadata=e.metadata_,
                error=e.error,
                created_at=e.created_at,
            )
            for e in events
        ],
        total_duration_ms=total_duration_ms,
    )


@router.get(
    "/pipeline/stats",
    response_model=PipelineStatsResponse,
    summary="Get pipeline aggregate statistics",
    description="Get aggregate statistics for pipeline nodes over a time period.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_pipeline_stats(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=30, description="Number of days to aggregate"),
):
    """Get aggregate pipeline statistics."""
    logger.info("Admin %d viewing pipeline stats for %d days", admin.id, days)

    stats = await PipelineTraceService.get_aggregate_stats(db, days)

    return PipelineStatsResponse(
        period_days=stats["period_days"],
        nodes=[
            NodeStatsResponse(**node_data)
            for node_data in stats["nodes"]
        ],
    )


@router.get(
    "/pipeline/events",
    response_model=PipelineEventSearchResponse,
    summary="Search pipeline events",
    description="Search and filter pipeline events with pagination.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def search_pipeline_events(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    layer: Optional[str] = Query(None, description="Filter by layer (1, 1.5, 2)"),
    node: Optional[str] = Query(None, description="Filter by node name"),
    status: Optional[str] = Query(None, description="Filter by status (success, error, skip)"),
    days: int = Query(1, ge=1, le=30, description="Time window in days"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """Search pipeline events with optional filters."""
    logger.info(
        "Admin %d searching pipeline events: layer=%s, node=%s, status=%s, days=%d",
        admin.id, layer, node, status, days,
    )

    events, total = await PipelineTraceService.search_events(
        db, layer=layer, node=node, status=status,
        days=days, limit=limit, offset=offset,
    )

    return PipelineEventSearchResponse(
        events=[
            PipelineEventResponse(
                id=e.id,
                news_id=e.news_id,
                layer=e.layer,
                node=e.node,
                status=e.status,
                duration_ms=e.duration_ms,
                metadata=e.metadata_,
                error=e.error,
                created_at=e.created_at,
            )
            for e in events
        ],
        total=total,
    )


# ============== Source Statistics Endpoints ==============


@router.get(
    "/news/source-stats",
    response_model=SourceStatsResponse,
    summary="Get news source quality statistics",
    description="Get per-source article quality metrics aggregated over a time period.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_source_stats(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=30, description="Number of days to aggregate"),
):
    """Get per-source quality statistics from the News table."""
    logger.info("Admin %d viewing source stats for %d days", admin.id, days)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Note: related_entities is JSON (not JSONB) — use json_array_length / json_typeof
    # content_status values are lowercase: pending, fetched, embedded, failed, blocked, deleted
    query = text("""
        SELECT
            source,
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE filter_status = 'useful') AS initial_useful,
            COUNT(*) FILTER (WHERE filter_status = 'uncertain') AS initial_uncertain,
            COUNT(*) FILTER (WHERE filter_status = 'keep') AS fine_keep,
            COUNT(*) FILTER (WHERE filter_status = 'delete') AS fine_delete,
            COUNT(*) FILTER (WHERE content_status = 'embedded') AS embedded,
            COUNT(*) FILTER (WHERE content_status IN ('failed', 'blocked')) AS fetch_failed,
            AVG(
                CASE
                    WHEN related_entities IS NOT NULL
                         AND related_entities::text != 'null'
                         AND json_typeof(related_entities) = 'array'
                    THEN json_array_length(related_entities)
                    ELSE 0
                END
            ) AS avg_entity_count,
            COUNT(*) FILTER (WHERE sentiment_tag = 'bullish') AS bullish,
            COUNT(*) FILTER (WHERE sentiment_tag = 'bearish') AS bearish,
            COUNT(*) FILTER (WHERE sentiment_tag = 'neutral') AS neutral
        FROM news
        WHERE created_at >= :cutoff
        GROUP BY source
        ORDER BY COUNT(*) DESC
    """)

    try:
        result = await db.execute(query, {"cutoff": cutoff})
        rows = result.fetchall()
    except Exception as e:
        logger.error("Failed to query source stats: %s", e)
        return SourceStatsResponse(period_days=days, sources=[], total_sources=0)

    sources = []
    for row in rows:
        total = row.total
        fine_total = row.fine_keep + row.fine_delete

        # Sentiment distribution (only include if any sentiment data exists)
        bullish = row.bullish
        bearish = row.bearish
        neutral = row.neutral
        sentiment_total = bullish + bearish + neutral
        sentiment_dist = (
            {"bullish": bullish, "bearish": bearish, "neutral": neutral}
            if sentiment_total > 0 else None
        )

        sources.append(SourceStatsItemResponse(
            source=row.source,
            total=total,
            initial_useful=row.initial_useful,
            initial_uncertain=row.initial_uncertain,
            fine_keep=row.fine_keep,
            fine_delete=row.fine_delete,
            embedded=row.embedded,
            fetch_failed=row.fetch_failed,
            avg_entity_count=round(row.avg_entity_count, 1) if row.avg_entity_count is not None else None,
            sentiment_distribution=sentiment_dist,
            keep_rate=round(row.embedded / total * 100, 1) if total > 0 else None,
            fetch_rate=round(fine_total / total * 100, 1) if total > 0 else None,
        ))

    return SourceStatsResponse(
        period_days=days,
        sources=sources,
        total_sources=len(sources),
    )


# ============== RSS Feed Management Endpoints ==============


def _feed_to_response(feed: RssFeed) -> RssFeedResponse:
    """Convert RssFeed model to response schema."""
    return RssFeedResponse(
        id=str(feed.id),
        name=feed.name,
        rsshub_route=feed.rsshub_route,
        description=feed.description,
        category=feed.category,
        symbol=feed.symbol,
        market=feed.market,
        poll_interval_minutes=feed.poll_interval_minutes,
        fulltext_mode=feed.fulltext_mode,
        is_enabled=feed.is_enabled,
        last_polled_at=feed.last_polled_at,
        last_error=feed.last_error,
        consecutive_errors=feed.consecutive_errors,
        article_count=feed.article_count,
        created_at=feed.created_at,
        updated_at=feed.updated_at,
    )


@router.get(
    "/rss-feeds",
    response_model=RssFeedListResponse,
    summary="List all RSS feeds",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def list_rss_feeds(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    category: Optional[str] = Query(None, description="Filter by category: media, exchange, social"),
    is_enabled: Optional[bool] = Query(None, description="Filter by enabled status"),
):
    """Get all configured RSS feeds."""
    logger.info("Admin %d listing RSS feeds (category=%s, enabled=%s)", admin.id, category, is_enabled)

    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    feeds, total = await rss_service.list_feeds(db, category=category, is_enabled=is_enabled)

    return RssFeedListResponse(
        feeds=[_feed_to_response(f) for f in feeds],
        total=total,
    )


@router.post(
    "/rss-feeds",
    response_model=RssFeedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new RSS feed",
    dependencies=[Depends(rate_limit(max_requests=20, window_seconds=60))],
)
async def create_rss_feed(
    data: RssFeedCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new RSS feed configuration."""
    logger.info(
        "Admin %d creating RSS feed: name=%s, route=%s",
        admin.id, data.name, data.rsshub_route,
    )

    # Check unique route
    existing = await db.execute(
        select(RssFeed).where(RssFeed.rsshub_route == data.rsshub_route)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Feed with route '{data.rsshub_route}' already exists",
        )

    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    feed = await rss_service.create_feed(db, data.model_dump(by_alias=False))

    logger.info(
        "[AUDIT] Admin %d created RSS feed %s (%s)",
        admin.id, feed.id, feed.name,
    )
    return _feed_to_response(feed)


@router.get(
    "/rss-feeds/stats",
    response_model=RssFeedStatsResponse,
    summary="Get RSS feed statistics",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_rss_feed_stats(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get per-feed article statistics."""
    logger.info("Admin %d viewing RSS feed stats", admin.id)

    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    stats = await rss_service.get_feed_stats(db)

    return RssFeedStatsResponse(
        total_feeds=stats["total_feeds"],
        enabled_feeds=stats["enabled_feeds"],
        total_articles=stats["total_articles"],
        feeds=[RssFeedStatsItem(**f) for f in stats["feeds"]],
    )


@router.post(
    "/rss-feeds/test",
    response_model=RssFeedTestResponse,
    summary="Test an RSSHub route",
    dependencies=[Depends(rate_limit(max_requests=10, window_seconds=60))],
)
async def test_rss_feed(
    data: RssFeedTestRequest,
    admin: User = Depends(require_admin),
):
    """Test an RSSHub route without saving to the database."""
    logger.info(
        "Admin %d testing RSS route: %s (fulltext=%s)",
        admin.id, data.rsshub_route, data.fulltext_mode,
    )

    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    result = await rss_service.test_feed(data.rsshub_route, data.fulltext_mode)

    return RssFeedTestResponse(
        route=result["route"],
        article_count=result["article_count"],
        articles=[RssFeedTestArticle(**a) for a in result["articles"]],
        error=result["error"],
    )


@router.post(
    "/rss-feeds/trigger",
    summary="Manually trigger RSS monitor task",
    dependencies=[Depends(rate_limit(max_requests=5, window_seconds=60))],
)
async def trigger_rss_monitor(
    admin: User = Depends(require_admin),
):
    """Manually trigger the RSS monitor Celery task."""
    logger.info("Admin %d manually triggering RSS monitor", admin.id)

    from worker.tasks.rss_monitor import monitor_rss_feeds
    task = monitor_rss_feeds.delay()

    return {"message": "RSS monitor task triggered", "task_id": str(task.id)}


@router.get(
    "/rss-feeds/{feed_id}",
    response_model=RssFeedResponse,
    summary="Get RSS feed details",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def get_rss_feed(
    feed_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get a single RSS feed by ID."""
    logger.info("Admin %d viewing RSS feed %s", admin.id, feed_id)

    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    feed = await rss_service.get_feed(db, feed_id)

    if not feed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RSS feed not found",
        )

    return _feed_to_response(feed)


@router.put(
    "/rss-feeds/{feed_id}",
    response_model=RssFeedResponse,
    summary="Update an RSS feed",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def update_rss_feed(
    feed_id: UUID,
    data: RssFeedUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing RSS feed configuration."""
    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    feed = await rss_service.get_feed(db, feed_id)

    if not feed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RSS feed not found",
        )

    # by_alias=False: CamelModel defaults to camelCase keys due to
    # serialize_by_alias=True, but SQLAlchemy uses snake_case columns.
    update_fields = data.model_dump(exclude_none=True, by_alias=False)
    logger.info(
        "Admin %d updating RSS feed %s: %s",
        admin.id, feed_id, list(update_fields.keys()),
    )

    # Check unique route if changing
    if data.rsshub_route is not None and data.rsshub_route != feed.rsshub_route:
        existing = await db.execute(
            select(RssFeed).where(
                RssFeed.rsshub_route == data.rsshub_route,
                RssFeed.id != feed_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Feed with route '{data.rsshub_route}' already exists",
            )

    feed = await rss_service.update_feed(db, feed, update_fields)

    logger.info(
        "[AUDIT] Admin %d updated RSS feed %s (%s)",
        admin.id, feed.id, feed.name,
    )
    return _feed_to_response(feed)


@router.delete(
    "/rss-feeds/{feed_id}",
    summary="Delete an RSS feed",
    dependencies=[Depends(rate_limit(max_requests=10, window_seconds=60))],
)
async def delete_rss_feed(
    feed_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete an RSS feed. Associated news articles retain their rss_feed_id (SET NULL)."""
    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    feed = await rss_service.get_feed(db, feed_id)

    if not feed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RSS feed not found",
        )

    logger.info(
        "[AUDIT] Admin %d deleting RSS feed %s (%s)",
        admin.id, feed.id, feed.name,
    )
    await rss_service.delete_feed(db, feed)

    return {"message": f"RSS feed '{feed.name}' deleted"}


@router.post(
    "/rss-feeds/{feed_id}/toggle",
    response_model=RssFeedResponse,
    summary="Toggle RSS feed enabled status",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def toggle_rss_feed(
    feed_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Quick toggle enable/disable for an RSS feed."""
    from app.services.rss_service import get_rss_service
    rss_service = get_rss_service()
    feed = await rss_service.get_feed(db, feed_id)

    if not feed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="RSS feed not found",
        )

    new_status = not feed.is_enabled
    feed = await rss_service.update_feed(db, feed, {"is_enabled": new_status})

    logger.info(
        "[AUDIT] Admin %d toggled RSS feed %s (%s) to enabled=%s",
        admin.id, feed.id, feed.name, new_status,
    )
    return _feed_to_response(feed)
