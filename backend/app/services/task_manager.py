"""Background task manager with Redis Stream event buffering.

Decouples LangGraph workflow execution from SSE connections so that:
1. Workflows continue running after client disconnect
2. Clients can reconnect and replay missed events via lastEventId
3. Any uvicorn worker can serve SSE for any task (cross-worker via Redis)
"""

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, AsyncGenerator, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# TTLs in seconds
ANALYSIS_TTL = 600        # 10 minutes (analysis takes ~2 min)
DISCUSSION_TTL = 1800     # 30 minutes (discussion can take ~10 min)
STREAM_MAXLEN = 2000      # Max events per Redis Stream (approximate trim)
HEARTBEAT_UPDATE_INTERVAL = 30   # seconds between metadata heartbeat updates
HEARTBEAT_INTERVAL = 15          # seconds between synthetic heartbeats to SSE consumer
DEAD_TASK_THRESHOLD = 90         # seconds without heartbeat → considered dead

# Lua CAS script: DELETE active key only if its value matches our task_id
_CAS_DELETE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class TaskManager:
    """Manages background asyncio tasks with Redis Stream event buffering."""

    def __init__(self) -> None:
        self._local_tasks: Dict[str, asyncio.Task] = {}

    # ── Core: get or create a background task ──

    async def get_or_create_task(
        self,
        task_type: str,
        user_id: int,
        symbol: str,
        market: str,
        language: str,
        workflow_factory: Callable[[], AsyncGenerator[Dict[str, Any], None]],
        *,
        session_id: str = "",
    ) -> Tuple[str, bool]:
        """Find existing running task or start a new one.

        Returns (task_id, is_new). If is_new=False, the caller should
        just subscribe to the existing task's event stream.
        """
        from app.db.redis import get_redis

        redis = await get_redis()
        active_key = f"task:active:{task_type}:{user_id}:{symbol}"
        ttl = DISCUSSION_TTL if task_type == "discussion" else ANALYSIS_TTL

        # Check for existing active task
        existing_task_id = await redis.get(active_key)
        if existing_task_id:
            meta = await redis.hgetall(f"task:meta:{existing_task_id}")
            if meta:
                task_status = meta.get("status", "")

                if task_status == "running":
                    # Verify it's not a dead task (stale heartbeat)
                    last_hb = float(meta.get("last_heartbeat", meta.get("created_at", "0")))
                    if time.time() - last_hb < DEAD_TASK_THRESHOLD:
                        logger.info(
                            "任务管理: 复用已有任务 %s type=%s symbol=%s",
                            existing_task_id[:8], task_type, symbol,
                        )
                        return existing_task_id, False
                    else:
                        logger.warning(
                            "任务管理: 已有任务 %s 心跳超时 (%.0fs), 清理并重新创建",
                            existing_task_id[:8], time.time() - last_hb,
                        )

                elif task_status == "completed":
                    # Only replay completed task if session_id matches (same
                    # session reconnecting) or no session_id is involved
                    # (analysis).  A new discussion session must NOT replay
                    # the old one's events.
                    existing_session = meta.get("session_id", "")
                    same_session = (
                        not session_id
                        or not existing_session
                        or session_id == existing_session
                    )
                    if same_session:
                        stream_exists = await redis.exists(
                            f"task:events:{existing_task_id}"
                        )
                        if stream_exists:
                            logger.info(
                                "任务管理: 复用已完成任务 %s (流仍可重放)",
                                existing_task_id[:8],
                            )
                            return existing_task_id, False
                    else:
                        logger.info(
                            "任务管理: 跳过已完成任务 %s (新会话 %s ≠ %s)",
                            existing_task_id[:8],
                            session_id[:8],
                            existing_session[:8],
                        )

            # Stale/dead/failed/cancelled — clean up active key
            await redis.delete(active_key)

        # Create new task
        task_id = str(uuid.uuid4())
        now = str(time.time())

        # Atomically claim the active slot (NX = only if not exists)
        acquired = await redis.set(active_key, task_id, nx=True, ex=ttl)
        if not acquired:
            # Race: another request claimed it between our GET and SET NX
            actual_task_id = await redis.get(active_key)
            if actual_task_id:
                return actual_task_id, False
            # Key vanished between SET NX and GET — try once more atomically
            acquired = await redis.set(active_key, task_id, nx=True, ex=ttl)
            if not acquired:
                actual_task_id = await redis.get(active_key)
                if actual_task_id:
                    return actual_task_id, False
                # Extremely unlikely — proceed with task creation anyway

        # Write metadata (include active_key for cleanup in _run_workflow)
        meta_key = f"task:meta:{task_id}"
        await redis.hset(meta_key, mapping={
            "type": task_type,
            "user_id": str(user_id),
            "symbol": symbol,
            "market": market,
            "language": language,
            "status": "running",
            "worker_id": str(os.getpid()),
            "created_at": now,
            "last_heartbeat": now,
            "session_id": session_id,
            "active_key": active_key,
        })
        await redis.expire(meta_key, ttl)

        # Start the background asyncio task
        async_task = asyncio.create_task(
            self._run_workflow(task_id, task_type, workflow_factory, ttl),
            name=f"task:{task_type}:{task_id[:8]}",
        )
        self._local_tasks[task_id] = async_task
        async_task.add_done_callback(lambda _: self._local_tasks.pop(task_id, None))

        logger.info(
            "任务管理: 创建新任务 %s type=%s symbol=%s worker=%d",
            task_id[:8], task_type, symbol, os.getpid(),
        )
        return task_id, True

    # ── Workflow runner (background task body) ──

    async def _run_workflow(
        self,
        task_id: str,
        task_type: str,
        workflow_factory: Callable[[], AsyncGenerator[Dict[str, Any], None]],
        ttl: int,
    ) -> None:
        """Execute the workflow and publish events to Redis Stream."""
        from app.db.redis import get_redis

        redis = await get_redis()
        stream_key = f"task:events:{task_id}"
        meta_key = f"task:meta:{task_id}"

        # Independent heartbeat task — keeps heartbeat fresh even when
        # the LLM takes a long time between events (prevents false dead-task)
        heartbeat_task: Optional[asyncio.Task] = None

        async def _heartbeat_loop() -> None:
            consecutive_failures = 0
            try:
                while True:
                    await asyncio.sleep(HEARTBEAT_UPDATE_INTERVAL)
                    try:
                        await redis.hset(meta_key, "last_heartbeat", str(time.time()))
                        consecutive_failures = 0
                    except Exception:
                        consecutive_failures += 1
                        if consecutive_failures >= 3:
                            logger.warning(
                                "Heartbeat persistently failing for %s (%d consecutive misses)",
                                task_id[:8], consecutive_failures,
                            )
                        else:
                            logger.debug("Heartbeat update failed for %s", task_id[:8])
            except asyncio.CancelledError:
                pass

        try:
            heartbeat_task = asyncio.create_task(
                _heartbeat_loop(), name=f"hb:{task_id[:8]}",
            )

            async for event in workflow_factory():
                event_json = json.dumps(event, ensure_ascii=False, default=str)
                await redis.xadd(
                    stream_key,
                    {"event": event_json},
                    maxlen=STREAM_MAXLEN,
                    approximate=True,
                )

            # Mark completed (but don't overwrite if already cancelled by another worker)
            try:
                current_meta = await redis.hgetall(meta_key)
                if current_meta.get("status") == "cancelled":
                    logger.info(
                        "任务管理: 任务 %s 已被取消, 不覆盖为completed",
                        task_id[:8],
                    )
                else:
                    await redis.hset(meta_key, mapping={
                        "status": "completed",
                        "completed_at": str(time.time()),
                    })
            except Exception:
                logger.warning("Failed to mark task %s as completed in Redis", task_id[:8])
            logger.info("任务管理: 任务完成 %s type=%s", task_id[:8], task_type)

        except asyncio.CancelledError:
            logger.info("任务管理: 任务取消 %s", task_id[:8])
            try:
                await redis.hset(meta_key, mapping={
                    "status": "cancelled",
                    "completed_at": str(time.time()),
                })
                cancel_event = json.dumps({
                    "type": "error",
                    "data": {"message": "Task was cancelled"},
                    "timestamp": time.time(),
                })
                await redis.xadd(
                    stream_key, {"event": cancel_event},
                    maxlen=STREAM_MAXLEN, approximate=True,
                )
            except Exception:
                logger.warning("Failed to write cancel status for task %s", task_id[:8])

        except Exception as e:
            logger.exception("任务管理: 工作流异常 task=%s: %s", task_id[:8], e)
            try:
                await redis.hset(meta_key, mapping={
                    "status": "failed",
                    "completed_at": str(time.time()),
                    "error": str(e)[:500],
                })
                error_event = json.dumps({
                    "type": "error",
                    "data": {"message": f"Workflow error: {str(e)[:200]}"},
                    "timestamp": time.time(),
                })
                await redis.xadd(
                    stream_key, {"event": error_event},
                    maxlen=STREAM_MAXLEN, approximate=True,
                )
            except Exception:
                logger.warning("Failed to write error status for task %s", task_id[:8])

        finally:
            # Stop heartbeat
            if heartbeat_task and not heartbeat_task.done():
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

            # Set TTL on the stream for garbage collection
            try:
                await redis.expire(stream_key, ttl)
            except Exception:
                pass

            # Clean up active key on failure/cancellation via CAS
            # (only delete if it still points to our task_id).
            # On completion, KEEP the active key so completed tasks are
            # discoverable for stream replay.
            try:
                meta = await redis.hgetall(meta_key)
                final_status = meta.get("status", "")
                active_key = meta.get("active_key", "")
                if active_key and final_status in ("failed", "cancelled"):
                    try:
                        cas_result = await redis.eval(_CAS_DELETE_SCRIPT, 1, active_key, task_id)
                        if cas_result == 0:
                            logger.warning("CAS delete rejected for task %s — active key claimed by another task", task_id[:8])
                    except Exception:
                        logger.warning("Active key cleanup failed for task %s", task_id[:8], exc_info=True)
                elif active_key and final_status == "completed":
                    # Refresh active key TTL to stay in sync with stream TTL
                    await redis.expire(active_key, ttl)
            except Exception:
                logger.warning("Active key cleanup failed for task %s", task_id[:8], exc_info=True)

            # For discussion tasks: mark orphaned DB session as failed
            # so it doesn't get stuck in "discussing" forever
            if task_type == "discussion" and final_status in ("failed", "cancelled"):
                session_id = meta.get("session_id", "") if meta else ""
                if session_id:
                    try:
                        await self._mark_discussion_failed(session_id)
                    except Exception:
                        logger.debug("Discussion orphan cleanup failed for %s", session_id[:8])

    @staticmethod
    async def _mark_discussion_failed(session_id: str) -> None:
        """Mark a discussion session as failed in PostgreSQL.

        Called when a discussion background task ends with failed/cancelled
        status, preventing the session from being stuck in 'discussing'.
        """
        import uuid as uuid_mod
        from app.db.task_session import get_task_session
        from sqlalchemy import select, update

        try:
            session_uuid = uuid_mod.UUID(session_id)
        except ValueError:
            return

        async with get_task_session() as db:
            from app.models.discussion import DiscussionSession
            result = await db.execute(
                update(DiscussionSession)
                .where(
                    DiscussionSession.id == session_uuid,
                    DiscussionSession.status.in_(["discussing", "synthesizing"]),
                )
                .values(status="failed", error="Background task ended abnormally")
            )
            if result.rowcount > 0:
                await db.commit()
                logger.info("任务管理: 标记讨论会话为失败 session=%s", session_id[:8])

    # ── Subscribe to events (called by SSE endpoint) ──

    async def subscribe_events(
        self,
        task_id: str,
        last_event_id: str = "0-0",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Subscribe to a task's event stream.

        Replays all events after last_event_id, then live-tails until
        the task reaches a terminal state.

        Yields dicts: {"event_id": str, "event": dict}
        """
        from app.db.redis import get_redis

        redis = await get_redis()
        stream_key = f"task:events:{task_id}"
        meta_key = f"task:meta:{task_id}"
        cursor = last_event_id
        last_yield_time = time.time()

        # Terminal event types that signal end of stream
        terminal_types = frozenset({
            "complete", "error", "discussion_complete", "timeout", "cancelled",
        })

        while True:
            # XREAD with BLOCK for efficient long-polling
            results = await redis.xread(
                {stream_key: cursor},
                count=100,
                block=1000,  # 1 second block timeout
            )

            if results:
                for _stream_name, entries in results:
                    for entry_id, fields in entries:
                        cursor = entry_id
                        event_json = fields.get("event", "{}")
                        try:
                            event = json.loads(event_json)
                        except json.JSONDecodeError:
                            continue

                        yield {"event_id": entry_id, "event": event}
                        last_yield_time = time.time()

                        # Check for terminal events
                        if event.get("type", "") in terminal_types:
                            return
            else:
                # No new events within block timeout — inject synthetic
                # heartbeat so the SSE generator can keep the connection alive
                if time.time() - last_yield_time >= HEARTBEAT_INTERVAL:
                    yield {"event_id": "__heartbeat__", "event": {"type": "heartbeat"}}
                    last_yield_time = time.time()

                # Check task health
                meta = await redis.hgetall(meta_key)
                if not meta:
                    # Metadata expired — task is gone
                    return

                status = meta.get("status", "")
                if status in ("completed", "failed", "cancelled"):
                    # Task finished — drain any remaining events
                    remaining = await redis.xrange(
                        stream_key, min=f"({cursor}", max="+",
                    )
                    for entry_id, fields in remaining:
                        event_json = fields.get("event", "{}")
                        try:
                            event = json.loads(event_json)
                        except json.JSONDecodeError:
                            continue
                        yield {"event_id": entry_id, "event": event}
                    return

                # Check for dead task (stale heartbeat)
                if status == "running":
                    last_hb = float(meta.get(
                        "last_heartbeat", meta.get("created_at", "0"),
                    ))
                    if time.time() - last_hb > DEAD_TASK_THRESHOLD:
                        logger.warning(
                            "任务管理: 任务 %s 心跳超时, 视为死亡",
                            task_id[:8],
                        )
                        yield {
                            "event_id": "dead",
                            "event": {
                                "type": "error",
                                "data": {"message": "Task appears to have stopped. Please retry."},
                                "timestamp": time.time(),
                            },
                        }
                        return

    # ── Task lookup and control ──

    async def find_active_task(
        self, task_type: str, user_id: int, symbol: str,
        *, include_completed: bool = False,
    ) -> Optional[str]:
        """Find active task_id for a (type, user, symbol) combination.

        Args:
            include_completed: If True, also return completed tasks whose
                event streams are still available in Redis for replay.
        """
        from app.db.redis import get_redis

        redis = await get_redis()
        active_key = f"task:active:{task_type}:{user_id}:{symbol}"
        task_id = await redis.get(active_key)
        if task_id:
            meta = await redis.hgetall(f"task:meta:{task_id}")
            if meta:
                status = meta.get("status", "")
                if status == "running":
                    return task_id
                if include_completed and status == "completed":
                    # Verify stream still exists for replay
                    if await redis.exists(f"task:events:{task_id}"):
                        return task_id
        return None

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, str]]:
        """Get task metadata hash."""
        from app.db.redis import get_redis

        redis = await get_redis()
        meta = await redis.hgetall(f"task:meta:{task_id}")
        return meta or None

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a task. Returns True if found and marked cancelled."""
        from app.db.redis import get_redis

        # Try local cancellation first (stops the actual workflow)
        task = self._local_tasks.get(task_id)
        if task and not task.done():
            task.cancel()
            return True

        # Not on this worker — mark in Redis and inject cancel event to stream
        # (workflow continues on the other worker, but subscribe_events will
        # see the terminal event and stop; the workflow will eventually timeout)
        redis = await get_redis()
        meta = await redis.hgetall(f"task:meta:{task_id}")
        if meta and meta.get("status") == "running":
            await redis.hset(f"task:meta:{task_id}", mapping={
                "status": "cancelled",
                "completed_at": str(time.time()),
            })
            # Inject cancel event so subscribers see it immediately
            try:
                cancel_event = json.dumps({
                    "type": "error",
                    "data": {"message": "Task was cancelled"},
                    "timestamp": time.time(),
                })
                await redis.xadd(
                    f"task:events:{task_id}",
                    {"event": cancel_event},
                    maxlen=STREAM_MAXLEN,
                    approximate=True,
                )
            except Exception:
                pass
            # Clean up active key via CAS
            active_key = meta.get("active_key", "")
            if active_key:
                try:
                    await redis.eval(_CAS_DELETE_SCRIPT, 1, active_key, task_id)
                except Exception:
                    pass
            return True
        return False

    async def cleanup(self) -> None:
        """Cancel all local tasks. Called during shutdown."""
        if not self._local_tasks:
            return
        logger.info("任务管理: 清理 %d 个本地任务", len(self._local_tasks))
        for task_id, task in list(self._local_tasks.items()):
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
        self._local_tasks.clear()


# Singleton
_task_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """Get the singleton TaskManager instance."""
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager
