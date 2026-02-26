"""Internal service authentication -- pure ASGI middleware.

Uses raw ASGI instead of BaseHTTPMiddleware to preserve SSE streaming.
BaseHTTPMiddleware buffers the entire response body, breaking
text/event-stream responses.

Skip paths:
  - /health, /health/ready  (health checks from Docker/orchestrator)
  - /v1/llm/ prefix         (LLM proxy called by RD-Agent subprocess
                              which does not carry an internal token)
"""

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import get_settings

logger = logging.getLogger(__name__)

# Paths that bypass authentication entirely
_SKIP_EXACT: set[str] = {"/health", "/health/ready"}
_SKIP_PREFIXES: tuple[str, ...] = ("/v1/llm/",)


class InternalAuthMiddleware:
    """Verify X-Internal-Token header on all routes except health and LLM proxy.

    Pure ASGI middleware -- does NOT buffer response body, so SSE
    streaming works correctly.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")

        # Skip authentication for health checks
        if path in _SKIP_EXACT:
            await self.app(scope, receive, send)
            return

        # Skip authentication for LLM proxy only from loopback
        # (called by RD-Agent subprocess on the same host)
        if path.startswith(_SKIP_PREFIXES):
            client = scope.get("client")
            client_host = client[0] if client else ""
            if client_host in ("127.0.0.1", "::1"):
                await self.app(scope, receive, send)
                return
            # Non-loopback requests to /v1/llm/ fall through to token check

        settings = get_settings()

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
                "Auth failed: invalid X-Internal-Token from %s on %s",
                client_host,
                path,
            )
            response = JSONResponse(
                status_code=401, content={"detail": "Invalid internal token"}
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
