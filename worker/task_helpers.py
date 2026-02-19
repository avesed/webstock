"""Shared helper functions for Celery worker tasks.

Provides:
- run_async_task(): Run async coroutines in Celery with proper event loop
  lifecycle and singleton reset.
- ensure_usage_recorder(): One-time LLM cost tracking registration.
- run_layer1_scoring_if_enabled(): Layer 1 3-agent scoring wrapper.
- build_score_details(): Serialize Layer1ScoringResult for DB storage.
- Redis pending-set helpers for scoring retry dedup.
"""

import asyncio
import importlib
import logging
from typing import Any, Callable, Dict, List, Set, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# One-time registration flag for LLM usage recorder in Celery workers
_recorder_registered = False

# Singletons to reset after each event loop closes.
# Each entry: (module_path, reset_function_name)
_SINGLETON_RESETS = [
    ("app.core.llm", "reset_llm_gateway"),
    ("app.db.redis", "reset_redis"),
    ("app.services.content_cleaning_service", "reset_content_cleaning_service"),
    ("app.services.rag", "reset_index_service"),
    ("app.services.stock_list_service", "reset_stock_list_service_sync"),
    ("app.services.stock_profile_service", "reset_stock_profile_service_sync"),
    ("app.services.data_service_client", "reset_data_service_client"),
]


def ensure_usage_recorder():
    """Register the LLM usage recorder once per worker process.

    Hooks into the LLM gateway so that every LLM call automatically
    records token usage and cost to the database.  Safe to call
    multiple times — only the first invocation has effect.
    """
    global _recorder_registered
    if _recorder_registered:
        return
    try:
        from app.core.llm import set_llm_usage_recorder
        from app.services.llm_cost_service import get_llm_cost_service

        async def _record(
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

        set_llm_usage_recorder(_record)
        _recorder_registered = True
        logger.debug("LLM usage recorder registered for Celery worker")
    except Exception as e:
        logger.warning("Failed to register LLM usage recorder: %s", e)


def _reset_singletons():
    """Reset all singleton async clients after event loop close.

    Celery tasks create a fresh event loop per invocation.  Singleton
    clients (LLM gateway, Redis, content services) may hold references
    to the now-closed loop, causing "Event loop is closed" errors on
    the next task.  Resetting them forces re-creation on next use.
    """
    for module_path, func_name in _SINGLETON_RESETS:
        try:
            module = importlib.import_module(module_path)
            getattr(module, func_name)()
        except Exception as e:
            logger.warning("Failed to call %s.%s: %s", module_path, func_name, e)


async def _close_async_clients():
    """Gracefully close async clients while the event loop is still alive.

    Must be called BEFORE loop.close() so that httpx/asyncpg protocol
    objects can properly clean up their Futures on the current loop.
    """
    # LLM gateway — closes OpenAI/Anthropic httpx clients
    try:
        mod = importlib.import_module("app.core.llm")
        gateway = mod.get_llm_gateway()
        await gateway.close()
    except Exception as e:
        logger.debug("Gateway close: %s", e)

    # Redis — close aioredis connection
    try:
        mod = importlib.import_module("app.db.redis")
        redis_close = getattr(mod, "close_redis", None)
        if redis_close:
            await redis_close()
    except Exception as e:
        logger.debug("Redis close: %s", e)

    # DataServiceClient — close httpx.AsyncClient bound to current loop
    try:
        mod = importlib.import_module("app.services.data_service_client")
        close_fn = getattr(mod, "close_data_service_client", None)
        if close_fn:
            await close_fn()
    except Exception as e:
        logger.debug("DataServiceClient close: %s", e)

    # Database engine — dispose pooled asyncpg connections.
    # The module-level engine in database.py keeps a connection pool;
    # those connections hold asyncpg protocol Futures bound to the
    # current event loop.  Disposing frees them so the next task
    # (on a new loop) gets fresh connections.
    try:
        db_mod = importlib.import_module("app.db.database")
        await db_mod.engine.dispose()
    except Exception as e:
        logger.debug("Database engine dispose: %s", e)


def run_async_task(coro_func: Callable[..., T], *args, **kwargs) -> T:
    """Run an async function in a new event loop, properly cleaning up afterwards.

    This helper ensures all singleton async clients are reset after each task
    to avoid "Event loop is closed" errors when tasks reuse singleton clients
    that were bound to different (now closed) event loops.

    Args:
        coro_func: Async callable to execute.
        *args, **kwargs: Forwarded to coro_func.

    Returns:
        The return value of coro_func.
    """
    ensure_usage_recorder()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro_func(*args, **kwargs))
    finally:
        # Close async clients BEFORE closing the loop — their internal
        # httpx connections hold protocol Futures bound to this loop.
        # If we close the loop first, those Futures become stale and cause
        # "got Future attached to a different loop" in the next task.
        try:
            loop.run_until_complete(_close_async_clients())
        except Exception as e:
            logger.warning("Error closing async clients: %s", e)
        loop.close()
        _reset_singletons()


# ---------------------------------------------------------------------------
# Layer 1 scoring helpers
# ---------------------------------------------------------------------------

async def run_layer1_scoring_if_enabled(
    db,
    system_settings,
    articles: List[Dict[str, str]],
):
    """Run Layer 1 scoring if LLM pipeline is enabled.

    Args:
        db: Database session.
        system_settings: System settings with feature flag.
        articles: List of dicts with url, title, text (summary).

    Returns:
        Tuple of (BatchScoringOutcome, is_enabled bool).
    """
    from app.services.layer1_scoring_service import BatchScoringOutcome

    if not system_settings.enable_llm_pipeline:
        return BatchScoringOutcome(results=[], timed_out_articles=[]), False

    from app.services.layer1_scoring_service import get_layer1_scoring_service

    scoring_service = get_layer1_scoring_service()

    # Format articles for scoring service
    scoring_articles = [
        {
            "url": a.get("url", ""),
            "title": a.get("headline", a.get("title", "")),
            "text": a.get("summary", ""),
        }
        for a in articles
    ]

    outcome = await scoring_service.batch_score_articles(db, scoring_articles)
    return outcome, True


# ---------------------------------------------------------------------------
# Redis pending-set helpers for scoring retry dedup
# ---------------------------------------------------------------------------

SCORING_PENDING_KEY = "news:scoring:pending"
_PENDING_TTL = 1800  # 30 minutes


async def add_scoring_pending(urls: List[str]):
    """Mark URLs as pending retry scoring (for dedup in next monitor run).

    Uses a Redis SET with a 30-minute TTL as safety net.
    """
    if not urls:
        return
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        pipe = redis.pipeline()
        pipe.sadd(SCORING_PENDING_KEY, *urls)
        # nx=True: only set TTL when key is new, don't extend on every SADD
        pipe.expire(SCORING_PENDING_KEY, _PENDING_TTL, nx=True)
        await pipe.execute()
        logger.debug("Scoring pending: added %d URLs", len(urls))
    except Exception as e:
        logger.warning("Failed to add scoring pending URLs: %s", e)


async def remove_scoring_pending(urls: List[str]):
    """Remove URLs from pending set after scoring completes or fails-open."""
    if not urls:
        return
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        await redis.srem(SCORING_PENDING_KEY, *urls)
        logger.debug("Scoring pending: removed %d URLs", len(urls))
    except Exception as e:
        logger.warning("Failed to remove scoring pending URLs: %s", e)


async def get_scoring_pending() -> Set[str]:
    """Get all URLs currently pending retry scoring (for dedup check)."""
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        return await redis.smembers(SCORING_PENDING_KEY)
    except Exception as e:
        logger.warning("Failed to get scoring pending URLs: %s", e)
        return set()


def build_score_details(scoring_result) -> dict:
    """Build score_details dict from Layer1ScoringResult for DB storage.

    Returns a JSON-serializable dict with dimension scores, per-agent
    details, reasoning, and critical event flag.
    """
    return {
        "dimensionScores": {
            name: s.score
            for name, s in scoring_result.agent_scores.items()
        },
        "agentDetails": {
            name: {
                "tier": s.tier,
                "score": s.score,
                "reason": s.reason,
            }
            for name, s in scoring_result.agent_scores.items()
        },
        "reasoning": scoring_result.reasoning,
        "isCriticalEvent": scoring_result.is_critical,
    }
