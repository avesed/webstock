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
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.config import get_settings
from app.core.cache import close_redis
from app.core.executor import shutdown_executor
from app.core.request_id import RequestIdFilter, RequestIdMiddleware

settings = get_settings()

# Configure logging with request ID injection
LOG_FORMAT = "%(asctime)s [data] %(levelname).1s [%(request_id)s] %(message)s"
LOG_DATEFMT = "%H:%M:%S"

_root = logging.getLogger()
_root.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
for _h in _root.handlers[:]:
    _root.removeHandler(_h)
_handler = logging.StreamHandler(sys.stdout)
_handler.addFilter(RequestIdFilter())
_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
_root.addHandler(_handler)
for _name in ("httpx", "httpcore", "urllib3", "asyncio", "watchfiles"):
    logging.getLogger(_name).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: load keys, cleanup on shutdown."""
    logger.info("Starting data-service...")

    # Load API keys from DB and start Redis subscriber for live updates
    from app.core.api_keys import load_api_keys_from_db, start_subscriber, stop_subscriber
    await load_api_keys_from_db()
    start_subscriber()

    yield

    logger.info("Shutting down data-service...")
    await stop_subscriber()
    shutdown_executor()
    await close_redis()
    logger.info("data-service shut down")


app = FastAPI(
    title="WebStock Data Service",
    version="1.0.0",
    description="Stateless data provider microservice for WebStock",
    lifespan=lifespan,
)

# Request ID middleware (reads X-Request-ID from upstream or generates a new one)
app.add_middleware(RequestIdMiddleware)

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
