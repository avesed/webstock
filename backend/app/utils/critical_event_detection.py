"""Critical event keyword detection for news pipeline.

Provides fast regex-based detection of critical financial events across
Chinese and English keywords. Used as a fast-path in scoring services
to bypass LLM evaluation for obviously high-impact events.

Extracted from ``news_scoring_service.py`` for reuse across Layer 1
and Layer 2 scoring pipelines.
"""

import re

# ---------------------------------------------------------------------------
# Critical event keyword lists
# ---------------------------------------------------------------------------

CRITICAL_KEYWORDS_ZH = [
    # 地缘/安全
    "战争", "军事冲突", "武装冲突", "紧急", "恐怖袭击",
    # 交易异常
    "熔断", "暂停交易", "交易暂停",
    # 公司重大事件
    "破产", "退市", "重大欺诈",
    # 资本市场重大事件
    "IPO", "上市冲刺", "拟上市", "首次公开募股",
    "重大并购", "合并重组", "收购要约", "借壳上市",
    "股权结构", "拆分上市", "私有化",
    # 宏观政策
    "加息", "降息", "央行政策",
    "市场崩盘", "监管禁令", "行业整顿",
]

CRITICAL_KEYWORDS_EN = [
    # Geopolitical/security
    "war", "military conflict", "armed conflict",
    "emergency", "terrorist attack",
    # Trading anomalies
    "circuit breaker", "trading halt", "trading suspended",
    # Corporate critical events
    "bankruptcy", "delisting", "major fraud",
    # Capital markets
    "IPO", "initial public offering", "going public",
    "major acquisition", "merger", "takeover bid", "restructuring",
    "stock split", "privatization", "spinoff",
    # Macro policy
    "rate hike", "rate cut", "central bank",
    "market crash", "regulatory ban", "industry crackdown",
]

# Pre-compiled regex for O(n) matching across all keywords in a single pass.
# Case-insensitive for English keywords; Chinese keywords are inherently
# case-insensitive.
CRITICAL_PATTERN = re.compile(
    "|".join(
        re.escape(kw)
        for kw in CRITICAL_KEYWORDS_ZH + CRITICAL_KEYWORDS_EN
    ),
    re.IGNORECASE,
)


def detect_critical_event(title: str, text: str) -> bool:
    """Check if title or text contains critical event keywords.

    Uses the pre-compiled ``CRITICAL_PATTERN`` regex for O(n) matching
    across all keywords in a single pass. Only inspects the first
    2 000 characters of the text body to keep detection fast.

    Args:
        title: Article title.
        text: Article body text (may be arbitrarily long; only the
              first 2 000 characters are examined).

    Returns:
        ``True`` if any critical event keyword is found in the title
        or the inspected portion of the text.
    """
    combined = f"{title} {text[:2000]}"
    return bool(CRITICAL_PATTERN.search(combined))
