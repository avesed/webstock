"""Input sanitization for AI prompts to prevent prompt injection attacks."""

import re
from typing import Any, Dict, Optional

# Maximum lengths for different input types
MAX_SYMBOL_LENGTH = 20
MAX_TEXT_LENGTH = 1000
MAX_DESCRIPTION_LENGTH = 500
MAX_NEWS_SUMMARY_LENGTH = 200

# Patterns that could indicate prompt injection attempts
INJECTION_PATTERNS = [
    # System/instruction override attempts
    r"(?i)\b(SYSTEM|ASSISTANT|USER|HUMAN|AI):\s*",
    r"(?i)\bIGNORE\s+(ALL\s+)?(PREVIOUS|ABOVE|PRIOR)\b",
    r"(?i)\bFORGET\s+(ALL\s+)?(PREVIOUS|ABOVE|PRIOR|EVERYTHING)\b",
    r"(?i)\bDISREGARD\s+(ALL\s+)?(PREVIOUS|ABOVE|PRIOR)\b",
    r"(?i)\bOVERRIDE\s+(ALL\s+)?(PREVIOUS|INSTRUCTIONS?)\b",
    r"(?i)\bNEW\s+INSTRUCTIONS?\b",
    r"(?i)\bACT\s+AS\s+(IF\s+)?(YOU\s+)?(ARE|WERE)\b",
    r"(?i)\bPRETEND\s+(TO\s+BE|YOU\s+ARE)\b",
    r"(?i)\bYOU\s+ARE\s+NOW\b",
    r"(?i)\bSWITCH\s+TO\s+(A\s+)?NEW\s+",
    r"(?i)\bRESET\s+(YOUR\s+)?(CONTEXT|INSTRUCTIONS?)\b",
    # Delimiter injection
    r"```\s*(system|instructions?|config)",
    r"<\s*(system|instructions?|prompt)",
    r"\[\s*(SYSTEM|INST)\s*\]",
    r"###\s*(SYSTEM|INSTRUCTIONS?|NEW)\b",
]

# Compile patterns for efficiency
COMPILED_INJECTION_PATTERNS = [re.compile(pattern) for pattern in INJECTION_PATTERNS]


def sanitize_input(
    value: Optional[str],
    max_length: int = MAX_TEXT_LENGTH,
    field_name: str = "input",
) -> str:
    """
    Sanitize user-provided input for safe inclusion in prompts.

    Args:
        value: The input string to sanitize
        max_length: Maximum allowed length
        field_name: Name of the field (for logging/error messages)

    Returns:
        Sanitized string safe for prompt inclusion
    """
    if value is None:
        return "N/A"

    # Convert to string if needed
    if not isinstance(value, str):
        value = str(value)

    # Remove null bytes and other control characters (except newlines/tabs)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)

    # Strip leading/trailing whitespace
    value = value.strip()

    # Truncate to max length
    if len(value) > max_length:
        value = value[:max_length] + "..."

    # Remove potential injection patterns
    for pattern in COMPILED_INJECTION_PATTERNS:
        value = pattern.sub("[FILTERED]", value)

    return value


def sanitize_symbol(symbol: Optional[str]) -> str:
    """
    Sanitize stock symbol input.

    Supports various symbol formats:
    - US stocks: AAPL, MSFT
    - HK stocks: 0700.HK, 9988.HK
    - A-shares: 600519.SS, 000001.SZ
    - Precious metals futures: GC=F (Gold), SI=F (Silver), PL=F, PA=F

    Args:
        symbol: The stock symbol to sanitize

    Returns:
        Sanitized symbol string
    """
    if symbol is None:
        return "UNKNOWN"

    # Convert to string and uppercase
    symbol = str(symbol).strip().upper()

    # Remove any characters that aren't alphanumeric, dots, hyphens, or equals
    # The '=' is needed for futures symbols like GC=F, SI=F
    symbol = re.sub(r"[^A-Z0-9.\-=]", "", symbol)

    # Truncate to max symbol length
    if len(symbol) > MAX_SYMBOL_LENGTH:
        symbol = symbol[:MAX_SYMBOL_LENGTH]

    return symbol or "UNKNOWN"


def sanitize_market(market: Optional[str]) -> str:
    """
    Sanitize market identifier.

    Args:
        market: The market identifier to sanitize

    Returns:
        Sanitized market string (defaults to 'us' if invalid)
    """
    if market is None:
        return "us"

    # Convert to lowercase and strip
    market = str(market).strip().lower()

    # Only allow known market identifiers
    # Includes 'metal' for precious metals futures (GC=F, SI=F, etc.)
    valid_markets = {"us", "hk", "sh", "sz", "metal"}
    if market not in valid_markets:
        return "us"

    return market


def sanitize_numeric(
    value: Any,
    default: str = "N/A",
) -> str:
    """
    Sanitize numeric values for display in prompts.

    Args:
        value: The numeric value to sanitize
        default: Default value if input is invalid

    Returns:
        String representation of the number or default
    """
    if value is None:
        return default

    try:
        # Convert to float to validate
        num = float(value)
        # Check for invalid values
        if num != num:  # NaN check
            return default
        return str(value)
    except (ValueError, TypeError):
        return default


def sanitize_dict_values(
    data: Optional[Dict[str, Any]],
    text_fields: Optional[set] = None,
    max_text_length: int = MAX_TEXT_LENGTH,
) -> Dict[str, Any]:
    """
    Sanitize all string values in a dictionary.

    Args:
        data: Dictionary with potentially unsafe values
        text_fields: Set of field names to treat as text (longer content)
        max_text_length: Maximum length for text fields

    Returns:
        Dictionary with sanitized values
    """
    if data is None:
        return {}

    text_fields = text_fields or {"description", "summary", "content", "text"}
    sanitized = {}

    for key, value in data.items():
        if isinstance(value, str):
            # Determine max length based on field type
            if key in text_fields:
                max_len = max_text_length
            elif key == "description":
                max_len = MAX_DESCRIPTION_LENGTH
            else:
                max_len = MAX_TEXT_LENGTH

            sanitized[key] = sanitize_input(value, max_length=max_len, field_name=key)
        elif isinstance(value, dict):
            sanitized[key] = sanitize_dict_values(value, text_fields, max_text_length)
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_input(item, field_name=key) if isinstance(item, str) else item
                for item in value
            ]
        else:
            sanitized[key] = value

    return sanitized


def sanitize_news_article(article: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Sanitize a news article dictionary.

    Args:
        article: News article with title, source, summary, etc.

    Returns:
        Sanitized article dictionary
    """
    if article is None:
        return {}

    return {
        "title": sanitize_input(
            article.get("title"), max_length=200, field_name="title"
        ),
        "source": sanitize_input(
            article.get("source"), max_length=100, field_name="source"
        ),
        "published_at": sanitize_input(
            article.get("published_at"), max_length=50, field_name="published_at"
        ),
        "summary": sanitize_input(
            article.get("summary"),
            max_length=MAX_NEWS_SUMMARY_LENGTH,
            field_name="summary",
        ),
    }


def escape_markdown(text: str) -> str:
    """
    Escape markdown special characters in text.

    Args:
        text: Text that may contain markdown characters

    Returns:
        Text with markdown characters escaped
    """
    # Characters that have special meaning in markdown
    special_chars = ["*", "_", "`", "[", "]", "(", ")", "#", "+", "-", ".", "!"]

    for char in special_chars:
        text = text.replace(char, "\\" + char)

    return text
