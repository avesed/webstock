"""Per-request user AI configuration via context variables."""

import contextvars
from dataclasses import dataclass
from typing import Optional


@dataclass
class UserAIConfig:
    """User-specific AI configuration for the current request."""

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    system_prompt: Optional[str] = None
    anthropic_api_key: Optional[str] = None


# Context variable holding user AI config for the current request
current_user_ai_config: contextvars.ContextVar[Optional[UserAIConfig]] = (
    contextvars.ContextVar("current_user_ai_config", default=None)
)
