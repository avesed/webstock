"""Ticker normalization and entity resolution utilities.

Validates, normalizes, and corrects stock ticker symbols extracted by LLM
entity extraction against the local stock list index.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Shanghai Exchange prefixes (SSE)
_SHANGHAI_PREFIXES = ("600", "601", "603", "605", "688")
# Shenzhen Exchange prefixes (SZSE)
_SHENZHEN_PREFIXES = ("000", "001", "002", "003", "300", "301")

_METAL_ALIASES: Dict[str, str] = {
    "XAU": "GC=F", "XAUUSD": "GC=F", "GOLD": "GC=F",
    "XAG": "SI=F", "XAGUSD": "SI=F", "SILVER": "SI=F",
    "XPT": "PL=F", "PLATINUM": "PL=F",
    "XPD": "PA=F", "PALLADIUM": "PA=F",
}

_METAL_PATTERN = re.compile(r"^(GC|SI|PL|PA)=F$")
_US_PATTERN = re.compile(r"^[A-Z]{1,5}$")
_CN_PATTERN = re.compile(r"^\d{6}\.\w{2,3}$")
_HK_PATTERN = re.compile(r"^\d{4,5}\.HK$")
_CN_BROKER_PATTERN = re.compile(r"^(SH|SZ)(\d{6})$")
_HK_PREFIX_PATTERN = re.compile(r"^HK(\d{4,5})$")


def _normalize_ticker(raw: str) -> str:
    """Normalize common non-standard ticker formats to canonical form."""
    ticker = raw.strip().upper()
    if not ticker:
        return ticker

    if ticker in _METAL_ALIASES:
        return _METAL_ALIASES[ticker]

    m = _CN_BROKER_PATTERN.match(ticker)
    if m:
        prefix, code = m.group(1), m.group(2)
        return f"{code}.SS" if prefix == "SH" else f"{code}.SZ"

    m = _HK_PREFIX_PATTERN.match(ticker)
    if m:
        code = str(int(m.group(1))).zfill(4)
        return f"{code}.HK"

    if re.match(r"^\d{6}$", ticker):
        if ticker.startswith(_SHANGHAI_PREFIXES):
            return f"{ticker}.SS"
        if ticker.startswith(_SHENZHEN_PREFIXES):
            return f"{ticker}.SZ"

    if re.match(r"^\d{4,5}$", ticker):
        code = str(int(ticker)).zfill(4)
        return f"{code}.HK"

    m = _HK_PATTERN.match(ticker)
    if m:
        code = str(int(ticker[:-3])).zfill(4)
        return f"{code}.HK"

    return ticker


def _verify_ticker_exists(ticker: str, stock_list_svc: Any) -> bool:
    """Check if a ticker actually exists in the StockListService index."""
    from app.services.stock_types import PRECIOUS_METALS

    if ticker in PRECIOUS_METALS:
        return True

    try:
        results = stock_list_svc.search(ticker, limit=1)
        if results and results[0].get("symbol", "").upper() == ticker.upper():
            return True

        if ticker.endswith(".HK"):
            code = ticker[:-3]
            alt_ticker = f"{code.zfill(5)}.HK"
            if alt_ticker != ticker:
                results = stock_list_svc.search(alt_ticker, limit=1)
                if results and results[0].get("symbol", "").upper() == alt_ticker.upper():
                    return True
    except Exception:
        pass
    return False


def _pick_best_match(results: List[Dict[str, Any]], query: str) -> str:
    """Pick the best symbol from search results, preferring exact name match."""
    if not results:
        return ""
    if len(results) == 1:
        return results[0]["symbol"]

    top_score = results[0].get("score", 0)
    query_lower = query.lower()
    for r in results:
        if r.get("score", 0) < top_score:
            break
        name_zh = r.get("name_zh", "") or ""
        name = r.get("name", "") or ""
        if name_zh == query or name.lower() == query_lower:
            return r["symbol"]
    for r in results:
        if r.get("score", 0) < top_score:
            break
        name_zh = r.get("name_zh", "") or ""
        if query in name_zh:
            return r["symbol"]

    return results[0]["symbol"]


def resolve_entity_tickers(
    entities: List[Dict[str, Any]],
    stock_list_svc: Any = None,
) -> List[Dict[str, Any]]:
    """Validate, normalize, and correct ticker symbols using the local stock list.

    Three-step process for each stock entity:
    1. Normalize common non-standard formats (SH600519, bare digits, etc.)
    2. Verify that syntactically valid tickers actually exist in StockListService
    3. Resolve unverified tickers via company_name or entity text search
    """
    if not stock_list_svc or not stock_list_svc.is_loaded:
        return entities

    resolved = []
    for e in entities:
        entity = dict(e)

        if entity.get("type") != "stock":
            resolved.append(entity)
            continue

        raw_ticker = entity.get("entity", "")
        ticker = _normalize_ticker(raw_ticker)

        is_valid_pattern = bool(
            _METAL_PATTERN.match(ticker)
            or _US_PATTERN.match(ticker)
            or _CN_PATTERN.match(ticker)
            or _HK_PATTERN.match(ticker)
        )

        needs_resolution = False
        if is_valid_pattern:
            if _verify_ticker_exists(ticker, stock_list_svc):
                if ticker != raw_ticker:
                    logger.info("Normalized entity ticker: '%s' -> '%s'", raw_ticker, ticker)
                entity["entity"] = ticker
            else:
                needs_resolution = True
        else:
            needs_resolution = True

        if needs_resolution:
            resolved_symbol = None

            company_name = entity.get("company_name", "")
            if company_name:
                try:
                    results = stock_list_svc.search(company_name, limit=10)
                    if results:
                        resolved_symbol = _pick_best_match(results, company_name)
                except Exception as err:
                    logger.debug("Search by company_name '%s' failed: %s", company_name, err)

            if not resolved_symbol and raw_ticker:
                try:
                    results = stock_list_svc.search(raw_ticker, limit=10)
                    if results and results[0].get("score", 0) >= 150:
                        resolved_symbol = _pick_best_match(results, raw_ticker)
                except Exception as err:
                    logger.debug("Search by entity text '%s' failed: %s", raw_ticker, err)

            if resolved_symbol:
                resolved_symbol = _normalize_ticker(resolved_symbol)
                logger.info(
                    "Resolved entity ticker: '%s' -> '%s' (via '%s')",
                    raw_ticker, resolved_symbol, company_name or raw_ticker,
                )
                entity["entity"] = resolved_symbol
            elif is_valid_pattern:
                entity["entity"] = ticker
                if ticker != raw_ticker:
                    logger.info("Normalized entity ticker (unverified): '%s' -> '%s'", raw_ticker, ticker)

        resolved.append(entity)

    return resolved
