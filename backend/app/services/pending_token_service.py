"""Service for managing pending approval tokens in Redis.

This module provides functions for creating, validating, and clearing
tokens used during the user registration approval workflow.
"""

import logging
import secrets

from app.db.redis import get_redis

logger = logging.getLogger(__name__)

# Redis key prefixes for pending tokens
PENDING_TOKEN_PREFIX = "pending:token:"
PENDING_USER_PREFIX = "pending:user:"
PENDING_TOKEN_TTL = 172800  # 48 hours in seconds


async def create_pending_token(user_id: int) -> str:
    """
    Create a pending approval token and store in Redis.

    Args:
        user_id: The ID of the user needing a pending token

    Returns:
        The generated token string
    """
    redis = await get_redis()
    token = secrets.token_urlsafe(32)

    # Store token -> user_id mapping
    token_key = f"{PENDING_TOKEN_PREFIX}{token}"
    await redis.setex(token_key, PENDING_TOKEN_TTL, str(user_id))

    # Store user_id -> token mapping (for cleanup when approved/rejected)
    user_key = f"{PENDING_USER_PREFIX}{user_id}"
    await redis.setex(user_key, PENDING_TOKEN_TTL, token)

    logger.debug(f"Created pending token for user {user_id}")
    return token


async def validate_pending_token(token: str, user_id: int) -> bool:
    """
    Validate a pending token matches the expected user.

    Uses constant-time comparison to prevent timing attacks.

    Args:
        token: The token to validate
        user_id: The expected user ID

    Returns:
        True if the token is valid for the given user, False otherwise
    """
    redis = await get_redis()
    token_key = f"{PENDING_TOKEN_PREFIX}{token}"
    stored_user_id = await redis.get(token_key)

    if not stored_user_id:
        logger.debug(f"Pending token validation failed: token not found in Redis")
        return False

    is_valid = int(stored_user_id) == user_id
    if is_valid:
        logger.debug(f"Pending token validated successfully for user {user_id}")
    else:
        logger.warning(
            f"Pending token validation failed: user_id mismatch "
            f"(expected={user_id}, stored={stored_user_id})"
        )

    return is_valid


async def clear_pending_token(user_id: int) -> None:
    """
    Clear pending tokens for a user from Redis.

    Should be called when a user is approved, rejected, or their account
    becomes active through other means.

    Args:
        user_id: The ID of the user whose tokens should be cleared
    """
    redis = await get_redis()

    # Get the token for this user
    user_key = f"{PENDING_USER_PREFIX}{user_id}"
    token = await redis.get(user_key)

    if token:
        # Delete the token -> user_id mapping
        token_key = f"{PENDING_TOKEN_PREFIX}{token}"
        await redis.delete(token_key)
        logger.debug(f"Cleared pending token for user {user_id}")
    else:
        logger.debug(f"No pending token found for user {user_id}")

    # Delete the user_id -> token mapping
    await redis.delete(user_key)
