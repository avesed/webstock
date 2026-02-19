"""Content extraction models."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ContentResult(BaseModel):
    """Result of extracting full-text content from a URL."""

    url: str
    full_text: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    keywords: Optional[List[str]] = None
    top_image: Optional[str] = None
    word_count: int = 0
    language: Optional[str] = None
    publish_date: Optional[str] = None
    is_partial: bool = False
    extraction_method: str  # "trafilatura" / "playwright" / "tavily" / "polygon"
    success: bool = True
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
