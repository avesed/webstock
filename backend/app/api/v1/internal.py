"""Internal API endpoints for machine-to-machine data access.

DEPRECATED: qlib-service now reads from data-service directly.
These endpoints are kept for backward compatibility during transition.

These endpoints are designed for inter-service communication (e.g.,
qlib-service -> main backend) and are secured via a shared token
in the X-Internal-Token header rather than JWT user authentication.
"""

import hmac
import logging
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.database import get_db
from app.schemas.base import CamelModel
from app.services.daily_bar_service import DailyBarService
from app.services.hsi_constituents import get_hsi_constituents

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static fallbacks
# ---------------------------------------------------------------------------

_US_FALLBACK_SYMBOLS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "BRK-B", "JPM", "V",
]

_CN_FALLBACK_SYMBOLS = [
    "600519.SS", "601318.SS", "600036.SS", "000858.SZ", "600276.SS",
    "601166.SS", "000333.SZ", "002415.SZ", "600900.SS", "601888.SS",
]

_METAL_SYMBOLS = ["GC=F", "SI=F", "PL=F", "PA=F"]

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


async def verify_internal_token(
    x_internal_token: str = Header(...),
) -> None:
    """Verify the internal API token via constant-time comparison."""
    if not settings.INTERNAL_API_TOKEN:
        logger.warning("Internal API request rejected: INTERNAL_API_TOKEN not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal API not configured",
        )
    if not hmac.compare_digest(x_internal_token, settings.INTERNAL_API_TOKEN):
        logger.warning("Internal API request rejected: invalid token")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid internal token",
        )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SymbolsResponse(CamelModel):
    market: str
    symbols: list[str]
    count: int


class HistoryBatchRequest(CamelModel):
    symbols: list[str]
    market: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class HistoryBatchMeta(CamelModel):
    symbol_count: int
    total_bars: int


class HistoryBatchResponse(CamelModel):
    data: dict[str, Any]
    meta: HistoryBatchMeta
    errors: list[str] = []





# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/internal", tags=["Internal"])


@router.get(
    "/symbols/{market}",
    response_model=SymbolsResponse,
    summary="Get symbol list for a market",
    description="Returns tradeable symbols for the specified market. "
    "Used by qlib-service for data synchronization.",
    dependencies=[Depends(verify_internal_token)],
)
async def get_symbols(market: str) -> SymbolsResponse:
    """Return the symbol list for the requested market."""
    market = market.lower()
    logger.info("Internal API: get_symbols requested for market=%s", market)

    if market == "us":
        symbols = await _get_us_symbols()
    elif market == "hk":
        symbols = await _get_hk_symbols()
    elif market == "cn":
        symbols = await _get_cn_symbols()
    elif market == "metal":
        symbols = list(_METAL_SYMBOLS)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown market: {market}. Supported: us, hk, cn, metal",
        )

    logger.info(
        "Internal API: returning %d symbols for market=%s", len(symbols), market,
    )
    return SymbolsResponse(
        market=market,
        symbols=symbols,
        count=len(symbols),
    )


@router.post(
    "/history/batch",
    response_model=HistoryBatchResponse,
    summary="Batch query daily bar data",
    description="Returns daily OHLCV data from the database in columnar format "
    "for efficient DataFrame construction. Max 50 symbols per request.",
    dependencies=[Depends(verify_internal_token)],
)
async def get_history_batch(
    request: HistoryBatchRequest,
    db: AsyncSession = Depends(get_db),
) -> HistoryBatchResponse:
    """Return daily bar data for a batch of symbols."""
    if len(request.symbols) > 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum 50 symbols per request, got {len(request.symbols)}",
        )

    logger.info(
        "Internal API: history batch requested: market=%s, symbols=%d, dates=%s..%s",
        request.market, len(request.symbols), request.start_date, request.end_date,
    )

    try:
        service = DailyBarService()
        data = await service.get_bars_batch(
            db,
            request.symbols,
            request.start_date,
            request.end_date,
        )
    except Exception as exc:
        logger.error(
            "Failed to query history batch: market=%s, symbols=%d: %s",
            request.market, len(request.symbols), exc,
        )
        raise

    total_bars = sum(len(cols.get("dates", [])) for cols in data.values())
    errors: list[str] = []
    missing = set(request.symbols) - set(data.keys())
    if missing:
        errors.extend(f"{s}: No data in DB" for s in sorted(missing))

    logger.info(
        "Internal API: returning %d bars for %d/%d symbols",
        total_bars, len(data), len(request.symbols),
    )

    return HistoryBatchResponse(
        data=data,
        meta=HistoryBatchMeta(
            symbol_count=len(data),
            total_bars=total_bars,
        ),
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Per-market symbol helpers
# ---------------------------------------------------------------------------


async def _get_us_symbols() -> list[str]:
    """Get US symbols from the local stock list index."""
    try:
        from app.services.stock_list_service import get_stock_list_service

        svc = await get_stock_list_service()
        # Exclude OTC (OOTC) — poor data coverage, not useful for quant analysis
        _US_MAJOR_EXCHANGES = {"XNAS", "XNYS", "ARCX", "BATS", "XASE"}
        symbols = [s.symbol for s in svc.stocks
                   if s.market == "us" and s.exchange in _US_MAJOR_EXCHANGES]
        if symbols:
            logger.info("Fetched %d US symbols from local stock list", len(symbols))
            return symbols
        logger.warning("Local stock list returned 0 US symbols, using fallback")
    except Exception as exc:
        logger.warning("Failed to load US symbols from stock list: %s", exc)
    return list(_US_FALLBACK_SYMBOLS)


async def _get_hk_symbols() -> list[str]:
    """Get HK symbols via HSI constituents service."""
    try:
        symbols = await get_hsi_constituents()
        logger.info("Fetched %d HK (HSI) symbols", len(symbols))
        return symbols
    except Exception as exc:
        logger.warning("Failed to fetch HK symbols: %s", exc)
        raise


async def _get_cn_symbols() -> list[str]:
    """Get CN symbols from the local stock list index."""
    try:
        from app.services.stock_list_service import get_stock_list_service

        svc = await get_stock_list_service()
        symbols = [s.symbol for s in svc.stocks if s.market in ("sh", "sz")]
        if symbols:
            logger.info("Fetched %d CN symbols from local stock list", len(symbols))
            return symbols
        logger.warning("Local stock list returned 0 CN symbols, using fallback")
    except Exception as exc:
        logger.warning("Failed to load CN symbols from stock list: %s", exc)
    return list(_CN_FALLBACK_SYMBOLS)
