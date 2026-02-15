"""Admin user management endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import rate_limit
from app.core.security import hash_password, require_admin
from app.db.database import get_db
from app.models.user import AccountStatus, User, UserRole
from app.models.user_settings import UserSettings
from app.schemas.admin import (
    ApproveUserRequest,
    CreateUserRequest,
    RejectUserRequest,
    ResetPasswordRequest,
    UpdateUserRequest,
    UserAdminResponse,
    UserListResponse,
)
from app.schemas.user import MessageResponse
from app.services.pending_token_service import clear_pending_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin - Users"])


# ============== User Helper Functions ==============


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
