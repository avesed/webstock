"""FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.config import settings
from app.db.database import AsyncSessionLocal, close_db, init_db
from app.db.redis import close_redis, init_redis
from app.services.cache_service import cleanup_cache_service
from app.services.data_aggregator import cleanup_data_aggregator
from app.services.alphaforge_client import close_alphaforge_client
from app.services.data_service_client import close_data_service_client
from app.services.stock_service import cleanup_stock_service
from app.services.stockpulse_client import close_stockpulse_client

os.environ.setdefault("LOG_TAG", "web")
from worker.log_config import setup_logging  # noqa: E402

setup_logging()

logger = logging.getLogger(__name__)

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


async def _maybe_seed_stock_knowledge_base() -> None:
    """Dispatch knowledge base build if no stock profiles exist yet.

    Uses a Redis SETNX lock to prevent duplicate dispatches when multiple
    uvicorn workers start concurrently.
    """
    from sqlalchemy import text

    from app.db.redis import get_redis

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(text(
                "SELECT COUNT(*) FROM document_embeddings "
                "WHERE source_type = 'stock_profile'"
            ))
            count = result.scalar() or 0

        if count == 0:
            # Use Redis lock to ensure only one worker dispatches the task
            redis = await get_redis()
            lock_key = "stock_kb:seed_lock"
            acquired = await redis.set(lock_key, "1", nx=True, ex=300)
            if not acquired:
                logger.debug(
                    "Stock knowledge base seed already dispatched by another worker"
                )
                return

            logger.info(
                "Stock knowledge base is empty, dispatching initial build "
                "(countdown=60s to wait for Celery worker)"
            )
            from worker.tasks.stock_profile_tasks import build_stock_knowledge_base
            build_stock_knowledge_base.apply_async(countdown=60)
        else:
            logger.debug(
                "Stock knowledge base already has %d profiles, skipping seed",
                count,
            )
    except Exception as e:
        logger.warning("Stock knowledge base seed check failed: %s", e)


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

    # Register LLM usage recorder for cost tracking
    from app.core.llm import set_llm_usage_recorder
    from app.services.llm_cost_service import get_llm_cost_service

    async def _record_llm_usage(
        purpose: str, model: str, prompt_tokens: int = 0,
        completion_tokens: int = 0, cached_tokens: int = 0,
        user_id=None, metadata=None,
    ):
        await get_llm_cost_service().record_usage(
            purpose=purpose, model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            user_id=user_id, metadata=metadata,
        )

    set_llm_usage_recorder(_record_llm_usage)
    logger.info("LLM usage recorder registered for cost tracking")

    # Check if stock knowledge base needs initial build
    await _maybe_seed_stock_knowledge_base()

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

    # Close DataServiceClient
    logger.debug("Closing DataServiceClient...")
    await close_data_service_client()
    logger.debug("DataServiceClient closed")

    # Close StockPulseClient
    logger.debug("Closing StockPulseClient...")
    await close_stockpulse_client()
    logger.debug("StockPulseClient closed")

    # Close AlphaForge client
    logger.debug("Closing AlphaForgeClient...")
    await close_alphaforge_client()
    logger.debug("AlphaForgeClient closed")

    # Cancel background tasks (needs Redis for metadata updates)
    from app.services.task_manager import get_task_manager

    logger.debug("Cleaning up TaskManager...")
    await get_task_manager().cleanup()
    logger.debug("TaskManager cleanup complete")

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

    from app.core.middleware import RequestIdMiddleware

    app.add_middleware(RequestIdMiddleware)

    # Include API router
    app.include_router(api_router)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        from app.core.request_id import get_request_id

        rid = get_request_id()
        logger.exception(
            "Unhandled exception [%s] %s %s", rid, request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "requestId": rid},
        )

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
