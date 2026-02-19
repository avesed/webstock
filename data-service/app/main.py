"""data-service FastAPI application.

This microservice provides a unified, stateless API for all external market data
sources used by WebStock:
- Stock quotes, history, info, financials (yfinance, akshare, tiingo, tushare)
- News articles (finnhub, akshare)
- Content extraction (trafilatura, playwright, tavily, polygon)
- Market indices, forex rates, HSI constituents
- Stock lists and profiles for knowledge base
- Analyst ratings, institutional data, northbound flows

Architecture:
- Multiple uvicorn workers (stateless, no shared mutable state)
- ThreadPoolExecutor for sync data provider libraries
- Redis DB 5 for short-TTL caching
- X-Internal-Token HMAC auth for service-to-service calls
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.config import get_settings
from app.core.cache import close_redis
from app.core.executor import shutdown_executor

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: configure logging, cleanup on shutdown."""
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Starting data-service...")
    yield
    logger.info("Shutting down data-service...")
    shutdown_executor()
    await close_redis()
    logger.info("data-service shut down")


app = FastAPI(
    title="WebStock Data Service",
    version="1.0.0",
    description="Stateless data provider microservice for WebStock",
    lifespan=lifespan,
)

# Register routers
from app.api.health import router as health_router  # noqa: E402
from app.api.stock import router as stock_router  # noqa: E402
from app.api.market import router as market_router  # noqa: E402
from app.api.analysis import router as analysis_router  # noqa: E402
from app.api.reference import router as reference_router  # noqa: E402
from app.api.news import router as news_router  # noqa: E402
from app.api.content import router as content_router  # noqa: E402

app.include_router(health_router)
app.include_router(stock_router)
app.include_router(market_router)
app.include_router(analysis_router)
app.include_router(reference_router)
app.include_router(news_router)
app.include_router(content_router)
