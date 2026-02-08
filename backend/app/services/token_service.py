"""Token counting service using tiktoken for accurate token estimation.

This module provides accurate token counting using OpenAI's tiktoken library,
with support for multiple models and caching for performance.
"""

import logging
from functools import lru_cache
from typing import Any, Dict, List

import tiktoken

logger = logging.getLogger(__name__)

# Model to encoding mapping for common models
MODEL_ENCODING_MAP = {
    "gpt-4o": "o200k_base",
    "gpt-4o-mini": "o200k_base",
    "gpt-4-turbo": "cl100k_base",
    "gpt-4": "cl100k_base",
    "gpt-3.5-turbo": "cl100k_base",
    "text-embedding-3-small": "cl100k_base",
    "text-embedding-3-large": "cl100k_base",
}

# Default encoding for unknown models (covers most OpenAI-compatible APIs)
DEFAULT_ENCODING = "cl100k_base"


@lru_cache(maxsize=8)
def get_encoding(model: str) -> tiktoken.Encoding:
    """
    Get tiktoken encoding for model (cached).

    Attempts to get the encoding for the specific model first,
    then falls back to a mapping table, and finally to cl100k_base.

    Args:
        model: The model name (e.g., "gpt-4o", "gpt-4o-mini")

    Returns:
        The tiktoken Encoding object for token counting
    """
    # First, try to get encoding directly from tiktoken
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        pass

    # Try our manual mapping for newer models
    encoding_name = MODEL_ENCODING_MAP.get(model.lower())
    if encoding_name:
        try:
            return tiktoken.get_encoding(encoding_name)
        except Exception as e:
            logger.warning(
                f"Failed to get encoding {encoding_name} for model {model}: {e}"
            )

    # Fall back to cl100k_base (compatible with most modern models)
    logger.debug(f"Model {model} not found, using {DEFAULT_ENCODING} encoding")
    return tiktoken.get_encoding(DEFAULT_ENCODING)


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    """
    Count tokens in text for given model.

    Args:
        text: The text to count tokens for
        model: The model name to use for tokenization

    Returns:
        Number of tokens in the text
    """
    if not text:
        return 0

    encoding = get_encoding(model)
    return len(encoding.encode(text))


def count_message_tokens(
    messages: List[Dict[str, Any]],
    model: str = "gpt-4o"
) -> int:
    """
    Count tokens in OpenAI message format.

    This follows OpenAI's token counting guidelines for chat completions.
    Each message has overhead tokens for role and message formatting.

    Reference: https://github.com/openai/openai-cookbook/blob/main/examples/How_to_count_tokens_with_tiktoken.ipynb

    Args:
        messages: List of message dicts with 'role' and 'content' keys
        model: The model name to use for tokenization

    Returns:
        Total token count including message overhead
    """
    if not messages:
        return 0

    encoding = get_encoding(model)

    # Token overhead varies by model
    # For gpt-4o and gpt-3.5-turbo: 4 tokens per message + 2 for reply priming
    tokens_per_message = 4
    tokens_per_name = -1  # If name is present, role is omitted

    tokens = 0
    for message in messages:
        tokens += tokens_per_message
        for key, value in message.items():
            if value is None:
                continue
            if key == "name":
                tokens += tokens_per_name
            tokens += len(encoding.encode(str(value)))

    # Every reply is primed with <|start|>assistant<|message|>
    tokens += 2

    return tokens


def estimate_tokens_fast(text: str) -> int:
    """
    Fast token estimation without tiktoken (for rate limiting decisions).

    Uses a simple heuristic: ~4 characters per token for English,
    ~2 characters per token for CJK characters.

    This is much faster than tiktoken but less accurate.
    Use for quick estimates when precision is not critical.

    Args:
        text: The text to estimate tokens for

    Returns:
        Estimated token count
    """
    if not text:
        return 0

    # Count CJK characters (Chinese, Japanese, Korean)
    cjk_chars = sum(
        1 for c in text
        if ('\u4e00' <= c <= '\u9fff' or  # CJK Unified
            '\u3040' <= c <= '\u30ff' or  # Hiragana + Katakana
            '\uac00' <= c <= '\ud7af')    # Hangul
    )
    other_chars = len(text) - cjk_chars

    # CJK: ~2 chars per token, Other: ~4 chars per token
    return max(1, (cjk_chars // 2) + (other_chars // 4))


def truncate_to_token_limit(
    text: str,
    max_tokens: int,
    model: str = "gpt-4o"
) -> str:
    """
    Truncate text to fit within a token limit.

    Args:
        text: The text to truncate
        max_tokens: Maximum number of tokens allowed
        model: The model name to use for tokenization

    Returns:
        Truncated text that fits within the token limit
    """
    if not text:
        return text

    encoding = get_encoding(model)
    tokens = encoding.encode(text)

    if len(tokens) <= max_tokens:
        return text

    # Truncate and decode
    truncated_tokens = tokens[:max_tokens]
    return encoding.decode(truncated_tokens)


def clear_encoding_cache() -> None:
    """
    Clear the encoding cache.

    Useful for testing or when switching between many different models.
    """
    get_encoding.cache_clear()
    logger.debug("Encoding cache cleared")
