"""Stock analysis agent prompt templates.

This module contains prompts for:
- Fundamental analysis
- Technical analysis
- Sentiment analysis
- News analysis

Plus sanitization utilities for safe prompt construction.
"""

from app.prompts.analysis.fundamental_prompt import (
    build_fundamental_prompt,
    get_system_prompt as get_fundamental_system_prompt,
)
from app.prompts.analysis.technical_prompt import (
    build_technical_prompt,
    get_system_prompt as get_technical_system_prompt,
)
from app.prompts.analysis.sentiment_prompt import (
    build_sentiment_prompt,
    get_system_prompt as get_sentiment_system_prompt,
)
from app.prompts.analysis.news_prompt import (
    build_news_analysis_prompt as build_news_prompt,
    get_news_analysis_system_prompt as get_news_system_prompt,
)
from app.prompts.analysis.sanitizer import (
    sanitize_dict_values,
    sanitize_input,
    sanitize_market,
    sanitize_news_article,
    sanitize_symbol,
)

__all__ = [
    # Fundamental analysis
    "build_fundamental_prompt",
    "get_fundamental_system_prompt",
    # Technical analysis
    "build_technical_prompt",
    "get_technical_system_prompt",
    # Sentiment analysis
    "build_sentiment_prompt",
    "get_sentiment_system_prompt",
    # News analysis
    "build_news_prompt",
    "get_news_system_prompt",
    # Sanitization utilities
    "sanitize_input",
    "sanitize_symbol",
    "sanitize_market",
    "sanitize_dict_values",
    "sanitize_news_article",
]
