"""Shared helper functions for Celery worker tasks.

Provides:
- run_async_task(): Run async coroutines in Celery with proper event loop
  lifecycle and singleton reset.
- ensure_usage_recorder(): One-time LLM cost tracking registration.
- run_layer1_scoring_if_enabled(): Layer 1 3-agent scoring wrapper.
- build_score_details(): Serialize Layer1ScoringResult for DB storage.
"""

import asyncio
import importlib
import logging
from typing import Any, Callable, Dict, List, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# One-time registration flag for LLM usage recorder in Celery workers
_recorder_registered = False

# Singletons to reset after each event loop closes.
# Each entry: (module_path, reset_function_name)
_SINGLETON_RESETS = [
    ("app.core.llm", "reset_llm_gateway"),
    ("app.db.redis", "reset_redis"),
    ("app.services.full_content_service", "reset_full_content_service"),
    ("app.services.content_cleaning_service", "reset_content_cleaning_service"),
    ("app.services.rag", "reset_index_service"),
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
        loop.close()
        _reset_singletons()


# ---------------------------------------------------------------------------
# Layer 1 scoring helpers
# ---------------------------------------------------------------------------

async def run_layer1_scoring_if_enabled(
    db,
    system_settings,
    articles: List[Dict[str, str]],
) -> tuple[List, bool]:
    """Run Layer 1 scoring if LLM pipeline is enabled.

    Args:
        db: Database session.
        system_settings: System settings with feature flag.
        articles: List of dicts with url, title, text (summary).

    Returns:
        Tuple of (list of Layer1ScoringResult, is_enabled bool).
    """
    if not system_settings.enable_llm_pipeline:
        return [], False

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

    results = await scoring_service.batch_score_articles(db, scoring_articles)
    return results, True


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
