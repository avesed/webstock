"""Data synchronization API endpoints.

Endpoints:
    POST /data/sync/{market} - Trigger market data sync (runs in ProcessPoolExecutor)
    GET  /data/status         - Get sync status for all markets
"""
import logging

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.executor import run_qlib_background
from app.models.schemas import SyncRequest, SyncStatusResponse
from app.services.data_sync import DataSyncService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data", tags=["data"])


@router.post("/sync/{market}")
async def sync_market(
    market: str,
    req: SyncRequest,
):
    """Trigger data synchronization for a market.

    Downloads EOD data and converts to Qlib .bin format.
    Runs in ProcessPoolExecutor (can take minutes for full markets).

    Supported markets: us, hk, cn, metal.
    """
    # Validate market code
    valid_markets = {"us", "hk", "cn", "sh", "sz", "metal"}
    if market not in valid_markets:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid market: {market}. Valid: {sorted(valid_markets)}",
        )

    logger.info(
        "POST /data/sync/%s symbols=%s update_only=%s",
        market,
        f"{len(req.symbols)} symbols" if req.symbols else "full market",
        req.update_only,
    )

    try:
        result = await run_qlib_background(
            DataSyncService.sync_market,
            market,
            symbols=req.symbols,
            update_only=req.update_only,
        )
        return result
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Data sync for {market} timed out (limit: 30 minutes)",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Data sync failed for %s: %s", market, e, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Data sync failed: {e}"
        )


@router.get("/status", response_model=SyncStatusResponse)
async def get_data_status():
    """Get data synchronization status for all markets.

    Returns per-market information: last_sync timestamp, symbol count,
    date range, and whether data exists on disk.
    """
    logger.info("GET /data/status")

    try:
        settings = get_settings()
        markets = DataSyncService.get_sync_status(settings.QLIB_DATA_DIR)
        return SyncStatusResponse(markets=markets)
    except Exception as e:
        logger.error("Failed to get sync status: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get sync status: {e}"
        )
