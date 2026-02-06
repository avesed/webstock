"""Authentication API endpoints."""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
import logging

logger = logging.getLogger(__name__)
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
from app.models.user import User, UserRole
from app.models.login_log import LoginLog
from app.schemas.user import (
    MessageResponse,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)

bearer_scheme = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=UserResponse,
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

    # Create new user
    user = User(
        email=user_data.email,
        password_hash=hash_password(user_data.password),
    )

    db.add(user)
    await db.commit()
    await db.refresh(user)

    return user


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
    response_model=TokenResponse,
    summary="Login with email and password",
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
