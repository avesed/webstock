"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents import cleanup_orchestrator
from app.api.v1.router import api_router
from app.config import settings
from app.db.database import close_db, init_db
from app.db.redis import close_redis, init_redis
from app.services.cache_service import cleanup_cache_service
from app.services.data_aggregator import cleanup_data_aggregator
from app.services.stock_service import cleanup_stock_service


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for startup and shutdown events."""
    # Startup
    print(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")

    # Initialize database
    print("Initializing database connection...")
    await init_db()
    print("Database connection established")

    # Initialize Redis
    print("Initializing Redis connection...")
    await init_redis()
    print("Redis connection established")

    yield

    # Shutdown
    print("Shutting down...")

    # Cleanup services in reverse order of dependency
    print("Cleaning up agent orchestrator...")
    await cleanup_orchestrator()
    print("Agent orchestrator cleanup complete")

    print("Cleaning up stock service...")
    await cleanup_stock_service()
    print("Stock service cleanup complete")

    print("Cleaning up data aggregator...")
    await cleanup_data_aggregator()
    print("Data aggregator cleanup complete")

    print("Cleaning up cache service...")
    await cleanup_cache_service()
    print("Cache service cleanup complete")

    # Close Redis
    await close_redis()
    print("Redis connection closed")

    # Close database
    await close_db()
    print("Database connection closed")


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
