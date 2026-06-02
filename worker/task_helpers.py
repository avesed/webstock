"""Shared helper functions for Celery worker tasks.

Provides:
- run_async_task(): Run async coroutines in Celery with proper event loop
  lifecycle and singleton reset.
- ensure_usage_recorder(): One-time LLM cost tracking registration.
"""

import asyncio
import importlib
import logging
from typing import Any, Callable, Dict, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# One-time registration flag for LLM usage recorder in Celery workers
_recorder_registered = False

# Singletons to reset after each event loop closes.
# Each entry: (module_path, reset_function_name)
_SINGLETON_RESETS = [
    ("app.core.llm", "reset_llm_gateway"),
    ("app.db.redis", "reset_redis"),
    ("app.services.rag", "reset_index_service"),
    ("app.services.stock_list_service", "reset_stock_list_service_sync"),
    ("app.services.stock_profile_service", "reset_stock_profile_service_sync"),
    ("app.services.stockpulse_client", "reset_stockpulse_client"),
    ("app.services.alphaforge_client", "reset_alphaforge_client"),
    ("app.services.ai_gateway_client", "reset_ai_gateway_client"),
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


def _quiet_exception_handler(loop, context):
    """Suppress 'Event loop is closed' from deferred httpx transport cleanup.

    OpenAI SDK's ``AsyncOpenAI.__del__`` calls
    ``asyncio.get_running_loop().create_task(self.aclose())`` when GC
    collects a stale client during a *later* task's event loop.  The task
    fails (old transport bound to a closed loop) and ``Task.__del__`` calls
    ``loop.call_exception_handler()`` on the *current* loop — producing
    noisy "Task exception was never retrieved" log lines that are harmless.
    """
    exc = context.get("exception")
    if isinstance(exc, RuntimeError) and "Event loop is closed" in str(exc):
        return
    loop.default_exception_handler(context)


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

    # StockPulseClient — close httpx.AsyncClient bound to current loop
    try:
        mod = importlib.import_module("app.services.stockpulse_client")
        close_fn = getattr(mod, "close_stockpulse_client", None)
        if close_fn:
            await close_fn()
    except Exception as e:
        logger.debug("StockPulseClient close: %s", e)

    # AlphaForgeClient — close httpx.AsyncClient bound to current loop
    try:
        mod = importlib.import_module("app.services.alphaforge_client")
        close_fn = getattr(mod, "close_alphaforge_client", None)
        if close_fn:
            await close_fn()
    except Exception as e:
        logger.debug("AlphaForgeClient close: %s", e)

    # AiGatewayClient — close httpx.AsyncClient bound to current loop
    try:
        mod = importlib.import_module("app.services.ai_gateway_client")
        close_fn = getattr(mod, "close_ai_gateway_client", None)
        if close_fn:
            await close_fn()
    except Exception as e:
        logger.debug("AiGatewayClient close: %s", e)

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
    # Suppress "Event loop is closed" from deferred httpx transport cleanup.
    # OpenAI SDK's AsyncOpenAI.__del__ calls
    #   asyncio.get_running_loop().create_task(self.aclose())
    # when GC collects an old client during a LATER task's execution.
    # The task fails (old transport, closed loop) and Task.__del__ calls
    # call_exception_handler() on THIS loop — so the handler must be here.
    loop.set_exception_handler(_quiet_exception_handler)
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
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()
        _reset_singletons()
