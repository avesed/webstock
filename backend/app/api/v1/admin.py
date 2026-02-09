"""Admin API endpoints for user and system management."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import rate_limit
from app.core.security import hash_password, require_admin
from app.db.database import get_db
from app.db.redis import get_redis
from app.models.system_settings import SystemSettings
from app.models.user import AccountStatus, User, UserRole
from app.services.pending_token_service import clear_pending_token
from app.models.user_settings import UserSettings
from app.schemas.admin import (
    ActivityStats,
    ApiCallStats,
    ApiStats,
    ApproveUserRequest,
    CreateUserRequest,
    DailyFilterStatsResponse,
    FeaturesConfig,
    FilterStatsResponse,
    LangGraphConfig,
    LlmConfig,
    NewsConfig,
    RejectUserRequest,
    ResetPasswordRequest,
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
from app.schemas.user import MessageResponse

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
        embedding_model=settings.embedding_model or "text-embedding-3-small",
        news_filter_model=settings.news_filter_model or "gpt-4o-mini",
        news_retention_days=settings.news_retention_days,
        finnhub_api_key_set=bool(settings.finnhub_api_key),
        polygon_api_key_set=bool(settings.polygon_api_key),
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
        embedding_model=settings.embedding_model or "text-embedding-3-small",
        news_filter_model=settings.news_filter_model or "gpt-4o-mini",
        news_retention_days=settings.news_retention_days,
        finnhub_api_key_set=bool(settings.finnhub_api_key),
        polygon_api_key_set=bool(settings.polygon_api_key),
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

    return SystemConfigResponse(
        llm=LlmConfig(
            api_key="***" if settings.openai_api_key else None,
            base_url=settings.openai_base_url or "https://api.openai.com/v1",
            model=settings.openai_model or "gpt-4o-mini",
            max_tokens=settings.openai_max_tokens,  # None = use model default
            temperature=settings.openai_temperature,  # None = use model default
        ),
        news=NewsConfig(
            default_source="scraper",  # TODO: Add to system settings if needed
            retention_days=settings.news_retention_days,
            embedding_model=settings.embedding_model or "text-embedding-3-small",
            filter_model=settings.news_filter_model or "gpt-4o-mini",
            auto_fetch_enabled=True,  # TODO: Add to system settings if needed
            use_llm_config=settings.news_use_llm_config,
            openai_base_url=settings.news_openai_base_url,
            openai_api_key="***" if settings.news_openai_api_key else None,
            finnhub_api_key="***" if settings.finnhub_api_key else None,
        ),
        features=FeaturesConfig(
            allow_user_api_keys=settings.allow_user_custom_api_keys,
            allow_user_custom_models=False,  # TODO: Add to system settings if needed
            enable_news_analysis=settings.enable_news_analysis,
            enable_stock_analysis=settings.enable_stock_analysis,
            require_registration_approval=settings.require_registration_approval,
            use_two_phase_filter=settings.use_two_phase_filter,
        ),
        langgraph=LangGraphConfig(
            local_llm_base_url=settings.local_llm_base_url,
            analysis_model=settings.analysis_model or "gpt-4o-mini",
            synthesis_model=settings.synthesis_model or "gpt-4o",
            use_local_models=settings.use_local_models,
            max_clarification_rounds=settings.max_clarification_rounds,
            clarification_confidence_threshold=settings.clarification_confidence_threshold,
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

    # Update news settings
    if data.news:
        if data.news.retention_days:
            settings.news_retention_days = data.news.retention_days
        if data.news.embedding_model:
            settings.embedding_model = data.news.embedding_model
        if data.news.filter_model:
            settings.news_filter_model = data.news.filter_model
        if data.news.use_llm_config is not None:
            settings.news_use_llm_config = data.news.use_llm_config
        # Handle openai_base_url - allow clearing by passing empty string
        if data.news.openai_base_url is not None:
            settings.news_openai_base_url = data.news.openai_base_url or None
        # Handle openai_api_key - only update if not masked
        if data.news.openai_api_key and data.news.openai_api_key != "***":
            settings.news_openai_api_key = data.news.openai_api_key or None
        # Handle finnhub_api_key - only update if not masked
        if data.news.finnhub_api_key and data.news.finnhub_api_key != "***":
            settings.finnhub_api_key = data.news.finnhub_api_key or None

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
