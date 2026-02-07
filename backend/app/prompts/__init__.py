"""Unified prompt templates for all AI features.

This module centralizes all LLM prompts for:
- Chat: AI chat assistant prompts
- Analysis: Stock analysis agent prompts (fundamental, technical, sentiment, news)
- News Filter: News relevance evaluation prompts

Structure:
    prompts/
    ├── __init__.py              # This file - main exports
    ├── chat_prompt.py           # AI chat assistant prompts
    ├── news_filter_prompt.py    # News relevance evaluation prompts
    └── analysis/                # Stock analysis agent prompts
        ├── __init__.py
        ├── fundamental_prompt.py
        ├── technical_prompt.py
        ├── sentiment_prompt.py
        ├── news_prompt.py
        └── sanitizer.py
"""

from app.prompts.chat_prompt import (
    build_chat_system_prompt,
    CHAT_SYSTEM_PROMPT_EN,
    CHAT_SYSTEM_PROMPT_ZH,
)
from app.prompts.news_filter_prompt import (
    NEWS_FILTER_SYSTEM_PROMPT,
    NEWS_FILTER_USER_PROMPT,
)

# Re-export analysis prompts
from app.prompts.analysis import (
    build_fundamental_prompt,
    get_fundamental_system_prompt,
    build_technical_prompt,
    get_technical_system_prompt,
    build_sentiment_prompt,
    get_sentiment_system_prompt,
    build_news_prompt,
    get_news_system_prompt,
    # Sanitization utilities
    sanitize_input,
    sanitize_symbol,
    sanitize_market,
    sanitize_dict_values,
    sanitize_news_article,
)

__all__ = [
    # Chat prompts
    "build_chat_system_prompt",
    "CHAT_SYSTEM_PROMPT_EN",
    "CHAT_SYSTEM_PROMPT_ZH",
    # News filter prompts
    "NEWS_FILTER_SYSTEM_PROMPT",
    "NEWS_FILTER_USER_PROMPT",
    # Analysis prompts
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
