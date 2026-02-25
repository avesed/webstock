"""Internal service authentication — pure ASGI middleware.

Uses raw ASGI instead of BaseHTTPMiddleware to preserve SSE streaming.
BaseHTTPMiddleware buffers the entire response body, breaking
text/event-stream responses.
"""

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings

logger = logging.getLogger(__name__)


class InternalAuthMiddleware:
    """Verify X-Internal-Token header on all routes except /health.

    Pure ASGI middleware — does NOT buffer response body, so SSE
    streaming works correctly.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in ("/health", "/health/ready"):
            await self.app(scope, receive, send)
            return

        if not settings.INTERNAL_API_TOKEN:
            # No token configured = auth disabled (development mode)
            await self.app(scope, receive, send)
            return

        # Extract X-Internal-Token from headers
        headers = dict(
            (k.decode("latin-1").lower(), v.decode("latin-1"))
            for k, v in scope.get("headers", [])
        )
        token = headers.get("x-internal-token", "")

        if token != settings.INTERNAL_API_TOKEN:
            client_host = "unknown"
            if scope.get("client"):
                client_host = scope["client"][0]
            logger.warning(
                "Auth failed: invalid X-Internal-Token from %s", client_host,
            )
            response = JSONResponse(
                status_code=401, content={"detail": "Invalid internal token"}
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
