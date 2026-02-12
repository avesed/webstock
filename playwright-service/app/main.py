"""Playwright extraction service - HTTP API."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import settings
from .extractor import get_extractor

logging.basicConfig(
    level=settings.LOG_LEVEL.upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class ExtractRequest(BaseModel):
    url: str = Field(..., description="URL to extract content from")


class ExtractResponse(BaseModel):
    success: bool
    full_text: Optional[str] = None
    word_count: Optional[int] = None
    language: Optional[str] = None
    authors: Optional[list[str]] = None
    metadata: Optional[dict] = None
    error: Optional[str] = None


class SnapshotRequest(BaseModel):
    url: str = Field(..., description="URL to get accessibility snapshot from")


class SnapshotResponse(BaseModel):
    success: bool
    snapshot_text: Optional[str] = None
    url: Optional[str] = None
    error: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Playwright extraction service")
    extractor = await get_extractor()
    logger.info("Playwright browser ready")
    yield
    logger.info("Shutting down Playwright extraction service")
    await extractor.close()


app = FastAPI(
    title="Playwright Extraction Service",
    description="Headless browser content extraction for JS-heavy websites",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "playwright-extraction"}


@app.post("/extract", response_model=ExtractResponse)
async def extract_content(request: ExtractRequest):
    """Extract content from URL with overall timeout protection (P2-3.4)."""
    try:
        extractor = await get_extractor()
        # Wrap with overall timeout to prevent hung requests
        timeout_s = settings.BROWSER_TIMEOUT / 1000.0
        result = await asyncio.wait_for(
            extractor.extract(request.url),
            timeout=timeout_s,
        )
        return ExtractResponse(**result)
    except asyncio.TimeoutError:
        logger.error("Extract endpoint timed out for URL: %s", request.url[:100])
        raise HTTPException(
            status_code=504,
            detail="Content extraction timed out",
        )
    except Exception as e:
        # Sanitize error messages — don't leak internal details (P1-2.3)
        logger.error("Extract endpoint error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal extraction error")


@app.post("/snapshot", response_model=SnapshotResponse)
async def get_snapshot(request: SnapshotRequest):
    """Get accessibility snapshot with overall timeout protection."""
    try:
        extractor = await get_extractor()
        timeout_s = settings.BROWSER_TIMEOUT / 1000.0
        result = await asyncio.wait_for(
            extractor.snapshot(request.url),
            timeout=timeout_s,
        )
        return SnapshotResponse(**result)
    except asyncio.TimeoutError:
        logger.error("Snapshot endpoint timed out for URL: %s", request.url[:100])
        raise HTTPException(
            status_code=504,
            detail="Snapshot extraction timed out",
        )
    except Exception as e:
        logger.error("Snapshot endpoint error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal extraction error")
