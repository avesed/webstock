"""Trafilatura content extraction provider.

Migrated from backend/app/services/full_content_service.py (TrafilaturaProvider class).
Uses trafilatura for web content extraction with excellent accuracy (F1 ~0.92)
and strong Chinese content support.

Image download and re-encode is OPTIONAL (controlled by include_images parameter,
default False for speed).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.core.executor import run_in_executor

logger = logging.getLogger(__name__)

# Timeout for content fetching (seconds)
FETCH_TIMEOUT = 30

# Minimum content length to be considered complete
MIN_CONTENT_LENGTH = 500

# Maximum content length to store
MAX_CONTENT_LENGTH = 50000


def detect_language(text: str) -> str:
    """Simple language detection based on character ranges.

    Args:
        text: Text to analyze.

    Returns:
        Language code (en, zh, etc.).
    """
    if not text:
        return "en"
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    total_chars = len(text)
    if total_chars > 0 and chinese_chars / total_chars > 0.1:
        return "zh"
    return "en"


def calculate_word_count(text: str, language: str) -> int:
    """Calculate word count aware of CJK languages.

    For Chinese/Japanese/Korean: counts CJK characters (excluding punctuation).
    For other languages: counts words (split by whitespace).

    Args:
        text: Text to analyze.
        language: Language code (zh, ja, ko, en, etc.).

    Returns:
        Word/character count.
    """
    if not text:
        return 0
    if language in ("zh", "ja", "ko"):
        cjk_chars = re.findall(
            r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text,
        )
        return len(cjk_chars)
    else:
        return len(text.split())


# Image re-encoding constants
_MAX_IMAGES_TOTAL_BYTES = 5 * 1024 * 1024  # 5 MB total
_MAX_IMAGE_BYTES = 2 * 1024 * 1024          # 2 MB per image
_IMAGE_TIMEOUT = 10


def _reencode_image(
    raw_bytes: bytes, mime: str,
) -> Tuple[Optional[bytes], str]:
    """Re-encode image via Pillow to normalize JPEG quirks.

    Go-based LLM servers use Go's standard image/jpeg decoder which
    doesn't support all JPEG subsampling modes. Re-encoding through
    Pillow (libjpeg) produces universally compatible output.

    Returns (re-encoded bytes, mime) or (None, mime) on failure.
    """
    try:
        from io import BytesIO
        from PIL import Image

        img = Image.open(BytesIO(raw_bytes))
        if img.mode in ("P", "RGBA", "LA"):
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85, subsampling="4:2:0")
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        logger.debug("Image re-encode failed, skipping: %s", e)
        return None, mime


class TrafilaturaContentProvider:
    """Content provider using trafilatura for web content extraction.

    trafilatura provides a 3-tier fallback extraction pipeline
    (own algorithm -> jusText -> readability-lxml) with excellent accuracy.
    """

    def __init__(self) -> None:
        self._trafilatura_available: Optional[bool] = None

    def _check_available(self) -> bool:
        """Check if trafilatura is installed."""
        if self._trafilatura_available is None:
            try:
                import trafilatura  # noqa: F401
                self._trafilatura_available = True
            except ImportError:
                logger.warning(
                    "trafilatura not installed. Install with: pip install trafilatura"
                )
                self._trafilatura_available = False
        return self._trafilatura_available

    async def extract(
        self,
        url: str,
        language: str = "en",
        include_images: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Extract content from a URL using trafilatura.

        Args:
            url: Article URL.
            language: Expected language (en, zh, etc.).
            include_images: Whether to download and base64-encode images.
                Defaults to False for speed. Set True when multimodal
                LLM processing is needed.

        Returns:
            Dict matching ContentResult model, or None on failure.
        """
        start_time = time.monotonic()
        logger.info("Trafilatura fetch: url=%s, language=%s", url[:100], language)

        if not self._check_available():
            return {
                "url": url,
                "success": False,
                "error": "trafilatura not available",
                "extraction_method": "trafilatura",
                "word_count": 0,
            }

        try:
            import json as _json
            import trafilatura

            loop = asyncio.get_running_loop()

            def _fetch_and_extract() -> Tuple[Optional[str], List[str]]:
                """Download page and extract content in a worker thread."""
                downloaded = trafilatura.fetch_url(url)
                if downloaded is None:
                    return None, []

                result_json = trafilatura.extract(
                    downloaded,
                    output_format="json",
                    include_comments=False,
                    include_tables=True,
                    favor_recall=True,
                    with_metadata=True,
                )

                # Optionally extract image URLs from raw HTML
                image_urls: List[str] = []
                if include_images:
                    try:
                        from app.utils.image_extraction import extract_image_urls
                        image_urls = extract_image_urls(downloaded, url, max_images=5)
                    except ImportError:
                        logger.debug("image_extraction not available, skipping images")

                return result_json, image_urls

            result = await asyncio.wait_for(
                loop.run_in_executor(None, _fetch_and_extract),
                timeout=FETCH_TIMEOUT + 5,
            )
            result_json, image_urls = result

            if result_json is None:
                elapsed = time.monotonic() - start_time
                logger.warning(
                    "Trafilatura returned no data: url=%s, elapsed=%.2fs",
                    url[:80], elapsed,
                )
                return {
                    "url": url,
                    "success": False,
                    "error": "trafilatura returned no content (download or extraction failed)",
                    "extraction_method": "trafilatura",
                    "word_count": 0,
                }

            try:
                extracted = _json.loads(result_json)
            except _json.JSONDecodeError as je:
                logger.error(
                    "Trafilatura returned invalid JSON for %s: %s",
                    url[:80], str(je)[:200],
                )
                return {
                    "url": url,
                    "success": False,
                    "error": f"Content extraction returned invalid JSON: {str(je)[:200]}",
                    "extraction_method": "trafilatura",
                    "word_count": 0,
                }

            full_text = (extracted.get("text") or "").strip()

            # Always detect language (trafilatura often misdetects Chinese)
            detected_language = detect_language(full_text) if full_text else (language or "en")
            word_count = calculate_word_count(full_text, detected_language)
            is_partial = len(full_text) < MIN_CONTENT_LENGTH

            # Truncate if too long
            if len(full_text) > MAX_CONTENT_LENGTH:
                full_text = full_text[:MAX_CONTENT_LENGTH] + "..."

            # Parse author (as comma-separated string, matching backend expectation)
            author = None
            raw_author = extracted.get("author")
            if raw_author:
                author = raw_author.strip()

            # Parse publish date
            publish_date = None
            raw_date = extracted.get("date")
            if raw_date:
                try:
                    from dateutil.parser import parse as parse_date
                    parsed_date = parse_date(raw_date)
                    if parsed_date.tzinfo is None:
                        from datetime import timezone as _tz
                        parsed_date = parsed_date.replace(tzinfo=_tz.utc)
                    publish_date = parsed_date.isoformat()
                except (ValueError, TypeError, ImportError):
                    pass

            # Parse keywords
            keywords = None
            raw_tags = extracted.get("tags")
            if raw_tags:
                keywords = [t.strip() for t in raw_tags.split(",") if t.strip()]

            top_image = extracted.get("image")
            hostname = extracted.get("hostname")
            sitename = extracted.get("sitename")

            # Optionally download images as base64
            downloaded_images: List[Dict[str, str]] = []
            if include_images and image_urls:
                downloaded_images = await self._download_images_as_base64(image_urls)

            elapsed = time.monotonic() - start_time
            logger.info(
                "Trafilatura fetch succeeded: url=%s, words=%d, chars=%d, "
                "partial=%s, images=%d/%d, elapsed=%.2fs",
                url[:80], word_count, len(full_text), is_partial,
                len(downloaded_images), len(image_urls), elapsed,
            )

            result_dict: Dict[str, Any] = {
                "url": url,
                "full_text": full_text if full_text else None,
                "title": extracted.get("title"),
                "author": author,
                "keywords": keywords,
                "top_image": top_image if top_image else None,
                "word_count": word_count,
                "language": detected_language,
                "publish_date": publish_date,
                "is_partial": is_partial,
                "extraction_method": "trafilatura",
                "success": True,
                "metadata": {
                    "hostname": hostname,
                    "sitename": sitename,
                    "categories": extracted.get("categories"),
                },
            }

            if downloaded_images:
                result_dict["images"] = downloaded_images

            return result_dict

        except asyncio.TimeoutError:
            logger.error("Timeout fetching content from %s", url)
            return {
                "url": url,
                "success": False,
                "error": f"Timeout after {FETCH_TIMEOUT}s",
                "extraction_method": "trafilatura",
                "word_count": 0,
            }
        except Exception as e:
            logger.error("Error extracting content from %s: %s", url, e)
            return {
                "url": url,
                "success": False,
                "error": str(e)[:500],
                "extraction_method": "trafilatura",
                "word_count": 0,
            }

    async def _download_images_as_base64(
        self, image_urls: List[str],
    ) -> List[Dict[str, str]]:
        """Download images and encode as base64 for multimodal LLM.

        Downloads sequentially with size limits. Skips images that are too
        large, unreachable, or not a recognized image MIME type.

        Returns:
            List of {"url": ..., "base64": ..., "mime": ...} dicts.
        """
        if not image_urls:
            return []

        import base64
        import httpx

        results: List[Dict[str, str]] = []
        total_bytes = 0

        async with httpx.AsyncClient(
            timeout=_IMAGE_TIMEOUT,
            follow_redirects=True,
        ) as client:
            for img_url in image_urls:
                if total_bytes >= _MAX_IMAGES_TOTAL_BYTES:
                    logger.debug("Image download budget exhausted, skipping remaining")
                    break
                try:
                    resp = await client.get(img_url)
                    resp.raise_for_status()

                    content_type = resp.headers.get("content-type", "")
                    if "jpeg" in content_type or "jpg" in content_type:
                        mime = "image/jpeg"
                    elif "png" in content_type:
                        mime = "image/png"
                    elif "webp" in content_type:
                        mime = "image/webp"
                    elif "gif" in content_type:
                        mime = "image/gif"
                    elif "svg" in content_type:
                        continue
                    else:
                        lower = img_url.lower()
                        if ".jpg" in lower or ".jpeg" in lower:
                            mime = "image/jpeg"
                        elif ".png" in lower:
                            mime = "image/png"
                        elif ".webp" in lower:
                            mime = "image/webp"
                        else:
                            continue

                    img_bytes = resp.content
                    if len(img_bytes) > _MAX_IMAGE_BYTES:
                        logger.debug(
                            "Image too large (%d bytes), skipping: %s",
                            len(img_bytes), img_url[:80],
                        )
                        continue

                    img_bytes, mime = _reencode_image(img_bytes, mime)
                    if not img_bytes:
                        continue

                    total_bytes += len(img_bytes)
                    b64 = base64.b64encode(img_bytes).decode("ascii")
                    results.append({
                        "url": img_url,
                        "base64": b64,
                        "mime": mime,
                    })
                except Exception as e:
                    logger.debug("Failed to download image %s: %s", img_url[:80], e)
                    continue

        return results
