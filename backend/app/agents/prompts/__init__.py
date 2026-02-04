"""Agent prompt templates."""

from app.agents.prompts.fundamental_prompt import (
    build_fundamental_prompt,
    get_system_prompt as get_fundamental_system_prompt,
)
from app.agents.prompts.sanitizer import (
    sanitize_dict_values,
    sanitize_input,
    sanitize_market,
    sanitize_news_article,
    sanitize_symbol,
)
from app.agents.prompts.sentiment_prompt import (
    build_sentiment_prompt,
    get_system_prompt as get_sentiment_system_prompt,
)
from app.agents.prompts.technical_prompt import (
    build_technical_prompt,
    get_system_prompt as get_technical_system_prompt,
)

__all__ = [
    "build_fundamental_prompt",
    "get_fundamental_system_prompt",
    "build_technical_prompt",
    "get_technical_system_prompt",
    "build_sentiment_prompt",
    "get_sentiment_system_prompt",
    # Sanitization utilities
    "sanitize_input",
    "sanitize_symbol",
    "sanitize_market",
    "sanitize_dict_values",
    "sanitize_news_article",
]
