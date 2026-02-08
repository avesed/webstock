"""JSON extraction utilities for LLM responses.

This module handles extracting structured JSON data from LLM responses,
including handling of thinking/reasoning tags that some models use.
"""

import json
import logging
import re
from typing import Any, Dict, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Patterns for thinking/reasoning tags used by various models
THINKING_TAG_PATTERNS = [
    # DeepSeek, Qwen thinking tags
    re.compile(r"<think>.*?</think>", re.DOTALL),
    re.compile(r"<thinking>.*?</thinking>", re.DOTALL),
    # Reasoning tags
    re.compile(r"<reasoning>.*?</reasoning>", re.DOTALL),
    re.compile(r"<reason>.*?</reason>", re.DOTALL),
    # Internal monologue tags
    re.compile(r"<internal>.*?</internal>", re.DOTALL),
    re.compile(r"<scratchpad>.*?</scratchpad>", re.DOTALL),
]

# Pattern for JSON in markdown code blocks
JSON_CODE_BLOCK_PATTERN = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?```",
    re.DOTALL
)

# Pattern for standalone JSON objects
JSON_OBJECT_PATTERN = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)


def strip_thinking_tags(content: str) -> str:
    """
    Remove thinking/reasoning tags from LLM response.

    Some models (like DeepSeek, Qwen) use special tags for chain-of-thought
    reasoning that should be stripped before extracting the actual response.

    Args:
        content: Raw LLM response content

    Returns:
        Content with thinking tags removed
    """
    if not content:
        return content

    result = content
    for pattern in THINKING_TAG_PATTERNS:
        result = pattern.sub("", result)

    return result.strip()


def extract_json_from_response(content: str) -> Dict[str, Any]:
    """
    Extract JSON from LLM response, handling thinking tags and code blocks.

    This function handles multiple response formats:
    1. JSON in markdown code blocks (```json ... ```)
    2. Raw JSON objects in the response
    3. Responses with thinking/reasoning tags that should be stripped

    Args:
        content: Raw LLM response content

    Returns:
        Parsed JSON as a dictionary

    Raises:
        ValueError: If no valid JSON can be extracted from the response
    """
    if not content:
        raise ValueError("Empty response content")

    # Step 1: Remove thinking/reasoning tags
    cleaned_content = strip_thinking_tags(content)

    if not cleaned_content:
        raise ValueError("Response content is empty after removing thinking tags")

    # Step 2: Try to extract JSON from markdown code block
    code_block_match = JSON_CODE_BLOCK_PATTERN.search(cleaned_content)
    if code_block_match:
        json_str = code_block_match.group(1).strip()
        try:
            result = json.loads(json_str)
            logger.debug("Successfully parsed JSON from markdown code block")
            return result
        except json.JSONDecodeError as e:
            logger.debug(f"Failed to parse JSON from code block: {e}")

    # Step 3: Try to find and parse a JSON object directly
    # Find all potential JSON objects
    potential_jsons = JSON_OBJECT_PATTERN.findall(cleaned_content)

    for json_str in potential_jsons:
        try:
            # Try to parse, handling nested objects
            result = _try_parse_json(json_str)
            if result is not None:
                logger.debug("Successfully parsed JSON from regex pattern match")
                return result
        except (json.JSONDecodeError, ValueError):
            continue

    # Step 4: Try parsing the entire cleaned content as JSON
    try:
        result = json.loads(cleaned_content)
        logger.debug("Successfully parsed entire cleaned content as JSON")
        return result
    except json.JSONDecodeError:
        pass

    # Step 5: More aggressive extraction - find first { and last }
    first_brace = cleaned_content.find("{")
    last_brace = cleaned_content.rfind("}")

    if first_brace != -1 and last_brace > first_brace:
        json_str = cleaned_content[first_brace:last_brace + 1]
        try:
            result = json.loads(json_str)
            logger.debug("Successfully parsed JSON using brace extraction method")
            return result
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse extracted JSON: {e}")

    # Log response preview at WARNING level when all methods fail
    preview_length = min(500, len(cleaned_content))
    logger.warning(
        f"All JSON parsing methods failed. Response preview ({preview_length} chars): "
        f"{cleaned_content[:preview_length]}"
    )

    raise ValueError(
        f"Unable to extract valid JSON from response. "
        f"Content preview: {cleaned_content[:200]}..."
    )


def _try_parse_json(json_str: str) -> Optional[Dict[str, Any]]:
    """
    Try to parse a JSON string, handling common issues.

    Args:
        json_str: Potential JSON string

    Returns:
        Parsed dictionary or None if parsing fails
    """
    # Try direct parsing first
    try:
        result = json.loads(json_str)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Try fixing common issues
    fixed = json_str

    # Fix trailing commas (common LLM mistake)
    fixed = re.sub(r",\s*}", "}", fixed)
    fixed = re.sub(r",\s*]", "]", fixed)

    try:
        result = json.loads(fixed)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    return None


def extract_structured_data(
    content: str,
    schema: Type[T],
    strict: bool = False
) -> Optional[T]:
    """
    Extract and validate structured data from LLM response.

    This function combines JSON extraction with Pydantic validation
    to ensure the extracted data matches the expected schema.

    Args:
        content: Raw LLM response content
        schema: Pydantic model class to validate against
        strict: If True, raise exception on validation failure;
                if False, return None

    Returns:
        Validated Pydantic model instance, or None if extraction/validation fails

    Raises:
        ValueError: If strict=True and extraction or validation fails
    """
    try:
        json_data = extract_json_from_response(content)
        return schema.model_validate(json_data)
    except ValueError as e:
        logger.warning(f"Failed to extract JSON: {e}")
        if strict:
            raise
        return None
    except ValidationError as e:
        logger.warning(f"Failed to validate against schema {schema.__name__}: {e}")
        if strict:
            raise ValueError(f"Schema validation failed: {e}")
        return None


def safe_json_extract(
    content: str,
    default: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Safely extract JSON from response, returning default on failure.

    This is a convenience wrapper around extract_json_from_response
    that never raises exceptions.

    Args:
        content: Raw LLM response content
        default: Default value to return if extraction fails

    Returns:
        Extracted JSON dictionary or the default value
    """
    try:
        return extract_json_from_response(content)
    except (ValueError, json.JSONDecodeError) as e:
        logger.debug(f"JSON extraction failed, using default: {e}")
        return default if default is not None else {}
