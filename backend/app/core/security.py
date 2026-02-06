"""Security utilities for JWT and password hashing."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.database import get_db
from app.db.redis import get_redis
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

# Token blacklist key prefix
TOKEN_BLACKLIST_PREFIX = "token:blacklist:"

# Password hashing context with bcrypt
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=settings.BCRYPT_ROUNDS,
)

# HTTP Bearer token scheme
bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash."""
    return pwd_context.verify(plain_password, hashed_password)


async def add_token_to_blacklist(token_id: str, expires_in: int) -> None:
    """
    Add a token to the blacklist in Redis.

    Args:
        token_id: The unique JWT ID (jti claim)
        expires_in: Time in seconds until the token expires
    """
    redis_client = await get_redis()
    key = f"{TOKEN_BLACKLIST_PREFIX}{token_id}"
    # Set the key with expiration matching the token expiration
    await redis_client.setex(key, expires_in, "1")


async def is_token_blacklisted(token_id: str) -> bool:
    """
    Check if a token is blacklisted.

    Args:
        token_id: The unique JWT ID (jti claim)

    Returns:
        True if the token is blacklisted, False otherwise
    """
    redis_client = await get_redis()
    key = f"{TOKEN_BLACKLIST_PREFIX}{token_id}"
    result = await redis_client.exists(key)
    return bool(result)


def _get_jwt_key_for_signing() -> str:
    """Get JWT key for signing tokens. Uses environment variable."""
    # For signing, we always use the primary key from environment
    # This ensures consistency even during rotation
    return settings.JWT_SECRET_KEY


def create_access_token(
    data: dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> tuple[str, str]:
    """
    Create JWT access token with unique jti claim.

    Returns:
        Tuple of (token, jti) where jti is the unique token identifier
    """
    to_encode = data.copy()
    jti = str(uuid.uuid4())

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode.update({
        "exp": expire,
        "type": "access",
        "jti": jti,
    })

    # Use primary key for signing
    key = _get_jwt_key_for_signing()
    token = jwt.encode(
        to_encode,
        key,
        algorithm=settings.JWT_ALGORITHM,
    )
    return token, jti


def create_refresh_token(
    data: dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> tuple[str, str]:
    """
    Create JWT refresh token with unique jti claim.

    Returns:
        Tuple of (token, jti) where jti is the unique token identifier
    """
    to_encode = data.copy()
    jti = str(uuid.uuid4())

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS
        )

    to_encode.update({
        "exp": expire,
        "type": "refresh",
        "jti": jti,
    })

    token = jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return token, jti


def _try_decode_with_key(token: str, key: str) -> Optional[dict[str, Any]]:
    """Try to decode token with a specific key."""
    try:
        payload = jwt.decode(token, key, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


def decode_token_sync(token: str) -> dict[str, Any]:
    """
    Decode and validate JWT token (synchronous version).
    
    First tries environment variables, then falls back to settings.
    Supports key rotation with previous keys.
    """
    import os
    
    # Get keys from environment (which may have been updated)
    primary_key = os.environ.get("JWT_SECRET_KEY") or settings.JWT_SECRET_KEY
    previous_keys_str = os.environ.get("JWT_SECRET_KEY_PREVIOUS", "")
    
    # Parse previous keys
    previous_keys = []
    if previous_keys_str:
        previous_keys = [k.strip() for k in previous_keys_str.split(",") if k.strip()]
    # Also include keys from settings as fallback
    for key in settings.jwt_previous_keys:
        if key not in previous_keys:
            previous_keys.append(key)
    
    # Try primary key first
    payload = _try_decode_with_key(token, primary_key)
    if payload:
        return payload
    
    # Try previous keys (for smooth rotation)
    for prev_key in previous_keys:
        if prev_key:  # Ensure key is not empty
            payload = _try_decode_with_key(token, prev_key)
            if payload:
                return payload
    
    # None of the keys worked
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token: Signature verification failed.",
        headers={"WWW-Authenticate": "Bearer"},
    )


# Keep the old name for backwards compatibility
decode_token = decode_token_sync


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Get current authenticated user from JWT token."""
    logger.debug("[Auth] get_current_user called, credentials present: %s", credentials is not None)
    
    if credentials is None:
        logger.warning("[Auth] No credentials provided - Authorization header missing")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(credentials.credentials)

    # Verify token type
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if token is blacklisted
    jti = payload.get("jti")
    if jti and await is_token_blacklisted(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Fetch user from database
    result = await db.execute(
        select(User).where(User.id == int(user_id))
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    if user.is_locked and user.locked_until:
        if datetime.now(timezone.utc) < user.locked_until:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is temporarily locked",
            )

    return user


async def get_user_from_refresh_token(
    refresh_token: Optional[str] = Cookie(None, alias="refresh_token"),
    db: AsyncSession = Depends(get_db),
) -> tuple[User, dict[str, Any]]:
    """
    Get user from refresh token in HttpOnly cookie.

    Returns:
        Tuple of (user, token_payload) for blacklisting the old token
    """
    if refresh_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token not found",
        )

    payload = decode_token(refresh_token)

    # Verify token type
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    # Check if token is blacklisted
    jti = payload.get("jti")
    if jti and await is_token_blacklisted(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    # Fetch user from database
    result = await db.execute(
        select(User).where(User.id == int(user_id))
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    return user, payload


def get_key_rotation_info() -> dict[str, Any]:
    """
    Get information about current key configuration for monitoring.

    Returns:
        Dict with key info (safe for logging, no actual key values)
    """
    primary = settings.JWT_SECRET_KEY
    previous = settings.jwt_previous_keys

    return {
        "primary_key_fingerprint": f"{primary[:8]}...{primary[-8:]}" if len(primary) > 16 else "[hidden]",
        "primary_key_length": len(primary),
        "previous_keys_count": len(previous),
        "previous_key_fingerprints": [
            f"{k[:8]}...{k[-8:]}" if len(k) > 16 else "[hidden]"
            for k in previous
        ],
        "algorithm": settings.JWT_ALGORITHM,
        "access_token_expire_minutes": settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
        "refresh_token_expire_days": settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS,
    }


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Dependency that requires the current user to be an administrator.

    Use this dependency on endpoints that should only be accessible to admin users.
    It first validates that the user is authenticated via get_current_user,
    then checks if they have the ADMIN role.

    Args:
        current_user: The authenticated user from get_current_user dependency

    Returns:
        The authenticated admin user

    Raises:
        HTTPException: 401 if user is not authenticated (from get_current_user)
        HTTPException: 403 if user is not an admin

    Example:
        @router.get("/admin/settings")
        async def get_admin_settings(
            admin: User = Depends(require_admin),
        ):
            return {"message": "Admin access granted"}
    """
    if current_user.role != UserRole.ADMIN:
        logger.warning(
            f"Non-admin user {current_user.id} ({current_user.email}) "
            f"attempted to access admin-only resource"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )

    logger.debug(f"Admin access granted for user {current_user.id}")
    return current_user
