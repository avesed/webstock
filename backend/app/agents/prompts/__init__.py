"""Agent prompt templates.

DEPRECATED: This module is maintained for backward compatibility.
Please import from app.prompts instead.

Example:
    # Old (deprecated):
    from app.agents.prompts import build_fundamental_prompt

    # New (recommended):
    from app.prompts import build_fundamental_prompt
"""

# Re-export everything from the new prompts module for backward compatibility
from app.prompts.analysis import (
    build_fundamental_prompt,
    get_fundamental_system_prompt,
    build_technical_prompt,
    get_technical_system_prompt,
    build_sentiment_prompt,
    get_sentiment_system_prompt,
    build_news_prompt,
    get_news_system_prompt,
    sanitize_dict_values,
    sanitize_input,
    sanitize_market,
    sanitize_news_article,
    sanitize_symbol,
)

__all__ = [
    "build_fundamental_prompt",
    "get_fundamental_system_prompt",
    "build_technical_prompt",
    "get_technical_system_prompt",
    "build_sentiment_prompt",
    "get_sentiment_system_prompt",
    "build_news_prompt",
    "get_news_system_prompt",
    # Sanitization utilities
    "sanitize_input",
    "sanitize_symbol",
    "sanitize_market",
    "sanitize_dict_values",
    "sanitize_news_article",
]
