"""Authentication API endpoints."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Union

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.rate_limiter import rate_limit
from app.core.security import (
    add_token_to_blacklist,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    get_user_from_refresh_token,
    hash_password,
    verify_password,
)
from app.db.database import get_db
from app.models.system_settings import SystemSettings
from app.models.user import AccountStatus, User, UserRole
from app.models.login_log import LoginLog
from app.schemas.auth import (
    CheckStatusRequest,
    CheckStatusResponse,
    PendingApprovalResponse,
    RegisterResponse,
)
from app.schemas.user import (
    MessageResponse,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from app.services.pending_token_service import (
    clear_pending_token,
    create_pending_token,
    validate_pending_token,
)

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/auth", tags=["Authentication"])


async def _get_system_settings(db: AsyncSession) -> SystemSettings:
    """Get system settings or create default if not exists."""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.id == 1)
    )
    system_settings = result.scalar_one_or_none()

    if not system_settings:
        system_settings = SystemSettings(id=1)
        db.add(system_settings)
        await db.commit()
        await db.refresh(system_settings)

    return system_settings


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(max_requests=5, window_seconds=60, key_prefix="auth:register")),
):
    """
    Register a new user account.

    - **email**: Valid email address (must be unique)
    - **password**: Password with at least 8 characters

    If registration approval is enabled by admin, new users will be in
    pending_approval status until approved by an administrator.
    """
    # Check if email already exists
    result = await db.execute(
        select(User).where(User.email == user_data.email)
    )
    existing_user = result.scalar_one_or_none()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Check system settings for registration approval requirement
    system_settings = await _get_system_settings(db)
    requires_approval = system_settings.require_registration_approval

    # Determine account status
    # FIRST_ADMIN_EMAIL always gets ACTIVE status and bypasses approval
    is_first_admin = (
        settings.FIRST_ADMIN_EMAIL
        and user_data.email.lower() == settings.FIRST_ADMIN_EMAIL.lower()
    )

    if requires_approval and not is_first_admin:
        account_status = AccountStatus.PENDING_APPROVAL
        logger.info(
            f"New user registration with pending approval: {user_data.email}"
        )
    else:
        account_status = AccountStatus.ACTIVE

    # Create new user
    user = User(
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        account_status=account_status,
    )

    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info(
        f"User registered: id={user.id}, email={user.email}, "
        f"status={user.account_status.value}, requires_approval={requires_approval and not is_first_admin}"
    )

    return RegisterResponse(
        user=UserResponse(
            id=user.id,
            email=user.email,
            role=user.role,
            is_active=user.is_active,
            account_status=user.account_status,
            created_at=user.created_at,
        ),
        requires_approval=requires_approval and not is_first_admin,
    )


async def _log_login_attempt(
    db: AsyncSession,
    user_id: int | None,
    ip_address: str,
    user_agent: str,
    success: bool,
    failure_reason: str | None = None,
) -> None:
    """Log a login attempt to the database."""
    log_entry = LoginLog(
        user_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent[:512] if user_agent else None,  # Truncate long user agents
        success=success,
        failure_reason=failure_reason,
    )
    db.add(log_entry)
    # Note: commit happens in the caller or via get_db context


@router.post(
    "/login",
    response_model=Union[TokenResponse, PendingApprovalResponse],
    summary="Login with email and password",
    responses={
        200: {"model": TokenResponse, "description": "Successful login"},
        202: {"model": PendingApprovalResponse, "description": "Account pending approval"},
    },
)
async def login(
    user_data: UserLogin,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit(max_requests=5, window_seconds=900, key_prefix="auth:login")),
):
    """
    Authenticate user and return access token.

    Refresh token is set as HttpOnly cookie.

    - **email**: User's email address
    - **password**: User's password

    If the account is pending approval, returns HTTP 202 with a pending token
    that can be used to check approval status.
    """
    # Get client info for logging
    ip_address = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")

    # Find user by email
    result = await db.execute(
        select(User).where(User.email == user_data.email)
    )
    user = result.scalar_one_or_none()

    if user is None:
        # Log failed attempt (user not found)
        await _log_login_attempt(
            db, None, ip_address, user_agent, False, "User not found"
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Check if account is locked
    if user.is_locked and user.locked_until:
        if datetime.now(timezone.utc) < user.locked_until:
            remaining = (user.locked_until - datetime.now(timezone.utc)).seconds // 60
            await _log_login_attempt(
                db, user.id, ip_address, user_agent, False, "Account locked"
            )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Account is locked. Try again in {remaining} minutes.",
            )
        else:
            # Unlock the account
            user.is_locked = False
            user.failed_login_attempts = 0
            user.locked_until = None

    # Verify password
    if not verify_password(user_data.password, user.password_hash):
        # Increment failed login attempts
        user.failed_login_attempts += 1

        # Lock account if max attempts exceeded
        if user.failed_login_attempts >= settings.MAX_LOGIN_ATTEMPTS:
            user.is_locked = True
            user.locked_until = datetime.now(timezone.utc) + timedelta(
                minutes=settings.ACCOUNT_LOCK_MINUTES
            )
            await _log_login_attempt(
                db, user.id, ip_address, user_agent, False,
                f"Account locked after {settings.MAX_LOGIN_ATTEMPTS} failed attempts"
            )
            await db.commit()

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Account locked due to too many failed attempts. Try again in {settings.ACCOUNT_LOCK_MINUTES} minutes.",
            )

        await _log_login_attempt(
            db, user.id, ip_address, user_agent, False, "Invalid password"
        )
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Check if account is active
    if not user.is_active:
        await _log_login_attempt(
            db, user.id, ip_address, user_agent, False, "Account disabled"
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    # Check if account is pending approval
    if user.account_status == AccountStatus.PENDING_APPROVAL:
        await _log_login_attempt(
            db, user.id, ip_address, user_agent, False, "Account pending approval"
        )
        await db.commit()

        # Create a pending token for status checking
        pending_token = await create_pending_token(user.id)

        logger.info(
            f"Login attempt for pending user: id={user.id}, email={user.email}"
        )

        # Return 202 Accepted with pending approval info
        response.status_code = status.HTTP_202_ACCEPTED
        return PendingApprovalResponse(
            status="pending_approval",
            message="Your account is pending administrator approval. Please check back later.",
            pending_token=pending_token,
            email=user.email,
        )

    # Reset failed login attempts on successful login
    user.failed_login_attempts = 0
    user.is_locked = False
    user.locked_until = None

    # Auto-promote to admin if this is the only user in the system
    if user.role != UserRole.ADMIN:
        user_count_result = await db.execute(select(func.count(User.id)))
        total_users = user_count_result.scalar()
        if total_users == 1:
            user.role = UserRole.ADMIN
            logger.info(f"Auto-promoted user {user.id} ({user.email}) to admin (only user in system)")

    # Log successful login
    await _log_login_attempt(db, user.id, ip_address, user_agent, True)
    await db.commit()

    # Create tokens (now returns tuple of (token, jti))
    token_data = {"sub": str(user.id)}
    access_token, _ = create_access_token(token_data)
    refresh_token, _ = create_refresh_token(token_data)

    # Set refresh token as HttpOnly cookie with lax SameSite
    # Lax allows cookies on top-level navigation and safe HTTP methods (GET, POST)
    # This is needed for page refresh to work correctly while maintaining CSRF protection
    # Note: path="/" ensures the cookie is sent with all requests to the domain,
    # which is required for the refresh endpoint to receive the cookie after page reload
    # secure=False for HTTP deployments; set to True when HTTPS is configured
    cookie_secure = False
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=cookie_secure,
        samesite="lax",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path="/",
    )

    return TokenResponse(
        access_token=access_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
)
async def refresh_token(
    response: Response,
    user_and_payload: tuple[User, dict[str, Any]] = Depends(get_user_from_refresh_token),
):
    """
    Get new access token using refresh token from HttpOnly cookie.
    """
    user, old_token_payload = user_and_payload

    # Blacklist the old refresh token to prevent reuse
    old_jti = old_token_payload.get("jti")
    if old_jti:
        # Calculate remaining time until expiration
        exp = old_token_payload.get("exp")
        if exp:
            remaining = max(0, int(exp - datetime.now(timezone.utc).timestamp()))
            await add_token_to_blacklist(old_jti, remaining)

    # Create new tokens (now returns tuple of (token, jti))
    token_data = {"sub": str(user.id)}
    access_token, _ = create_access_token(token_data)
    new_refresh_token, _ = create_refresh_token(token_data)

    # Update refresh token cookie with lax SameSite
    # Note: path="/" ensures the cookie is sent with all requests to the domain,
    # which is required for the refresh endpoint to receive the cookie after page reload
    # In development, secure=False allows HTTP; in production, secure=True requires HTTPS
    is_production = settings.ENVIRONMENT == "production"
    # Force secure=False for HTTP deployments (remove this for production HTTPS)
    cookie_secure = False  # is_production
    response.set_cookie(
        key="refresh_token",
        value=new_refresh_token,
        httponly=True,
        secure=cookie_secure,
        samesite="lax",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path="/",
    )

    return TokenResponse(
        access_token=access_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Logout user",
)
async def logout(
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    _: User = Depends(get_current_user),
):
    """
    Logout user by invalidating tokens and clearing the refresh token cookie.
    """
    # Blacklist the access token
    if credentials:
        try:
            payload = decode_token(credentials.credentials)
            jti = payload.get("jti")
            if jti:
                exp = payload.get("exp")
                if exp:
                    remaining = max(0, int(exp - datetime.now(timezone.utc).timestamp()))
                    await add_token_to_blacklist(jti, remaining)
        except HTTPException:
            pass  # Token already invalid, proceed with logout

    # Clear refresh token cookie
    # Note: path must match the path used when setting the cookie
    response.delete_cookie(
        key="refresh_token",
        path="/",
    )

    return MessageResponse(message="Successfully logged out")


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user",
)
async def get_me(
    current_user: User = Depends(get_current_user),
):
    """
    Get current authenticated user's information.
    """
    return current_user


from app.core.security import get_key_rotation_info


@router.get(
    "/key-status",
    summary="Get JWT key rotation status (admin only)",
    response_model=dict,
)
async def get_key_status(
    current_user: User = Depends(get_current_user),
):
    """
    Get JWT key rotation status for monitoring.

    Only returns key fingerprints (safe for logs), not actual keys.
    Requires authentication.
    """
    # TODO: Add admin check if needed
    # For now, any authenticated user can view this info
    return get_key_rotation_info()


@router.post(
    "/check-status",
    response_model=CheckStatusResponse,
    summary="Check account approval status",
    dependencies=[Depends(rate_limit(max_requests=10, window_seconds=60, key_prefix="auth:check-status"))],
)
async def check_status(
    data: CheckStatusRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Check the approval status of a pending account.

    This endpoint allows users with pending accounts to check if their
    account has been approved or rejected without needing to attempt login.

    Rate limited to 10 requests per minute per IP.

    - **email**: User's email address
    - **pending_token**: Token received during login attempt while pending
    """
    # Find user by email
    result = await db.execute(
        select(User).where(User.email == data.email)
    )
    user = result.scalar_one_or_none()

    if user is None:
        # Don't reveal if email exists or not
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    # Validate the pending token using constant-time comparison
    is_valid = await validate_pending_token(data.pending_token, user.id)
    if not is_valid:
        logger.warning(
            f"Invalid pending token for user {user.id} ({user.email})"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token",
        )

    # Return status based on account state
    if user.account_status == AccountStatus.PENDING_APPROVAL:
        logger.debug(f"Status check: user {user.id} still pending approval")
        return CheckStatusResponse(
            status="pending_approval",
            message="Your account is still pending administrator approval.",
        )
    elif user.account_status == AccountStatus.ACTIVE and user.is_active:
        # Clear the pending token since account is now active
        await clear_pending_token(user.id)
        logger.info(f"Status check: user {user.id} ({user.email}) is now approved")
        return CheckStatusResponse(
            status="active",
            message="Your account has been approved. You can now log in.",
        )
    else:
        # Account was rejected (is_active=False or suspended)
        # Clear the pending token
        await clear_pending_token(user.id)
        logger.info(
            f"Status check: user {user.id} ({user.email}) was rejected "
            f"(is_active={user.is_active}, status={user.account_status.value})"
        )
        return CheckStatusResponse(
            status="rejected",
            message="Your account registration was not approved.",
        )
