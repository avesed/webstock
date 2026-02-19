"""Image URL extraction from HTML content for multimodal LLM processing.

Extracts, filters, and prioritizes image URLs from raw HTML. Designed
for use in the news pipeline where article HTML may contain charts,
financial tables, or other visually informative images that benefit
from multimodal analysis.

No external HTML parsing dependencies required — uses regex-based
extraction suitable for the tag-level patterns we care about.
"""

import logging
import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exclusion patterns — tracking pixels, ads, social widgets, tiny assets
# ---------------------------------------------------------------------------
_EXCLUDE_PATTERNS: List[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"pixel",
        r"tracker",
        r"beacon",
        r"analytics",
        r"facebook\.com",
        r"twitter\.com",
        r"x\.com/.*\.(png|jpg|svg)",
        r"linkedin\.com",
        r"gravatar\.com",
        r"\.gif(\?|$)",
        r"\blogo\b",
        r"\bicon\b",
        r"\bavatar\b",
        r"\bbadge\b",
        r"\bbutton\b",
        r"\bbanner\b",
        r"advertisement",
        r"sponsor",
        r"\bpromo\b",
        r"spacer",
        r"blank\.(png|jpg|gif)",
        r"1x1\.",
        r"transparent\.",
        r"share[-_]?icon",
        r"social[-_]?(icon|button|share)",
        r"emoticon",
        r"emoji",
        r"widget",
        r"thumbnail[-_]?placeholder",
    ]
]

# Domains that almost exclusively serve tracking / ad assets
_EXCLUDE_DOMAINS = {
    "ad.doubleclick.net",
    "pagead2.googlesyndication.com",
    "pixel.quantserve.com",
    "b.scorecardresearch.com",
    "sb.scorecardresearch.com",
    "pixel.wp.com",
    "stats.wp.com",
    "www.google-analytics.com",
    "www.facebook.com",
    "connect.facebook.net",
    "platform.twitter.com",
}

# ---------------------------------------------------------------------------
# Priority patterns — images likely to contain financial information
# ---------------------------------------------------------------------------
_PRIORITY_PATTERNS: List[Tuple[re.Pattern, int]] = [
    (re.compile(p, re.IGNORECASE), score)
    for p, score in [
        (r"chart", 3),
        (r"graph", 3),
        (r"figure", 2),
        (r"table", 2),
        (r"financial", 2),
        (r"earnings", 2),
        (r"revenue", 2),
        (r"stock", 1),
        (r"market", 1),
        (r"screenshot", 2),
        (r"report", 1),
        (r"\bdata\b", 1),
        (r"performance", 1),
        (r"quarterly", 2),
        (r"annual", 1),
        (r"infographic", 2),
        (r"comparison", 1),
        (r"forecast", 2),
        (r"valuation", 2),
        (r"balance[-_]?sheet", 3),
        (r"income[-_]?statement", 3),
        (r"cash[-_]?flow", 3),
        (r"candlestick", 3),
        (r"technical[-_]?analysis", 3),
    ]
]

# Regex to extract <img> tags with attributes
_IMG_TAG_RE = re.compile(
    r"<img\s[^>]*?>",
    re.IGNORECASE | re.DOTALL,
)

# Regex to extract the src attribute value
_SRC_RE = re.compile(
    r"""\bsrc\s*=\s*(?:"([^"]*?)"|'([^']*?)')""",
    re.IGNORECASE,
)

# Regex to extract width/height attributes (HTML attribute values)
_WIDTH_RE = re.compile(
    r"""\bwidth\s*=\s*(?:"(\d+)"|'(\d+)'|(\d+))""",
    re.IGNORECASE,
)
_HEIGHT_RE = re.compile(
    r"""\bheight\s*=\s*(?:"(\d+)"|'(\d+)'|(\d+))""",
    re.IGNORECASE,
)

# Inline style dimension extraction (e.g., style="width: 50px; height: 30px")
_STYLE_WIDTH_RE = re.compile(r"width\s*:\s*(\d+)\s*px", re.IGNORECASE)
_STYLE_HEIGHT_RE = re.compile(r"height\s*:\s*(\d+)\s*px", re.IGNORECASE)

# Minimum dimension in pixels — below this, images are likely icons or spacers
_MIN_DIMENSION = 100

# Default maximum images to return
MAX_IMAGES = 5

# Valid image extensions
_VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".svg"}


def extract_image_urls(
    html_content: str,
    base_url: str,
    max_images: int = MAX_IMAGES,
) -> List[str]:
    """Extract and prioritize image URLs from HTML content.

    Parses ``<img>`` tags from raw HTML, filters out tracking pixels, ads,
    tiny icons, and social media widgets, then returns up to *max_images*
    absolute URLs sorted by financial relevance.

    Args:
        html_content: Raw HTML string to extract images from.
        base_url: Base URL used to resolve relative ``src`` paths.
        max_images: Maximum number of image URLs to return.

    Returns:
        List of absolute image URLs, ordered by descending priority score.
        Empty list if no suitable images are found.
    """
    if not html_content or not base_url:
        return []

    seen_urls: set = set()
    candidates: List[Tuple[str, int]] = []  # (url, priority_score)

    img_tags = _IMG_TAG_RE.findall(html_content)
    logger.debug("Found %d <img> tags in HTML content", len(img_tags))

    for tag in img_tags:
        # Extract src
        src_match = _SRC_RE.search(tag)
        if not src_match:
            continue

        raw_url = src_match.group(1) or src_match.group(2)
        if not raw_url or not raw_url.strip():
            continue

        raw_url = raw_url.strip()

        # Skip data URIs (base64-encoded inline images) — they have no URL
        if raw_url.startswith("data:"):
            continue

        # Resolve to absolute URL
        absolute_url = urljoin(base_url, raw_url)

        # Deduplicate
        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)

        # Validate URL scheme
        parsed = urlparse(absolute_url)
        if parsed.scheme not in ("http", "https"):
            continue

        # Check domain exclusion
        hostname = parsed.hostname or ""
        if hostname in _EXCLUDE_DOMAINS:
            continue

        # Check URL-based exclusion patterns
        if _is_excluded(absolute_url):
            logger.debug("Excluded image (pattern match): %s", absolute_url)
            continue

        # Check dimensions from HTML attributes — skip obviously tiny images
        if _is_too_small(tag):
            logger.debug("Excluded image (too small): %s", absolute_url)
            continue

        # Validate extension if path has one (allow extensionless URLs,
        # which are common with CDN/image service URLs)
        if not _has_valid_extension(parsed.path):
            continue

        # Compute priority score
        score = _priority_score(absolute_url, tag)
        candidates.append((absolute_url, score))

    # Sort by priority score descending, then by order of appearance (stable sort)
    candidates.sort(key=lambda c: c[1], reverse=True)

    result = [url for url, _score in candidates[:max_images]]

    logger.debug(
        "Extracted %d images from %d candidates (max %d)",
        len(result),
        len(candidates),
        max_images,
    )
    return result


def _is_excluded(url: str) -> bool:
    """Check if a URL matches any exclusion pattern.

    Args:
        url: Absolute image URL to check.

    Returns:
        True if the URL should be excluded.
    """
    for pattern in _EXCLUDE_PATTERNS:
        if pattern.search(url):
            return True
    return False


def _is_too_small(tag: str) -> bool:
    """Check if an ``<img>`` tag specifies dimensions below the minimum.

    Checks both HTML attributes (``width="50"``) and inline styles
    (``style="width: 50px"``). If no dimension is specified, the image
    is *not* excluded (we cannot know its size without fetching it).

    Args:
        tag: Raw ``<img>`` tag string.

    Returns:
        True if any specified dimension is below ``_MIN_DIMENSION``.
    """
    # Check HTML attributes
    width = _extract_dimension(_WIDTH_RE, tag)
    height = _extract_dimension(_HEIGHT_RE, tag)

    # Check inline style
    if width is None:
        style_w = _STYLE_WIDTH_RE.search(tag)
        if style_w:
            width = int(style_w.group(1))

    if height is None:
        style_h = _STYLE_HEIGHT_RE.search(tag)
        if style_h:
            height = int(style_h.group(1))

    # If either dimension is specified and too small, exclude
    if width is not None and width < _MIN_DIMENSION:
        return True
    if height is not None and height < _MIN_DIMENSION:
        return True

    return False


def _extract_dimension(pattern: re.Pattern, tag: str) -> Optional[int]:
    """Extract an integer dimension value from an ``<img>`` tag.

    The regex captures the value from quoted or unquoted attributes
    across three capture groups. Returns the first non-None match.

    Args:
        pattern: Compiled regex with up to 3 capture groups.
        tag: Raw ``<img>`` tag string.

    Returns:
        Integer pixel value or None if not found.
    """
    match = pattern.search(tag)
    if not match:
        return None

    for group in match.groups():
        if group is not None:
            try:
                return int(group)
            except ValueError:
                return None
    return None


def _priority_score(url: str, tag: str) -> int:
    """Score a URL by its likelihood of being a valuable financial image.

    Higher scores indicate images more likely to contain charts, tables,
    financial data, or other content useful for multimodal LLM analysis.

    The score considers both the URL path/filename and the ``alt`` text
    from the ``<img>`` tag.

    Args:
        url: Absolute image URL.
        tag: Raw ``<img>`` tag string (used for alt text extraction).

    Returns:
        Non-negative integer score. Higher is better.
    """
    score = 0

    # Build the text to match against: URL + alt text
    alt_match = re.search(
        r"""\balt\s*=\s*(?:"([^"]*?)"|'([^']*?)')""",
        tag,
        re.IGNORECASE,
    )
    alt_text = ""
    if alt_match:
        alt_text = alt_match.group(1) or alt_match.group(2) or ""

    search_text = f"{url} {alt_text}"

    for pattern, pattern_score in _PRIORITY_PATTERNS:
        if pattern.search(search_text):
            score += pattern_score

    # Bonus for larger images if dimensions are specified
    width = _extract_dimension(_WIDTH_RE, tag)
    height = _extract_dimension(_HEIGHT_RE, tag)
    if width is not None and width >= 600:
        score += 1
    if height is not None and height >= 400:
        score += 1

    return score


def _has_valid_extension(path: str) -> bool:
    """Check if a URL path has a recognized image extension.

    URLs without any extension are allowed (common with CDN services
    like Cloudinary, imgix, etc.). Only URLs with a non-image extension
    (e.g., ``.js``, ``.css``) are rejected.

    Args:
        path: URL path component.

    Returns:
        True if the path has a valid image extension or no extension.
    """
    # Strip query params that may have leaked into path parsing
    clean_path = path.split("?")[0].split("#")[0]

    # Find the last dot in the filename portion
    last_slash = clean_path.rfind("/")
    filename = clean_path[last_slash + 1:] if last_slash >= 0 else clean_path

    dot_pos = filename.rfind(".")
    if dot_pos < 0:
        # No extension — allow (CDN URLs often lack extensions)
        return True

    ext = filename[dot_pos:].lower()
    return ext in _VALID_EXTENSIONS


# ---------------------------------------------------------------------------
# Module-level singleton access
# ---------------------------------------------------------------------------

class ImageExtractor:
    """Stateless image extraction utility.

    Wraps the module-level functions into a class for consistency with
    the singleton access pattern used elsewhere in the codebase
    (e.g., ``get_full_content_service()``).
    """

    def extract(
        self,
        html_content: str,
        base_url: str,
        max_images: int = MAX_IMAGES,
    ) -> List[str]:
        """Extract image URLs from HTML content.

        Delegates to :func:`extract_image_urls`. See that function for
        full documentation.
        """
        return extract_image_urls(html_content, base_url, max_images)


_instance: Optional[ImageExtractor] = None


def get_image_extractor() -> ImageExtractor:
    """Return the module-level ImageExtractor singleton.

    Returns:
        Shared ``ImageExtractor`` instance.
    """
    global _instance
    if _instance is None:
        _instance = ImageExtractor()
    return _instance
