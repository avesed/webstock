"""Per-request ID via context variables for distributed tracing."""

import contextvars
import uuid
from typing import Optional

request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id", default=None
)


def generate_request_id() -> str:
    """Generate a new request ID (UUID4 hex, 32 chars)."""
    return uuid.uuid4().hex


def get_request_id() -> Optional[str]:
    """Get the full request ID for the current context."""
    return request_id_var.get()


def get_request_id_short() -> str:
    """Get first 8 chars of request ID for log output, or '-' if unset."""
    rid = request_id_var.get()
    return rid[:8] if rid else "-"
