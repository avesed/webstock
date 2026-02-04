"""Dynamic JWT key management using Redis.

This module provides a Redis-based JWT key store that allows:
1. Dynamic key rotation without restarting services
2. Shared key state across all backend instances
3. Smooth transition with previous key support
"""

import os
from typing import Optional

from app.db.redis import get_redis

# Redis keys for JWT key storage
JWT_PRIMARY_KEY = "jwt:key:primary"
JWT_PREVIOUS_KEYS = "jwt:key:previous"
JWT_KEY_UPDATED_AT = "jwt:key:updated_at"

# Local cache (will be refreshed from Redis periodically)
_local_primary_key: Optional[str] = None
_local_previous_keys: Optional[list[str]] = None


async def get_jwt_keys_from_redis() -> tuple[str, list[str]]:
    """
    Get current JWT keys from Redis.
    
    Returns:
        Tuple of (primary_key, previous_keys_list)
    """
    redis_client = await get_redis()
    
    # Get primary key
    primary = await redis_client.get(JWT_PRIMARY_KEY)
    
    # Get previous keys
    previous_str = await redis_client.get(JWT_PREVIOUS_KEYS)
    previous = []
    if previous_str:
        previous = [k.strip() for k in previous_str.split(",") if k.strip()]
    
    # If no keys in Redis, use environment variables and store them
    if not primary:
        primary = os.environ.get("JWT_SECRET_KEY", "")
        if primary and primary != "change-me-in-production":
            await redis_client.set(JWT_PRIMARY_KEY, primary)
            
            # Also store previous keys if they exist
            previous_env = os.environ.get("JWT_SECRET_KEY_PREVIOUS", "")
            if previous_env:
                await redis_client.set(JWT_PREVIOUS_KEYS, previous_env)
                previous = [k.strip() for k in previous_env.split(",") if k.strip()]
    
    return primary or "", previous


async def set_jwt_keys_in_redis(primary: str, previous: list[str]) -> None:
    """
    Set new JWT keys in Redis.
    
    This updates the keys for all backend instances immediately.
    """
    redis_client = await get_redis()
    
    # Store primary key
    await redis_client.set(JWT_PRIMARY_KEY, primary)
    
    # Store previous keys
    if previous:
        await redis_client.set(JWT_PREVIOUS_KEYS, ",".join(previous))
    else:
        await redis_client.delete(JWT_PREVIOUS_KEYS)
    
    # Update timestamp
    from datetime import datetime
    await redis_client.set(JWT_KEY_UPDATED_AT, datetime.now().isoformat())


async def get_current_jwt_keys() -> tuple[str, list[str]]:
    """
    Get current JWT keys (from Redis or environment).
    
    This is the main function to use when you need JWT keys.
    It automatically fetches from Redis with fallback to environment.
    """
    return await get_jwt_keys_from_redis()


async def rotate_jwt_keys() -> dict:
    """
    Perform JWT key rotation using Redis.
    
    Returns:
        Dict with rotation result information
    """
    import secrets
    from datetime import datetime
    
    # Get current keys
    current_primary, current_previous = await get_jwt_keys_from_redis()
    
    # Generate new key
    new_primary = secrets.token_hex(32)
    
    # Build new previous keys list (keep last 2)
    new_previous = []
    if current_primary:
        new_previous.append(current_primary)
    if current_previous and len(current_previous) > 0:
        new_previous.extend(current_previous[:1])
    
    # Store in Redis
    await set_jwt_keys_in_redis(new_primary, new_previous)
    
    # Also update local environment for this process
    os.environ["JWT_SECRET_KEY"] = new_primary
    os.environ["JWT_SECRET_KEY_PREVIOUS"] = ",".join(new_previous)
    
    timestamp = datetime.now().isoformat()
    
    return {
        "status": "success",
        "timestamp": timestamp,
        "new_primary_fingerprint": f"{new_primary[:8]}...{new_primary[-8:]}",
        "previous_keys_count": len(new_previous),
        "message": "Keys rotated successfully. All backend instances will use new keys immediately.",
    }


async def get_key_status() -> dict:
    """Get current JWT key status from Redis."""
    primary, previous = await get_jwt_keys_from_redis()
    
    redis_client = await get_redis()
    updated_at = await redis_client.get(JWT_KEY_UPDATED_AT)
    
    return {
        "primary_key_fingerprint": f"{primary[:8]}...{primary[-8:]}" if len(primary) > 16 else "[hidden]",
        "primary_key_length": len(primary),
        "previous_keys_count": len(previous),
        "previous_key_fingerprints": [
            f"{k[:8]}...{k[-8:]}" if len(k) > 16 else "[hidden]"
            for k in previous
        ],
        "updated_at": updated_at or "unknown",
        "source": "redis",
    }
