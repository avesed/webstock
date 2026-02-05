"""Shared symbol validation module for all market types.

This module provides unified symbol validation and normalization logic
for US stocks, Hong Kong stocks, A-shares, and precious metals futures.
"""

import logging
import re
from typing import Tuple

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

# Symbol validation patterns
# IMPORTANT: Order matters - metal pattern must be checked BEFORE US pattern
# because SI (Silver) is also a valid US stock ticker
SYMBOL_PATTERNS = {
    "METAL": re.compile(r"^(GC|SI|PL|PA)=F$"),  # Precious metals futures (GC=F, SI=F, PL=F, PA=F)
    "US": re.compile(r"^[A-Z]{1,5}$"),           # US: 1-5 uppercase letters
    "HK": re.compile(r"^[0-9]{4,5}\.HK$"),       # HK: 4-5 digits followed by .HK
    "A_SHARE": re.compile(r"^[0-9]{6}\.(SS|SZ)$"),  # A-Share: 6 digits followed by .SS or .SZ
    "A_SHARE_BARE": re.compile(r"^[0-9]{6}$"),    # A-Share without suffix
    "HK_BARE": re.compile(r"^[0-9]{4,5}$"),       # HK without .HK suffix
}

# Shanghai: 600xxx, 601xxx, 603xxx, 605xxx, 688xxx (STAR Market)
_SHANGHAI_PREFIXES = ("600", "601", "603", "605", "688")
# Shenzhen: 000xxx, 001xxx, 002xxx, 003xxx, 300xxx (ChiNext), 301xxx
_SHENZHEN_PREFIXES = ("000", "001", "002", "003", "300", "301")

# Valid precious metal symbols
VALID_METAL_SYMBOLS = {"GC=F", "SI=F", "PL=F", "PA=F"}


def validate_symbol(symbol: str) -> str:
    """
    Validate and normalize stock/commodity symbol with regex pattern matching.

    Supports:
    - US stocks: AAPL, MSFT, GOOGL (1-5 uppercase letters)
    - HK stocks: 0700.HK, 9988.HK (4-5 digits with .HK suffix)
    - Shanghai A-shares: 600519.SS, 600036.SS (6 digits with .SS suffix)
    - Shenzhen A-shares: 000001.SZ, 000858.SZ (6 digits with .SZ suffix)
    - Precious metals: GC=F (Gold), SI=F (Silver), PL=F (Platinum), PA=F (Palladium)

    Args:
        symbol: Raw symbol input from user

    Returns:
        Normalized symbol string

    Raises:
        HTTPException: If symbol format is invalid
    """
    symbol = symbol.strip().upper()

    if not symbol or len(symbol) > 20:
        logger.debug(f"Symbol validation failed: empty or too long - '{symbol}'")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid symbol format: symbol is empty or too long",
        )

    # Check for precious metals FIRST (before US pattern)
    # This is critical because SI (Silver) is also a valid US stock ticker
    if SYMBOL_PATTERNS["METAL"].match(symbol):
        logger.debug(f"Validated precious metal symbol: {symbol}")
        return symbol

    # Auto-append exchange suffix for bare 6-digit A-share codes
    if SYMBOL_PATTERNS["A_SHARE_BARE"].match(symbol):
        if symbol.startswith(_SHANGHAI_PREFIXES):
            symbol = f"{symbol}.SS"
            logger.debug(f"Normalized A-share symbol to Shanghai: {symbol}")
        elif symbol.startswith(_SHENZHEN_PREFIXES):
            symbol = f"{symbol}.SZ"
            logger.debug(f"Normalized A-share symbol to Shenzhen: {symbol}")

    # Auto-append .HK for bare 4-5 digit codes that look like HK stocks
    if SYMBOL_PATTERNS["HK_BARE"].match(symbol):
        symbol = f"{symbol}.HK"
        logger.debug(f"Normalized HK symbol: {symbol}")

    # Check against valid patterns (order: metal already checked, then others)
    is_valid = (
        SYMBOL_PATTERNS["US"].match(symbol) or
        SYMBOL_PATTERNS["HK"].match(symbol) or
        SYMBOL_PATTERNS["A_SHARE"].match(symbol)
    )

    if not is_valid:
        logger.debug(f"Symbol validation failed: invalid format - '{symbol}'")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid symbol format. Valid formats: "
                "US (e.g., AAPL), HK (e.g., 0700.HK), "
                "Shanghai (e.g., 600519.SS), Shenzhen (e.g., 000001.SZ), "
                "Precious metals (GC=F, SI=F, PL=F, PA=F)"
            ),
        )

    # Normalize HK symbols: 01810.HK -> 1810.HK (yfinance uses 4-digit codes)
    if symbol.endswith(".HK"):
        code = symbol[:-3]  # strip ".HK"
        code = str(int(code)).zfill(4)  # 01810 -> 1810, 00700 -> 0700
        symbol = f"{code}.HK"
        logger.debug(f"Normalized HK symbol format: {symbol}")

    logger.debug(f"Symbol validation successful: {symbol}")
    return symbol


def is_precious_metal(symbol: str) -> bool:
    """
    Check if a symbol represents a precious metal future.

    Args:
        symbol: Symbol to check (should be uppercase)

    Returns:
        True if symbol is a precious metal future
    """
    return symbol.upper() in VALID_METAL_SYMBOLS
