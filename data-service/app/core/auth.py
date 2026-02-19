"""Service-to-service authentication via X-Internal-Token header.

Uses HMAC constant-time comparison to prevent timing attacks.
If INTERNAL_API_TOKEN is not configured, authentication is disabled
to allow local development without token setup.
"""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from app.config import get_settings


async def verify_internal_token(request: Request) -> None:
    """Verify X-Internal-Token header for service-to-service auth.

    Raises:
        HTTPException: 401 if the token is missing or invalid.
    """
    settings = get_settings()

    # Auth disabled if no token configured (local dev)
    if not settings.INTERNAL_API_TOKEN:
        return

    token = request.headers.get("X-Internal-Token", "")
    if not token or not hmac.compare_digest(token, settings.INTERNAL_API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid internal token")
