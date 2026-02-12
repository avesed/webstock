"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import settings
from app.db.database import AsyncSessionLocal, close_db, init_db
from app.db.redis import close_redis, init_redis
from app.services.cache_service import cleanup_cache_service
from app.services.data_aggregator import cleanup_data_aggregator
from app.services.qlib_client import close_qlib_client
from app.services.stock_service import cleanup_stock_service

logger = logging.getLogger(__name__)

# ── Default RSS feeds (seeded on first boot when table is empty) ─────────
DEFAULT_RSS_FEEDS = [
    {
        "name": "Reuters Business",
        "rsshub_route": "/reuters/business",
        "description": "Reuters global business & markets news",
        "category": "media",
        "symbol": None,
        "market": "US",
        "poll_interval_minutes": 30,
        "fulltext_mode": False,
    },
    {
        "name": "CNBC Top News",
        "rsshub_route": "/cnbc/rss",
        "description": "CNBC US top news and market analysis",
        "category": "media",
        "symbol": None,
        "market": "US",
        "poll_interval_minutes": 30,
        "fulltext_mode": False,
    },
    {
        "name": "华尔街见闻",
        "rsshub_route": "/wallstreetcn/news/global",
        "description": "华尔街见闻全球财经资讯",
        "category": "media",
        "symbol": None,
        "market": "US",
        "poll_interval_minutes": 15,
        "fulltext_mode": False,
    },
    {
        "name": "财联社电报",
        "rsshub_route": "/cls/telegraph",
        "description": "财联社7×24小时实时财经电报",
        "category": "media",
        "symbol": None,
        "market": "CN",
        "poll_interval_minutes": 10,
        "fulltext_mode": False,
    },
    {
        "name": "金十数据",
        "rsshub_route": "/jin10",
        "description": "金十数据全球财经快讯",
        "category": "media",
        "symbol": None,
        "market": "US",
        "poll_interval_minutes": 10,
        "fulltext_mode": False,
    },
    {
        "name": "财新网",
        "rsshub_route": "/caixin/latest",
        "description": "财新网最新财经深度报道",
        "category": "media",
        "symbol": None,
        "market": "CN",
        "poll_interval_minutes": 30,
        "fulltext_mode": True,
    },
    {
        "name": "东方财富策略报告",
        "rsshub_route": "/eastmoney/report/strategyreport",
        "description": "东方财富网券商策略研报",
        "category": "media",
        "symbol": None,
        "market": "CN",
        "poll_interval_minutes": 60,
        "fulltext_mode": True,
    },
]


async def create_first_admin() -> None:
    """Create or promote the first admin user on startup if configured.

    This function checks if FIRST_ADMIN_EMAIL is configured and if no admin
    users exist yet. If both conditions are met:
    - If a user with that email exists, they are promoted to admin
    - If no user with that email exists, a warning is logged

    This is a one-time operation that only runs when no admins exist.
    """
    from sqlalchemy import func, select

    from app.models.user import User, UserRole

    admin_email = settings.FIRST_ADMIN_EMAIL
    if not admin_email:
        logger.debug("FIRST_ADMIN_EMAIL not configured, skipping admin creation")
        return

    async with AsyncSessionLocal() as db:
        # Check if any admin already exists
        result = await db.execute(
            select(func.count()).select_from(User).where(User.role == UserRole.ADMIN)
        )
        admin_count = result.scalar_one()

        if admin_count > 0:
            logger.info(
                "Admin user(s) already exist (%d), skipping first admin creation",
                admin_count,
            )
            return

        # Check if the specified email exists
        result = await db.execute(select(User).where(User.email == admin_email))
        user = result.scalar_one_or_none()

        if user:
            # Promote existing user to admin
            user.role = UserRole.ADMIN
            await db.commit()
            logger.info("Promoted existing user %s to admin role", admin_email)
        else:
            logger.warning(
                "FIRST_ADMIN_EMAIL is set to %s but no user with this email exists. "
                "Please register this email first, then restart the server.",
                admin_email,
            )


async def seed_default_rss_feeds() -> None:
    """Seed default RSS feeds on first boot if the table is empty."""
    from sqlalchemy import func, select

    from app.models.rss_feed import RssFeed

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(func.count()).select_from(RssFeed))
        count = result.scalar() or 0
        if count > 0:
            logger.debug("RSS feeds table already has %d entries, skipping seed", count)
            return

        logger.info("Seeding %d default RSS feeds...", len(DEFAULT_RSS_FEEDS))
        for feed_data in DEFAULT_RSS_FEEDS:
            db.add(RssFeed(**feed_data))
        await db.commit()
        logger.info("Default RSS feeds seeded successfully")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for startup and shutdown events."""
    # Startup
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)

    # Initialize database
    logger.info("Initializing database connection...")
    await init_db()
    logger.info("Database connection established")

    # Initialize Redis
    logger.info("Initializing Redis connection...")
    await init_redis()
    logger.info("Redis connection established")

    # Create first admin user if configured
    logger.debug("Checking first admin configuration...")
    await create_first_admin()

    # Seed default RSS feeds if table is empty
    await seed_default_rss_feeds()

    yield

    # Shutdown
    logger.info("Shutting down...")

    # Cleanup services in reverse order of dependency
    logger.debug("Cleaning up stock service...")
    await cleanup_stock_service()
    logger.debug("Stock service cleanup complete")

    logger.debug("Cleaning up data aggregator...")
    await cleanup_data_aggregator()
    logger.debug("Data aggregator cleanup complete")

    logger.debug("Cleaning up cache service...")
    await cleanup_cache_service()
    logger.debug("Cache service cleanup complete")

    # Close Qlib client
    logger.debug("Closing Qlib client...")
    await close_qlib_client()
    logger.debug("Qlib client closed")

    # Close Redis
    await close_redis()
    logger.info("Redis connection closed")

    # Close database
    await close_db()
    logger.info("Database connection closed")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Stock AI Analysis Framework API",
        docs_url="/api/docs" if settings.DEBUG else None,
        redoc_url="/api/redoc" if settings.DEBUG else None,
        openapi_url="/api/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include API router
    app.include_router(api_router)

    # Root endpoint
    @app.get("/", tags=["Root"])
    async def root():
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "status": "running",
        }

    return app


# Create application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
