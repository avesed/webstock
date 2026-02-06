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
from app.models.user import User, UserRole
from app.models.user_settings import UserSettings
from app.schemas.admin import (
    ApiCallStats,
    ResetPasswordRequest,
    SystemSettingsResponse,
    SystemStatsResponse,
    UpdateSystemSettingsRequest,
    UpdateUserRequest,
    UserAdminResponse,
    UserListResponse,
)

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
