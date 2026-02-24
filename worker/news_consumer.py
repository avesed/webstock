"""Standalone asyncio consumer for news LLM processing tasks.

Replaces the Celery ``news_llm`` worker with a single long-running
asyncio process.  Reads JSON messages from a Redis LIST queue
(``news:consumer:queue``) via BLPOP and processes them through
LangGraph pipelines with bounded concurrency.

Lifecycle:
    - Self-restarts after MAX_ARTICLES processed, MAX_UPTIME elapsed,
      or MAX_RSS_MB resident memory exceeded.
    - Graceful shutdown on SIGTERM/SIGINT: drains in-flight tasks,
      re-queues unfinished work, closes singletons.
    - Crashed in-flight items recovered from Redis HASH on next start.

Usage (Docker / supervisord):
    python -m worker.news_consumer

Environment:
    NEWS_CONSUMER_CONCURRENCY  (default 10)
    LOG_TAG                    (default "news")
"""

import asyncio
import hashlib
import json
import logging
import os
import resource
import signal
import sys
import time
from typing import Any, Dict, Optional, Set

from worker.news_queue import (
    QUEUE_KEY,
    RETRY_KEY,
    DEAD_LETTER_KEY,
    IN_FLIGHT_KEY,
    HEALTH_KEY,
    METRICS_KEY,
)

logger = logging.getLogger(__name__)


class NewsConsumer:
    """Asyncio-based consumer for news LLM processing tasks."""

    MAX_ARTICLES = 5000
    MAX_UPTIME = 86400  # 24 hours
    MAX_RSS_MB = 2048   # 2 GB resident memory limit for force-restart

    def __init__(self, concurrency: int = 10) -> None:
        self._concurrency = concurrency
        self._semaphore = asyncio.Semaphore(concurrency)
        self._shutdown_event = asyncio.Event()
        self._tasks: Set[asyncio.Task] = set()

        # Counters
        self._processed_count = 0
        self._failed_count = 0
        self._timed_out_count = 0
        self._dead_lettered_count = 0
        self._start_time: float = 0.0       # monotonic (for uptime calc)
        self._started_at_wall: float = 0.0  # wall clock (for health reporting)

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialize subsystems and run the consumer loops."""
        self._start_time = time.monotonic()
        self._started_at_wall = time.time()

        # Import Celery app so that news_pipeline.py can call
        # embed_news_article.apply_async() (pure Redis RPUSH, no worker needed).
        import worker.celery_app  # noqa: F401

        # Initialize Redis connection pool
        from app.db.redis import init_redis
        await init_redis()

        # Pre-compile the LangGraph pipeline (singleton)
        from app.agents.langgraph.workflows.news_pipeline import get_news_pipeline
        get_news_pipeline()
        logger.info("LangGraph新闻管道已编译")

        # Register LLM usage recorder for cost tracking
        from worker.task_helpers import ensure_usage_recorder
        ensure_usage_recorder()

        # Signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal, sig)

        # Recover in-flight items from previous crash
        await self._recover_in_flight()

        logger.info(
            "新闻消费者启动: concurrency=%d, max_articles=%d, "
            "max_uptime=%ds, max_rss=%dMB",
            self._concurrency, self.MAX_ARTICLES,
            self.MAX_UPTIME, self.MAX_RSS_MB,
        )

        # Run background loops concurrently
        try:
            await asyncio.gather(
                self._queue_consumer_loop(),
                self._retry_poller_loop(),
                self._heartbeat_loop(),
            )
        except Exception:
            logger.exception("消费者主循环异常")
        finally:
            await self._graceful_shutdown()

    def _handle_signal(self, sig: signal.Signals) -> None:
        """Set shutdown event on SIGTERM/SIGINT."""
        logger.info("收到信号 %s，准备关闭", sig.name)
        self._shutdown_event.set()

    # ------------------------------------------------------------------
    # Queue consumer loop
    # ------------------------------------------------------------------

    async def _queue_consumer_loop(self) -> None:
        """BLPOP from the main queue and dispatch tasks."""
        from app.db.redis import get_redis

        while not self._shutdown_event.is_set():
            try:
                redis = await get_redis()
                result = await redis.blpop(QUEUE_KEY, timeout=2)

                if result is None:
                    # Timeout — check self-restart conditions
                    if self._should_self_restart():
                        logger.info(
                            "触发自重启: processed=%d, uptime=%.0fs, rss=%dMB",
                            self._processed_count,
                            time.monotonic() - self._start_time,
                            self._check_rss_memory(),
                        )
                        self._shutdown_event.set()
                    continue

                # result is (key, value) tuple from BLPOP
                _, raw_message = result

                try:
                    message = json.loads(raw_message)
                except json.JSONDecodeError:
                    logger.error("无效JSON消息，丢弃: %s", raw_message[:200])
                    continue

                logger.debug(
                    "出队: task_type=%s, key=%s",
                    message.get("task_type"), self._get_message_key(message),
                )
                await self._semaphore.acquire()
                task = asyncio.create_task(
                    self._process_with_isolation(message)
                )
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("队列消费循环异常，1秒后重试")
                await asyncio.sleep(1)

    def _should_self_restart(self) -> bool:
        """Check if self-restart conditions are met."""
        if self._processed_count >= self.MAX_ARTICLES:
            return True
        if (time.monotonic() - self._start_time) >= self.MAX_UPTIME:
            return True
        if self._check_rss_memory() >= self.MAX_RSS_MB:
            return True
        return False

    # ------------------------------------------------------------------
    # Task isolation wrapper
    # ------------------------------------------------------------------

    async def _process_with_isolation(self, message: dict) -> None:
        """Process a single message with request ID, in-flight tracking, and timeout."""
        from app.core.request_id import request_id_var, generate_request_id
        from app.db.redis import get_redis

        rid = generate_request_id()
        request_id_var.set(rid)

        message_key = self._get_message_key(message)

        try:
            redis = await get_redis()
            await redis.hset(IN_FLIGHT_KEY, message_key, json.dumps(message, default=str))
        except Exception:
            logger.warning("无法写入in_flight记录: %s", message_key)

        try:
            async with asyncio.timeout(600):
                await self._route_and_process(message)
            self._processed_count += 1
        except TimeoutError:
            logger.warning(
                "任务超时(600s): task_type=%s, key=%s",
                message.get("task_type"), message_key,
            )
            await self._handle_retry(message, "timeout (600s)")
            self._timed_out_count += 1
        except Exception as e:
            logger.exception(
                "任务异常: task_type=%s, key=%s",
                message.get("task_type"), message_key,
            )
            await self._handle_retry(message, str(e)[:500])
            self._failed_count += 1
        finally:
            # Clean up
            try:
                redis = await get_redis()
                await redis.hdel(IN_FLIGHT_KEY, message_key)
            except Exception as cleanup_err:
                logger.warning("清除in-flight记录失败: key=%s: %s", message_key, cleanup_err)
            request_id_var.set(None)
            self._semaphore.release()

    @staticmethod
    def _get_message_key(message: dict) -> str:
        """Derive a unique key for in-flight tracking."""
        task_type = message.get("task_type", "")
        if task_type in ("process_article", "analyze_important"):
            return message.get("news_id", "unknown")
        # retry_score: hash the articles_data for uniqueness
        articles = message.get("articles_data", [])
        urls = sorted(a.get("url", "") for a in articles)
        return hashlib.md5("|".join(urls).encode()).hexdigest()

    # ------------------------------------------------------------------
    # Task routing
    # ------------------------------------------------------------------

    async def _route_and_process(self, message: dict) -> None:
        """Route message to the appropriate handler by task_type."""
        task_type = message.get("task_type")

        if task_type == "process_article":
            from app.agents.langgraph.workflows.news_pipeline import run_news_pipeline

            final_state = await run_news_pipeline(
                news_id=message["news_id"],
                url=message.get("url", ""),
                market=message.get("market", "US"),
                symbol=message.get("symbol", ""),
                title=message.get("title", ""),
                summary=message.get("summary", ""),
                published_at=message.get("published_at"),
                source=message.get("source", ""),
                file_path=message.get("file_path"),
                content_score=message.get("content_score", 0),
                processing_path=message.get("processing_path", "lightweight"),
                score_details=message.get("score_details"),
            )
            status = final_state.get("final_status", "unknown")
            logger.info(
                "process_article完成: news_id=%s, status=%s",
                message["news_id"], status,
            )

        elif task_type == "analyze_important":
            from worker.tasks.news_monitor import _analyze_news_async

            result = await _analyze_news_async(message["news_id"])
            logger.info(
                "analyze_important完成: news_id=%s, status=%s",
                message["news_id"], result.get("status", "unknown"),
            )

        elif task_type == "retry_score":
            from worker.tasks.news_monitor import (
                _retry_score_async,
                _ScoringPartialTimeout,
            )

            articles_data = message.get("articles_data", [])
            retry_num = message.get("retry_num", 0)
            try:
                result = await _retry_score_async(articles_data, retry_num)
                logger.info(
                    "retry_score完成: %d篇, retry=%d, status=%s",
                    len(articles_data), retry_num, result.get("status", "unknown"),
                )
            except _ScoringPartialTimeout as e:
                # Some articles scored OK (already stored), but a subset timed out.
                # Cap retries at 3 total attempts (matching Celery max_retries=2).
                if retry_num >= 2:
                    logger.warning(
                        "retry_score部分超时已达最大重试(%d次): %d篇转轻量处理",
                        retry_num + 1, len(e.timed_out_data),
                    )
                    await self._fail_open_retry_score(
                        {**message, "articles_data": e.timed_out_data}
                    )
                else:
                    # Enqueue narrowed retry — wrap in try/except so a Redis
                    # failure does not cause the full original message to be
                    # retried (the successfully scored articles are already committed).
                    try:
                        from app.db.redis import get_redis
                        from worker.news_queue import enqueue_retry_score

                        redis = await get_redis()
                        await enqueue_retry_score(
                            redis,
                            articles_data=e.timed_out_data,
                            retry_num=retry_num + 1,
                        )
                        logger.info(
                            "retry_score部分超时: %d篇成功, %d篇重新入队 (next retry_num=%d)",
                            len(articles_data) - len(e.timed_out_data),
                            len(e.timed_out_data),
                            retry_num + 1,
                        )
                    except Exception as enqueue_err:
                        logger.error(
                            "retry_score: 重新入队超时子集失败(%d篇): %s. "
                            "这些文章将留在pending集合中",
                            len(e.timed_out_data), enqueue_err,
                        )

        else:
            logger.error("未知的task_type: %s", task_type)

    # ------------------------------------------------------------------
    # Retry / dead-letter handling
    # ------------------------------------------------------------------

    async def _handle_retry(self, message: dict, error: str) -> None:
        """Retry with exponential backoff or dead-letter after 3 attempts."""
        from app.db.redis import get_redis

        retry_count = message.get("_retry_count", 0)
        task_type = message.get("task_type", "unknown")

        if retry_count >= 3:
            logger.warning(
                "重试耗尽(%d次), 转死信队列: task_type=%s, error=%s",
                retry_count, task_type, error[:200],
            )
            # Dead-letter
            try:
                redis = await get_redis()
                dead_msg = {**message, "_error": error, "_dead_at": time.time()}
                await redis.rpush(DEAD_LETTER_KEY, json.dumps(dead_msg, default=str))
                # Cap dead-letter queue at 1000 items
                await redis.ltrim(DEAD_LETTER_KEY, -1000, -1)
            except Exception:
                logger.exception("写入死信队列失败")
            self._dead_lettered_count += 1

            # Fail-open for exhausted retries
            if task_type == "process_article":
                await self._fail_open_article(message)
            elif task_type == "retry_score":
                await self._fail_open_retry_score(message)
        else:
            # Schedule retry with exponential backoff
            backoff = 30 * (2 ** retry_count)
            message["_retry_count"] = retry_count + 1
            try:
                redis = await get_redis()
                score = time.time() + backoff
                await redis.zadd(
                    RETRY_KEY,
                    {json.dumps(message, default=str): score},
                )
                logger.info(
                    "计划重试: task_type=%s, retry=%d, backoff=%ds",
                    task_type, retry_count + 1, backoff,
                )
            except Exception:
                logger.exception("写入重试队列失败")

    async def _fail_open_article(self, message: dict) -> None:
        """Fail-open for process_article that exhausted retries.

        Mark the article as keep with FAILED content status so it is not
        lost but also not blocking the pipeline.
        """
        from app.db.task_session import get_task_session
        from app.models.news import News, ContentStatus

        news_id = message.get("news_id")
        if not news_id:
            logger.warning(
                "fail-open: process_article消息缺少news_id, 无法更新DB. url=%s",
                message.get("url", "?"),
            )
            return

        try:
            from sqlalchemy import select

            async with get_task_session() as db:
                result = await db.execute(
                    select(News).where(News.id == news_id)
                )
                news = result.scalar_one_or_none()
                if news:
                    news.filter_status = "keep"
                    news.content_status = ContentStatus.FAILED.value
                    news.content_error = "consumer retry exhausted"
                    await db.commit()
                    logger.info("fail-open: news_id=%s 标记为keep/FAILED", news_id)
        except Exception:
            logger.exception("fail-open更新DB失败: news_id=%s", news_id)

    async def _fail_open_retry_score(self, message: dict) -> None:
        """Fail-open for retry_score that exhausted retries.

        Delegates to the existing _fail_open_store() which stores articles
        with lightweight processing path.
        """
        articles_data = message.get("articles_data", [])
        if not articles_data:
            return

        try:
            from worker.tasks.news_monitor import _fail_open_store
            result = await _fail_open_store(articles_data)
            logger.info(
                "retry_score fail-open: %d篇, stored=%d",
                len(articles_data), result.get("stored", 0),
            )
        except Exception:
            logger.exception("retry_score fail-open失败")
            # Last resort: clean up Redis pending set
            try:
                from worker.task_helpers import remove_scoring_pending
                urls = [a.get("url", "") for a in articles_data]
                await remove_scoring_pending(urls)
            except Exception as cleanup_err:
                logger.error(
                    "retry_score fail-open: pending集合清理也失败(%d URLs): %s",
                    len(articles_data), cleanup_err,
                )

    # ------------------------------------------------------------------
    # Retry poller loop
    # ------------------------------------------------------------------

    async def _retry_poller_loop(self) -> None:
        """Poll the retry ZSET every 5s and re-queue due items."""
        from app.db.redis import get_redis

        while not self._shutdown_event.is_set():
            try:
                redis = await get_redis()
                now = time.time()
                # Get items whose scheduled time has passed
                due_items = await redis.zrangebyscore(
                    RETRY_KEY, "-inf", str(now), start=0, num=50,
                )

                for raw in due_items:
                    removed = await redis.zrem(RETRY_KEY, raw)
                    if removed:
                        await redis.rpush(QUEUE_KEY, raw)

                if due_items:
                    logger.info("重试队列: %d个任务重新入队", len(due_items))

                # Trim stale entries (>24h old) and enforce size cap
                stale_cutoff = now - 86400
                stale_removed = await redis.zremrangebyscore(
                    RETRY_KEY, "-inf", str(stale_cutoff)
                )
                if stale_removed:
                    logger.warning("重试队列: 清除%d个过期条目(>24h)", stale_removed)

                size = await redis.zcard(RETRY_KEY)
                if size > 5000:
                    excess = await redis.zremrangebyrank(RETRY_KEY, 0, size - 5001)
                    logger.warning("重试队列超容量: 裁剪%d个最旧条目", excess)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("重试轮询异常")

            # Sleep in small increments to respect shutdown
            for _ in range(5):
                if self._shutdown_event.is_set():
                    break
                await asyncio.sleep(1)

    # ------------------------------------------------------------------
    # Heartbeat loop
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Write health info to Redis every 30s."""
        from app.db.redis import get_redis

        while not self._shutdown_event.is_set():
            try:
                redis = await get_redis()
                now = time.time()
                rss_mb = self._check_rss_memory()

                # Include queue/retry depths for monitoring
                retry_pending = await redis.zcard(RETRY_KEY)
                queue_depth = await redis.llen(QUEUE_KEY)

                health_data = {
                    "status": "running",
                    "pid": os.getpid(),
                    "started_at": self._started_at_wall,
                    "last_heartbeat": now,
                    "uptime_s": int(time.monotonic() - self._start_time),
                    "active_tasks": len(self._tasks),
                    "semaphore_available": self._semaphore._value,
                    "processed": self._processed_count,
                    "failed": self._failed_count,
                    "timed_out": self._timed_out_count,
                    "dead_lettered": self._dead_lettered_count,
                    "rss_mb": rss_mb,
                    "concurrency": self._concurrency,
                    "queue_depth": queue_depth,
                    "retry_pending": retry_pending,
                }

                pipe = redis.pipeline()
                pipe.hset(HEALTH_KEY, mapping={
                    k: json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                    for k, v in health_data.items()
                })
                pipe.expire(HEALTH_KEY, 120)
                # Also update a metrics key with longer TTL for monitoring
                pipe.hset(METRICS_KEY, mapping={
                    k: str(v) for k, v in health_data.items()
                })
                pipe.expire(METRICS_KEY, 300)
                await pipe.execute()

                # Memory warnings
                if rss_mb >= self.MAX_RSS_MB:
                    logger.error(
                        "内存超限: %dMB >= %dMB, 触发强制重启",
                        rss_mb, self.MAX_RSS_MB,
                    )
                    self._shutdown_event.set()
                elif rss_mb >= 1536:  # 1.5 GB warning threshold
                    logger.warning("内存警告: %dMB (阈值 %dMB)", rss_mb, self.MAX_RSS_MB)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("心跳循环异常")

            # Sleep in small increments to respect shutdown
            for _ in range(30):
                if self._shutdown_event.is_set():
                    break
                await asyncio.sleep(1)

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------

    async def _graceful_shutdown(self) -> None:
        """Drain in-flight tasks, re-queue unfinished, close singletons."""
        logger.info("开始优雅关闭, 等待 %d 个进行中的任务...", len(self._tasks))

        # Wait up to 45s for in-flight tasks to finish
        if self._tasks:
            done, pending = await asyncio.wait(self._tasks, timeout=45)
            if pending:
                logger.warning("关闭超时, 取消 %d 个未完成任务", len(pending))
                for task in pending:
                    task.cancel()
                # Give cancelled tasks a moment to clean up
                await asyncio.wait(pending, timeout=5)

        # Re-queue any items still in the in-flight HASH
        await self._requeue_in_flight()

        # Update health status
        try:
            from app.db.redis import get_redis
            redis = await get_redis()
            await redis.hset(HEALTH_KEY, "status", "stopped")
            await redis.expire(HEALTH_KEY, 60)
        except Exception as e:
            logger.debug("关闭时更新健康状态失败: %s", e)

        # Close singletons
        await self._close_singletons()

        uptime = time.monotonic() - self._start_time
        logger.info(
            "新闻消费者已关闭: uptime=%.0fs, processed=%d, "
            "failed=%d, timed_out=%d, dead_lettered=%d",
            uptime, self._processed_count,
            self._failed_count, self._timed_out_count,
            self._dead_lettered_count,
        )

    async def _requeue_in_flight(self) -> None:
        """Re-queue any items left in the in-flight HASH to the retry ZSET."""
        try:
            from app.db.redis import get_redis
            redis = await get_redis()
            items = await redis.hgetall(IN_FLIGHT_KEY)
            if not items:
                return
            now = time.time()
            pipe = redis.pipeline()
            for _key, raw in items.items():
                pipe.zadd(RETRY_KEY, {raw: now})  # Immediate retry on next start
            pipe.delete(IN_FLIGHT_KEY)
            await pipe.execute()
            logger.info("关闭时重新入队 %d 个in-flight任务到重试队列", len(items))
        except Exception:
            logger.exception("重新入队in-flight任务失败")

    async def _close_singletons(self) -> None:
        """Close async singletons gracefully."""
        # LLM gateway
        try:
            from app.core.llm import get_llm_gateway
            await get_llm_gateway().close()
        except Exception as e:
            logger.debug("关闭LLM gateway: %s", e)

        # DataServiceClient
        try:
            from app.services.data_service_client import close_data_service_client
            await close_data_service_client()
        except Exception as e:
            logger.debug("关闭DataServiceClient: %s", e)

        # Redis
        try:
            from app.db.redis import close_redis
            await close_redis()
        except Exception as e:
            logger.debug("关闭Redis: %s", e)

        # Database engine
        try:
            from app.db.database import engine
            await engine.dispose()
        except Exception as e:
            logger.debug("关闭数据库引擎: %s", e)

    # ------------------------------------------------------------------
    # In-flight recovery (on startup)
    # ------------------------------------------------------------------

    async def _recover_in_flight(self) -> None:
        """Recover items from the in-flight HASH left by a previous crash."""
        from app.db.redis import get_redis

        try:
            redis = await get_redis()
            items = await redis.hgetall(IN_FLIGHT_KEY)
            if not items:
                return

            now = time.time()
            pipe = redis.pipeline()
            for _key, raw in items.items():
                # Push to retry ZSET with score=now for immediate processing
                pipe.zadd(RETRY_KEY, {raw: now})
            pipe.delete(IN_FLIGHT_KEY)
            await pipe.execute()
            logger.info("恢复 %d 个上次崩溃的in-flight任务", len(items))
        except Exception:
            logger.exception("恢复in-flight任务失败")

    # ------------------------------------------------------------------
    # Memory check
    # ------------------------------------------------------------------

    @staticmethod
    def _check_rss_memory() -> int:
        """Return current RSS memory in MB.

        Reads /proc/self/statm for current resident pages (Linux).
        Falls back to ru_maxrss (peak RSS) if /proc is unavailable.
        """
        try:
            with open("/proc/self/statm") as f:
                # Field [1] = resident pages
                pages = int(f.read().split()[1])
            return pages * resource.getpagesize() // (1024 * 1024)
        except (OSError, ValueError, IndexError):
            # Fallback: peak RSS (note: high-water mark, not current)
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024


# ======================================================================
# Entry point
# ======================================================================

def main() -> None:
    os.environ.setdefault("LOG_TAG", "news")

    # Ensure backend/ is on sys.path for local dev
    # (Docker sets PYTHONPATH=/app/backend:/app, this handles dev cases)
    backend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend")
    if os.path.isdir(backend_dir) and backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from worker.log_config import setup_logging
    setup_logging()

    concurrency = int(os.getenv("NEWS_CONSUMER_CONCURRENCY", "10"))
    consumer = NewsConsumer(concurrency=concurrency)
    asyncio.run(consumer.start())


if __name__ == "__main__":
    main()
