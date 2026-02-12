"""qlib-service FastAPI application.

This microservice wraps Microsoft Qlib for quantitative data processing:
- Expression engine (dynamic quantitative calculator for LLM agents)
- Alpha158/360 factor computation
- Factor analysis (IC, cross-sectional ranking, industry neutralization)
- Backtesting (TopK/Dropout, signal-based, long-short strategies)
- EOD data sync (yfinance for US/HK, akshare for A-shares)

Architecture:
- Single uvicorn worker (Qlib global state not safe for multi-process)
- ThreadPoolExecutor(1) for quick queries (<15s)
- ProcessPoolExecutor(1) for long tasks (backtests up to 30min)
- Redis DB 3 for factor caching, backtest progress, sync status
"""
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.config import get_settings
from app.executor import shutdown_executors

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: init Qlib + Redis, cleanup on shutdown."""
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("Starting qlib-service...")

    # Check if Qlib data directory exists
    data_dir = settings.QLIB_DATA_DIR
    if not os.path.exists(data_dir):
        logger.warning(
            "Qlib data directory does not exist: %s (will be created on first sync)",
            data_dir,
        )
        os.makedirs(data_dir, exist_ok=True)

    # Try to initialize Qlib with default market (non-fatal if data not yet downloaded)
    try:
        from app.context import QlibContext

        market_data_dir = os.path.join(
            data_dir,
            QlibContext.REGION_TO_DATA_DIR.get(settings.DEFAULT_MARKET, "us_data"),
        )
        if os.path.exists(market_data_dir) and os.listdir(market_data_dir):
            QlibContext.ensure_init(settings.DEFAULT_MARKET, data_dir)
            logger.info(
                "Qlib initialized with default market: %s", settings.DEFAULT_MARKET
            )
        else:
            logger.warning(
                "No data for default market '%s' yet. Run data sync first.",
                settings.DEFAULT_MARKET,
            )
    except Exception as e:
        logger.warning("Qlib initialization deferred: %s", e)

    yield

    # Shutdown
    logger.info("Shutting down qlib-service...")
    shutdown_executors()
    logger.info("qlib-service shut down")


app = FastAPI(
    title="WebStock Qlib Service",
    version="1.0.0",
    description="Quantitative data processing microservice powered by Microsoft Qlib",
    lifespan=lifespan,
)

# Register routers
from app.api.health import router as health_router
from app.api.factors import router as factors_router
from app.api.data import router as data_router
from app.api.expression import router as expression_router
from app.api.backtests import router as backtests_router

app.include_router(health_router)
app.include_router(factors_router)
app.include_router(data_router)
app.include_router(expression_router)
app.include_router(backtests_router)
