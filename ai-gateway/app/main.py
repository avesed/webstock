"""AI Gateway — OpenAI-compatible LLM proxy with multi-provider routing."""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.auth import InternalAuthMiddleware
from app.config import settings
from app.provider_cache import provider_cache
from app.routes.chat import router as chat_router
from app.routes.embeddings import router as embeddings_router

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [gateway] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle events."""
    # Startup
    logger.info("AI Gateway starting on port %d", settings.PORT)
    await provider_cache.init()
    logger.info("AI Gateway ready")
    yield
    # Shutdown
    await provider_cache.close()
    logger.info("AI Gateway shutdown complete")


app = FastAPI(
    title="AI Gateway",
    description="OpenAI-compatible LLM proxy with multi-provider routing",
    version="1.0.0",
    docs_url=None,  # Internal service, no Swagger UI
    redoc_url=None,
    lifespan=lifespan,
)

# Auth middleware — pure ASGI (not BaseHTTPMiddleware) to preserve SSE streaming
app.add_middleware(InternalAuthMiddleware)

# Routes
app.include_router(chat_router)
app.include_router(embeddings_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler to return structured error responses."""
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"error": {"message": str(exc), "type": "internal_error"}},
    )


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "ai-gateway"}


@app.get("/health/ready")
async def health_ready():
    """Readiness check — verifies DB connectivity."""
    try:
        providers = await provider_cache.get_all_providers()
        return {
            "status": "ok",
            "service": "ai-gateway",
            "providers_loaded": len(providers),
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "detail": str(e)},
        )
