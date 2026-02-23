"""ThreadPoolExecutor for bridging synchronous data provider libraries to async.

Many data provider libraries (yfinance, akshare, tushare, finnhub) are synchronous.
This executor runs them in a thread pool so they don't block the FastAPI event loop.

Self-healing: A background watchdog periodically probes the executor and recycles
it when threads are stuck. This prevents gradual thread-pool exhaustion caused by
zombie threads (blocking calls that outlive their asyncio timeout).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable, Optional, TypeVar

from app.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
_executor_created_at: float = 0.0

# How often to recycle the executor (seconds). Zombie threads from timed-out
# calls accumulate over time; recycling keeps the pool fresh.
RECYCLE_INTERVAL = 4 * 3600  # 4 hours

# Maximum seconds to wait for the executor health probe.
HEALTH_PROBE_TIMEOUT = 5.0

# Watchdog check interval (seconds)
WATCHDOG_INTERVAL = 120  # 2 minutes

_watchdog_task: Optional[asyncio.Task] = None


def get_executor() -> ThreadPoolExecutor:
    """Get or create the shared ThreadPoolExecutor."""
    global _executor, _executor_created_at
    with _executor_lock:
        if _executor is None:
            settings = get_settings()
            _executor = ThreadPoolExecutor(
                max_workers=settings.EXECUTOR_MAX_WORKERS,
                thread_name_prefix="data-provider",
            )
            _executor_created_at = time.monotonic()
            logger.info(
                "ThreadPoolExecutor initialized: max_workers=%d",
                settings.EXECUTOR_MAX_WORKERS,
            )
    return _executor


def _recycle_executor(reason: str) -> ThreadPoolExecutor:
    """Shut down the old executor and create a fresh one.

    The old executor is told to shut down with cancel_futures=True, but
    zombie threads may linger (Python limitation). The important thing is
    that new tasks go to a healthy pool.
    """
    global _executor, _executor_created_at
    with _executor_lock:
        old = _executor
        settings = get_settings()
        _executor = ThreadPoolExecutor(
            max_workers=settings.EXECUTOR_MAX_WORKERS,
            thread_name_prefix="data-provider",
        )
        _executor_created_at = time.monotonic()
        logger.warning(
            "Executor recycled (%s): new pool max_workers=%d",
            reason,
            settings.EXECUTOR_MAX_WORKERS,
        )
    # Shut down old pool in background — stuck threads won't block us
    if old is not None:
        try:
            old.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
    return _executor


async def check_executor_health() -> bool:
    """Submit a trivial task to the executor and check it completes.

    Returns True if the executor is responsive, False if stuck.
    """
    loop = asyncio.get_running_loop()
    executor = get_executor()

    def _probe() -> bool:
        return True

    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(executor, _probe),
            timeout=HEALTH_PROBE_TIMEOUT,
        )
        return result is True
    except (asyncio.TimeoutError, Exception):
        return False


async def _watchdog_loop() -> None:
    """Background coroutine: periodically checks executor health and age.

    - Recycles on schedule (RECYCLE_INTERVAL) to prevent gradual degradation.
    - Recycles immediately if health probe fails (all threads stuck).
    """
    logger.info(
        "Executor watchdog started: probe every %ds, recycle every %ds",
        WATCHDOG_INTERVAL,
        RECYCLE_INTERVAL,
    )
    while True:
        try:
            await asyncio.sleep(WATCHDOG_INTERVAL)

            # Check age-based recycling
            age = time.monotonic() - _executor_created_at
            if age >= RECYCLE_INTERVAL:
                _recycle_executor(f"age={int(age)}s >= {RECYCLE_INTERVAL}s")
                continue

            # Health probe
            healthy = await check_executor_health()
            if not healthy:
                logger.error(
                    "Executor health probe FAILED — all threads may be stuck"
                )
                _recycle_executor("health probe failed")
        except asyncio.CancelledError:
            logger.info("Executor watchdog cancelled")
            return
        except Exception as e:
            logger.error("Executor watchdog error: %s", e)
            await asyncio.sleep(10)


def start_watchdog() -> None:
    """Launch the executor watchdog as a background asyncio task."""
    global _watchdog_task
    if _watchdog_task is None or _watchdog_task.done():
        _watchdog_task = asyncio.create_task(_watchdog_loop())


async def stop_watchdog() -> None:
    """Cancel the executor watchdog."""
    global _watchdog_task
    if _watchdog_task and not _watchdog_task.done():
        _watchdog_task.cancel()
        try:
            await _watchdog_task
        except asyncio.CancelledError:
            pass
    _watchdog_task = None


def shutdown_executor() -> None:
    """Gracefully shut down the executor. Call on application shutdown."""
    global _executor
    with _executor_lock:
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
