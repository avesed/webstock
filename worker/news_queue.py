"""Redis queue dispatch helpers for the standalone news consumer.

Lightweight module importable from Celery tasks (e.g. batch_fetch_content)
without pulling in heavy consumer dependencies.  Each helper serializes a
message dict and RPUSHes it to the consumer's Redis LIST queue.

Queue protocol:
    Each message is a JSON object with:
        task_type: str  — "process_article" | "analyze_important" | "retry_score"
        ...task-specific fields
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Redis keys (must match news_consumer.py)
QUEUE_KEY = "news:consumer:queue"
RETRY_KEY = "news:consumer:retry"
DEAD_LETTER_KEY = "news:consumer:dead_letter"
IN_FLIGHT_KEY = "news:consumer:in_flight"
HEALTH_KEY = "news:consumer:health"
METRICS_KEY = "news:consumer:metrics"


async def enqueue_process_article(
    redis,
    *,
    news_id: str,
    url: str,
    source: str = "",
    file_path: Optional[str] = None,
    market: str = "US",
    symbol: str = "",
    title: str = "",
    summary: str = "",
    published_at: Optional[str] = None,
    content_score: int = 0,
    processing_path: str = "full_analysis",
    score_details: Optional[dict] = None,
) -> int:
    """Enqueue a process_article task to the news consumer.

    Returns the new queue length.
    """
    message = {
        "task_type": "process_article",
        "news_id": news_id,
        "url": url,
        "source": source,
        "file_path": file_path,
        "market": market,
        "symbol": symbol,
        "title": title,
        "summary": summary,
        "published_at": published_at,
        "content_score": content_score,
        "processing_path": processing_path,
        "score_details": score_details,
    }
    length = await redis.rpush(QUEUE_KEY, json.dumps(message, default=str))
    logger.debug("入队 process_article: news_id=%s, queue_len=%d", news_id, length)
    return length


async def enqueue_analyze_important(redis, *, news_id: str) -> int:
    """Enqueue an analyze_important task to the news consumer.

    Returns the new queue length.
    """
    message = {
        "task_type": "analyze_important",
        "news_id": news_id,
    }
    length = await redis.rpush(QUEUE_KEY, json.dumps(message))
    logger.debug("入队 analyze_important: news_id=%s, queue_len=%d", news_id, length)
    return length


async def enqueue_retry_score(
    redis,
    *,
    articles_data: List[Dict[str, Any]],
    retry_num: int = 0,
) -> int:
    """Enqueue a retry_score task to the news consumer.

    Returns the new queue length.
    """
    message = {
        "task_type": "retry_score",
        "articles_data": articles_data,
        "retry_num": retry_num,
    }
    length = await redis.rpush(QUEUE_KEY, json.dumps(message, default=str))
    logger.debug(
        "入队 retry_score: %d篇, retry=%d, queue_len=%d",
        len(articles_data), retry_num, length,
    )
    return length
