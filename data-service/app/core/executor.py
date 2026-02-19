"""ThreadPoolExecutor for bridging synchronous data provider libraries to async.

Many data provider libraries (yfinance, akshare, tushare, finnhub) are synchronous.
This executor runs them in a thread pool so they don't block the FastAPI event loop.
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable, Optional, TypeVar

from app.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

_executor: Optional[ThreadPoolExecutor] = None


def get_executor() -> ThreadPoolExecutor:
    """Get or create the shared ThreadPoolExecutor."""
    global _executor
    if _executor is None:
        settings = get_settings()
        _executor = ThreadPoolExecutor(
            max_workers=settings.EXECUTOR_MAX_WORKERS,
            thread_name_prefix="data-provider",
        )
        logger.info(
            "ThreadPoolExecutor initialized: max_workers=%d",
            settings.EXECUTOR_MAX_WORKERS,
        )
    return _executor


def shutdown_executor() -> None:
    """Gracefully shut down the executor. Call on application shutdown."""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
        logger.info("ThreadPoolExecutor shut down")


async def run_in_executor(
    func: Callable[..., T],
    *args: Any,
    timeout: float = 30.0,
    **kwargs: Any,
) -> T:
    """Run a synchronous function in the thread pool with timeout.

    Args:
        func: The synchronous function to execute.
        *args: Positional arguments for func.
        timeout: Maximum seconds to wait (default 30).
        **kwargs: Keyword arguments for func.

    Returns:
        The return value of func.

    Raises:
        asyncio.TimeoutError: If the function exceeds the timeout.
    """
    func_name = getattr(func, "__name__", str(func))
    start = time.monotonic()

    loop = asyncio.get_running_loop()
    executor = get_executor()

    if kwargs:
        call = partial(func, *args, **kwargs)
    else:
        call = partial(func, *args) if args else func

    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(executor, call),
            timeout=timeout,
        )
        elapsed = time.monotonic() - start
        logger.debug("Executor: %s completed in %.2fs", func_name, elapsed)
        return result
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        logger.warning(
            "Executor: %s timed out after %.2fs (limit: %.1fs)",
            func_name,
            elapsed,
            timeout,
        )
        raise
    except Exception:
        elapsed = time.monotonic() - start
        logger.error(
            "Executor: %s failed after %.2fs",
            func_name,
            elapsed,
            exc_info=True,
        )
        raise
