"""Prompt loading utilities with caching support.

This module provides utilities for loading prompt templates from files
with caching for performance and hot-reload support for development.
"""

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Base directory for prompt instruction files
PROMPTS_BASE_DIR = Path(__file__).parent

# Cache for loaded instruction files
_instruction_cache: Dict[str, str] = {}
_cache_enabled: bool = True


def get_prompts_dir() -> Path:
    """
    Get the base directory for prompt files.

    Returns:
        Path to the prompts directory
    """
    return PROMPTS_BASE_DIR


def load_instructions(
    filename: str,
    subdirectory: Optional[str] = None,
    use_cache: bool = True
) -> str:
    """
    Load instruction content from a file.

    This function loads prompt instructions from text files, with optional
    caching for performance. Useful for loading long system prompts or
    instruction sets that are stored in separate files.

    Args:
        filename: Name of the instruction file (e.g., "fundamental_instructions.txt")
        subdirectory: Optional subdirectory within prompts/ (e.g., "analysis", "chat")
        use_cache: Whether to use cached content (default True)

    Returns:
        Content of the instruction file

    Raises:
        FileNotFoundError: If the instruction file does not exist
        IOError: If the file cannot be read
    """
    # Build the cache key
    cache_key = f"{subdirectory}/{filename}" if subdirectory else filename

    # Check cache first
    if use_cache and _cache_enabled and cache_key in _instruction_cache:
        logger.debug(f"Returning cached instructions: {cache_key}")
        return _instruction_cache[cache_key]

    # Build the file path
    if subdirectory:
        file_path = PROMPTS_BASE_DIR / subdirectory / filename
    else:
        file_path = PROMPTS_BASE_DIR / filename

    # Load the file
    if not file_path.exists():
        raise FileNotFoundError(
            f"Instruction file not found: {file_path}"
        )

    try:
        content = file_path.read_text(encoding="utf-8")
        logger.debug(f"Loaded instructions from: {file_path}")

        # Cache the content
        if _cache_enabled:
            _instruction_cache[cache_key] = content

        return content

    except IOError as e:
        logger.error(f"Failed to read instruction file {file_path}: {e}")
        raise


@lru_cache(maxsize=32)
def load_instructions_cached(filename: str, subdirectory: Optional[str] = None) -> str:
    """
    Load instructions with LRU caching (for immutable instruction files).

    This uses Python's lru_cache for efficient caching of frequently
    accessed instruction files. Use this for production where instruction
    files don't change at runtime.

    Args:
        filename: Name of the instruction file
        subdirectory: Optional subdirectory within prompts/

    Returns:
        Content of the instruction file
    """
    return load_instructions(filename, subdirectory, use_cache=False)


def clear_cache() -> None:
    """
    Clear the instruction cache.

    Use this to force reload of instruction files, for example
    during development when prompts are being updated, or in
    production for hot-reload scenarios.
    """
    global _instruction_cache
    _instruction_cache.clear()
    load_instructions_cached.cache_clear()
    logger.info("Instruction cache cleared")


def disable_cache() -> None:
    """
    Disable instruction caching.

    Useful for development when prompt files are being actively modified.
    """
    global _cache_enabled
    _cache_enabled = False
    clear_cache()
    logger.info("Instruction caching disabled")


def enable_cache() -> None:
    """
    Enable instruction caching.

    Re-enables caching after it has been disabled.
    """
    global _cache_enabled
    _cache_enabled = True
    logger.info("Instruction caching enabled")


def is_cache_enabled() -> bool:
    """
    Check if instruction caching is enabled.

    Returns:
        True if caching is enabled, False otherwise
    """
    return _cache_enabled


def get_cache_stats() -> Dict[str, int]:
    """
    Get cache statistics for monitoring.

    Returns:
        Dictionary with cache statistics
    """
    lru_info = load_instructions_cached.cache_info()
    return {
        "manual_cache_size": len(_instruction_cache),
        "lru_cache_hits": lru_info.hits,
        "lru_cache_misses": lru_info.misses,
        "lru_cache_size": lru_info.currsize,
        "lru_cache_maxsize": lru_info.maxsize,
        "cache_enabled": _cache_enabled,
    }


def load_template(
    template_name: str,
    subdirectory: Optional[str] = None,
    variables: Optional[Dict[str, str]] = None
) -> str:
    """
    Load a prompt template and optionally substitute variables.

    This is a convenience function that combines loading with
    simple variable substitution using Python's str.format().

    Args:
        template_name: Name of the template file
        subdirectory: Optional subdirectory within prompts/
        variables: Optional dictionary of variables to substitute

    Returns:
        Template content with variables substituted

    Example:
        >>> template = load_template(
        ...     "analysis_template.txt",
        ...     subdirectory="analysis",
        ...     variables={"symbol": "AAPL", "market": "US"}
        ... )
    """
    content = load_instructions(template_name, subdirectory)

    if variables:
        try:
            content = content.format(**variables)
        except KeyError as e:
            logger.warning(
                f"Missing variable in template {template_name}: {e}"
            )
            # Fall back to partial substitution
            for key, value in variables.items():
                content = content.replace(f"{{{key}}}", str(value))

    return content


def list_instruction_files(subdirectory: Optional[str] = None) -> list:
    """
    List available instruction files in a directory.

    Args:
        subdirectory: Optional subdirectory to list (default: root prompts dir)

    Returns:
        List of instruction file names
    """
    if subdirectory:
        directory = PROMPTS_BASE_DIR / subdirectory
    else:
        directory = PROMPTS_BASE_DIR

    if not directory.exists():
        return []

    # List text and markdown files
    files = []
    for ext in [".txt", ".md", ".prompt"]:
        files.extend(f.name for f in directory.glob(f"*{ext}"))

    return sorted(files)
