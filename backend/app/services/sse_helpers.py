"""Shared SSE helpers for reconnectable streaming via Redis Streams.

Wraps TaskManager.subscribe_events() with:
- SSE `id:` field for client-side lastEventId tracking
- Periodic heartbeat injection (15s)
- Overall timeout enforcement
"""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# SSE configuration
HEARTBEAT_INTERVAL = 15  # seconds between heartbeat SSE comments
DEFAULT_TIMEOUT = 300    # 5 minutes (analysis)


async def reconnectable_sse_generator(
    task_id: str,
    last_event_id: str = "0-0",
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> AsyncGenerator[str, None]:
    """Subscribe to a task's Redis Stream and yield SSE-formatted lines.

    Each event is emitted as:
        id: {redis_stream_id}
        data: {json}

        (blank line terminates the event per SSE spec)

    Heartbeats are injected as SSE comments (`: heartbeat`) every 15s
    when no real events arrive — keeps proxies and clients alive.

    Args:
        task_id: The background task to subscribe to.
        last_event_id: Redis Stream ID to resume from (e.g. "0-0" for start).
        timeout_seconds: Maximum streaming duration before forced close.
    """
    from app.services.task_manager import get_task_manager

    task_manager = get_task_manager()
    start_time = time.time()
    last_yield_time = time.time()

    try:
        async for entry in task_manager.subscribe_events(task_id, last_event_id):
            # Timeout check
            if time.time() - start_time > timeout_seconds:
                timeout_event = json.dumps({
                    "type": "timeout",
                    "message": "Streaming timeout reached",
                    "timestamp": time.time(),
                })
                yield f"data: {timeout_event}\n\n"
                return

            event_id = entry["event_id"]
            event = entry["event"]

            # Synthetic heartbeat from subscribe_events() — emit as SSE comment
            if event_id == "__heartbeat__":
                yield ": heartbeat\n\n"
                last_yield_time = time.time()
                continue

            # Emit SSE with id: field for reconnection
            event_json = json.dumps(event, ensure_ascii=False, default=str)
            yield f"id: {event_id}\ndata: {event_json}\n\n"
            last_yield_time = time.time()

    except asyncio.CancelledError:
        logger.debug("SSE generator cancelled for task %s", task_id[:8])
    except Exception:
        logger.exception("SSE generator error for task %s", task_id[:8])
        error_event = json.dumps({
            "type": "error",
            "message": "Streaming error",
            "timestamp": time.time(),
        })
        yield f"data: {error_event}\n\n"
