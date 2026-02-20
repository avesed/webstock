"""ASGI middleware for request ID propagation."""

from app.core.request_id import generate_request_id, request_id_var


class RequestIdMiddleware:
    """Pure ASGI middleware that sets a request ID for each request.

    Reads from X-Request-ID header (set by nginx) or generates a new one.
    Stores in context var and adds to response headers.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Extract request ID from headers or generate
        headers = dict(scope.get("headers", []))
        rid = (headers.get(b"x-request-id", b"") or b"").decode() or generate_request_id()

        token = request_id_var.set(rid)

        async def send_with_rid(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", rid.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_rid)
        finally:
            request_id_var.reset(token)
