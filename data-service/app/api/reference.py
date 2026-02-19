"""Reference data API — stock lists and profiles.

POST /v1/reference/stock-list
    Build the full stock list (~37K symbols) from all markets.
    Long-running (~2 min). Returns the complete list.

POST /v1/reference/stock-profiles/{market}
    Collect stock profiles for a given market (cn, us, hk).
    For CN: symbols body is ignored (collects all from concept boards).
    For US/HK: ``symbols`` specifies which stocks to collect.
    Long-running (can take minutes). Returns profile dicts.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import verify_internal_token
from app.models.base import ApiResponse
from app.models.reference import StockListResult, StockProfileResult

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/v1/reference",
    tags=["reference"],
    dependencies=[Depends(verify_internal_token)],
)


class ProfileRequest(BaseModel):
    """Request body for stock profile collection."""

    symbols: List[str] = []


@router.post("/stock-list", response_model=ApiResponse[StockListResult])
async def build_stock_list_endpoint():
    """Build the full stock list from all markets.

    Fetches ~37K symbols from Finnhub (US) and AKShare (CN/HK) in parallel,
    generates pinyin for Chinese names, deduplicates by symbol.

    Timeout hint: 120s.
    """
    from app.services.stock_list_service import build_stock_list

    t0 = time.monotonic()
    try:
        items = await build_stock_list()
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # Build by-market breakdown for logging
        by_market: dict[str, int] = {}
        for item in items:
            m = item.get("market", "unknown")
            by_market[m] = by_market.get(m, 0) + 1
        logger.info(
            "Stock list built: %d items in %dms — %s",
            len(items), elapsed_ms, by_market,
        )

        return ApiResponse(
            success=True,
            data=StockListResult(items=items, count=len(items)),
            source="finnhub+akshare",
            elapsed_ms=elapsed_ms,
        )
    except Exception as e:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.exception("Stock list build failed: %s", e)
        return ApiResponse(
            success=False,
            error=str(e),
            elapsed_ms=elapsed_ms,
        )


@router.post(
    "/stock-profiles/{market}",
    response_model=ApiResponse[StockProfileResult],
)
async def collect_profiles_endpoint(
    market: str,
    body: Optional[ProfileRequest] = None,
):
    """Collect stock profiles for a given market.

    - ``cn``: Collects all A-share profiles from concept boards + individual info.
      The ``symbols`` field in the body is ignored.
    - ``us``: Collects profiles for the given list of US stock symbols via yfinance.
    - ``hk``: Collects profiles for the given list of HK stock symbols via yfinance.

    Timeout hint: 300s.
    """
    from app.services.stock_profile_service import (
        collect_cn_profiles,
        collect_hk_profiles,
        collect_us_profiles,
    )

    market = market.lower().strip()
    symbols = body.symbols if body else []

    t0 = time.monotonic()
    try:
        if market == "cn":
            profiles = await collect_cn_profiles()
            source = "akshare"
        elif market == "us":
            if not symbols:
                return ApiResponse(
                    success=False,
                    error="symbols list required for US market",
                )
            profiles = await collect_us_profiles(symbols)
            source = "yfinance"
        elif market == "hk":
            if not symbols:
                return ApiResponse(
                    success=False,
                    error="symbols list required for HK market",
                )
            profiles = await collect_hk_profiles(symbols)
            source = "yfinance"
        else:
            return ApiResponse(
                success=False,
                error=f"Unsupported market: {market}. Must be cn, us, or hk.",
            )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "Profile collection for %s: %d profiles in %dms",
            market, len(profiles), elapsed_ms,
        )

        return ApiResponse(
            success=True,
            data=StockProfileResult(
                profiles=profiles,
                count=len(profiles),
                market=market,
            ),
            source=source,
            elapsed_ms=elapsed_ms,
        )
    except Exception as e:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.exception("Profile collection failed for %s: %s", market, e)
        return ApiResponse(
            success=False,
            error=str(e),
            elapsed_ms=elapsed_ms,
        )
