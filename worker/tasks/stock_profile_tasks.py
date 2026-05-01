"""Celery tasks for building and maintaining the stock profile knowledge base.

Two tasks with tiered scheduling:
- ``sync_concept_boards``: Daily at 6 AM UTC — diff-based A-share concept sync
- ``build_stock_knowledge_base``: Weekly Sunday 6 AM UTC — full market rebuild

Stock profiles are embedded and stored in ``document_embeddings`` with
``source_type="stock_profile"`` and ``source_id=<symbol>``.
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Set

from worker.celery_app import celery_app
from worker.task_helpers import run_async_task

logger = logging.getLogger(__name__)

# Redis key for caching the previous concept board mapping
CONCEPT_CACHE_KEY = "stock_profile:cn_concept_mapping"
EMBED_BATCH_SIZE = 50

# Redis locks to prevent concurrent execution
_BUILD_LOCK_KEY = "stock_profile:build_lock"
_SYNC_LOCK_KEY = "stock_profile:sync_lock"

# Redis progress key for admin dashboard
_PROGRESS_KEY = "kb:stock_profile:progress"
_PROGRESS_TTL = 600  # 10 minutes — auto-expires if task crashes


def _acquire_redis_lock(lock_key: str, ttl: int) -> bool:
    """Try to acquire a Redis lock (sync, for use inside Celery tasks)."""
    import redis as redis_lib

    from app.config import settings

    try:
        r = redis_lib.from_url(str(settings.REDIS_URL), decode_responses=True)
        return bool(r.set(lock_key, "1", nx=True, ex=ttl))
    except Exception as e:
        logger.warning("[StockProfileTask] Redis lock check failed: %s", e)
        return True  # fail-open: proceed if Redis is down


def _release_redis_lock(lock_key: str):
    """Release a Redis lock (sync)."""
    import redis as redis_lib

    from app.config import settings

    try:
        r = redis_lib.from_url(str(settings.REDIS_URL), decode_responses=True)
        r.delete(lock_key)
    except Exception:
        pass


def _clear_progress_sync():
    """Clear the progress key from Redis (sync, called in task finally)."""
    import redis as redis_lib

    from app.config import settings

    try:
        r = redis_lib.from_url(str(settings.REDIS_URL), decode_responses=True)
        r.delete(_PROGRESS_KEY)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Per-market Redis keys (matching daily_bar_tasks pattern)
# ---------------------------------------------------------------------------
_MARKET_LOCK_KEY_TEMPLATE = "kb:stock_profile:{market}:lock"
_MARKET_LOCK_TTL = 14400  # 4 hours — matches task time_limit
_MARKET_PROGRESS_KEY_TEMPLATE = "kb:stock_profile:{market}:progress"
_MARKET_PROGRESS_TTL = 3600  # 1 hour — auto-expires if task crashes
_MARKET_QUEUED_KEY_TEMPLATE = "kb:stock_profile:{market}:queued"
_MARKET_QUEUED_TTL = 14400

# CAS lock release: only delete if current value matches owner
_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

# Module-level sync Redis connection — reused across calls within one task
_redis_conn = None


def _get_sync_redis():
    """Get or create a module-level sync Redis connection."""
    global _redis_conn
    if _redis_conn is None:
        import redis as redis_lib
        from app.config import settings
        _redis_conn = redis_lib.from_url(str(settings.REDIS_URL), decode_responses=True)
    return _redis_conn


def _acquire_market_lock_sync(market: str, task_id: str = None) -> Optional[str]:
    """Acquire per-market lock. Returns owner token (task_id) or None if already locked."""
    try:
        r = _get_sync_redis()
        owner = task_id or str(uuid.uuid4())
        acquired = r.set(
            _MARKET_LOCK_KEY_TEMPLATE.format(market=market),
            owner,
            nx=True,
            ex=_MARKET_LOCK_TTL,
        )
        if acquired:
            # Clear queued flag on lock acquisition
            r.delete(_MARKET_QUEUED_KEY_TEMPLATE.format(market=market))
            return owner
        return None
    except Exception as e:
        logger.warning("[StockProfileTask] Lock acquire failed for %s: %s", market, e)
        return task_id or "fallback"  # fail-open


def _release_market_lock_sync(market: str, owner: str):
    """CAS lock release — only delete if current value matches owner."""
    try:
        r = _get_sync_redis()
        r.eval(
            _RELEASE_LOCK_LUA,
            1,
            _MARKET_LOCK_KEY_TEMPLATE.format(market=market),
            owner,
        )
    except Exception as e:
        logger.warning("[StockProfileTask] Failed to release lock for %s (owner=%s): %s", market, owner, e)


def _update_market_progress_sync(market: str, phase: str, current: int, total: int):
    """Write per-market progress to Redis."""
    try:
        from datetime import datetime, timezone
        r = _get_sync_redis()
        pct = int(current * 100 / total) if total > 0 else 0
        progress = {
            "phase": phase,
            "current": current,
            "total": total,
            "percent": pct,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        key = _MARKET_PROGRESS_KEY_TEMPLATE.format(market=market)
        r.set(key, json.dumps(progress), ex=_MARKET_PROGRESS_TTL)
    except Exception:
        pass


def _clear_market_progress_sync(market: str):
    """Clear per-market progress key."""
    try:
        r = _get_sync_redis()
        r.delete(_MARKET_PROGRESS_KEY_TEMPLATE.format(market=market))
    except Exception:
        pass


@celery_app.task(
    bind=True,
    max_retries=1,
    time_limit=14400,      # 4 hours hard limit (CN+US+HK collection is slow)
    soft_time_limit=13800, # 3h50m soft limit
)
def build_stock_knowledge_base(self):
    """Full rebuild of stock profile knowledge base across all markets.

    Pipeline architecture — each market embeds as soon as collection finishes:
    1. CN (akshare) pipeline runs parallel with yfinance pipeline
    2. yfinance pipeline: US collect → US embed (background) → HK collect → HK embed
    3. Each batch (50 profiles) is embedded and stored idempotently
    4. Redis concept mapping cache updated at the end
    """
    if not _acquire_redis_lock(_BUILD_LOCK_KEY, ttl=14400):
        logger.info("[StockProfileTask] Build already in progress, skipping")
        return {"skipped": True, "reason": "already_running"}

    logger.info("知识库：开始全量构建")
    try:
        result = run_async_task(_build_kb_async)
        logger.info("知识库：全量构建完成 %s", result)
        return result
    except Exception as e:
        logger.exception("[StockProfileTask] Build failed: %s", e)
        raise self.retry(exc=e, countdown=300)
    finally:
        _release_redis_lock(_BUILD_LOCK_KEY)
        _clear_progress_sync()


@celery_app.task(
    bind=True,
    max_retries=1,
    time_limit=3600,       # 1 hour hard limit
    soft_time_limit=3300,  # 55 min soft limit
)
def sync_concept_boards(self):
    """Daily incremental sync of A-share concept board mappings.

    Steps:
    1. Fetch current concept board → stock mapping
    2. Compare with cached mapping from Redis
    3. Re-embed only stocks whose concepts changed + newly listed stocks
    4. Update Redis cache
    """
    if not _acquire_redis_lock(_SYNC_LOCK_KEY, ttl=3600):
        logger.info("[StockProfileTask] Concept sync already in progress, skipping")
        return {"skipped": True, "reason": "already_running"}

    logger.info("知识库：开始概念同步")
    try:
        result = run_async_task(_sync_concepts_async)
        logger.info("知识库：概念同步完成 %s", result)
        return result
    except Exception as e:
        logger.exception("[StockProfileTask] Concept sync failed: %s", e)
        raise self.retry(exc=e, countdown=300)
    finally:
        _release_redis_lock(_SYNC_LOCK_KEY)


@celery_app.task(
    bind=True,
    name="worker.tasks.stock_profile_tasks.collect_market_profiles",
    max_retries=1,
    time_limit=14400,
    soft_time_limit=13800,
)
def collect_market_profiles(self, market: str):
    """Collect + embed stock profiles for a single market.

    Per-market locking, progress tracking, and counter rebuild.
    Used by the admin UI for individual market control.
    """
    if market not in ("cn", "us", "hk"):
        logger.error("[StockProfileTask] Invalid market: %s", market)
        return {"error": f"Invalid market: {market}"}

    # Check if the global build task is running (prevents concurrent writes)
    try:
        r = _get_sync_redis()
        if r.exists(_BUILD_LOCK_KEY):
            logger.warning(
                "[StockProfileTask] Global build lock held, skipping per-market collect for %s",
                market,
            )
            return {"skipped": True, "reason": "global_build_running"}
    except Exception:
        pass  # fail-open

    owner = _acquire_market_lock_sync(market, task_id=self.request.id)
    if owner is None:
        # Log holder info for debugging stale locks
        try:
            r = _get_sync_redis()
            holder = r.get(_MARKET_LOCK_KEY_TEMPLATE.format(market=market))
            ttl = r.ttl(_MARKET_LOCK_KEY_TEMPLATE.format(market=market))
            logger.warning(
                "[StockProfileTask] market=%s already locked (holder=%s, ttl=%ds), skipping",
                market, holder, ttl,
            )
        except Exception:
            logger.warning("[StockProfileTask] market=%s already locked, skipping", market)
        return {"skipped": True, "reason": "already_running"}

    logger.info("知识库：开始采集%s市场档案", market)
    try:
        result = run_async_task(lambda: _collect_market_async(market))
        logger.info("知识库：%s市场完成 %s", market, result)
        return result
    except Exception as e:
        logger.exception("[StockProfileTask] market=%s failed: %s", market, e)
        raise self.retry(exc=e, countdown=300)
    finally:
        _clear_market_progress_sync(market)
        _release_market_lock_sync(market, owner)


# ---------------------------------------------------------------------------
# Async implementations
# ---------------------------------------------------------------------------

async def _rebuild_embedding_counter_async(source_type: str) -> None:
    """Query per-source_type stats from DB and write to Redis counter.

    Called inside the task's async context after embedding completes.
    The stats endpoint reads this counter instead of running GROUP BY queries.
    """
    try:
        from sqlalchemy import text as sa_text

        from app.api.v1.admin.knowledge_base import COUNTER_KEY_EMBEDDING
        from app.db.redis import get_redis
        from app.db.task_session import get_task_session

        async with get_task_session() as db:
            row = await db.execute(sa_text(
                "SELECT COUNT(*) as count, MAX(created_at) as last_updated "
                "FROM document_embeddings WHERE source_type = :st"
            ), {"st": source_type})
            r = row.one()
            model_row = await db.execute(sa_text(
                "SELECT model FROM document_embeddings "
                "WHERE source_type = :st ORDER BY created_at DESC LIMIT 1"
            ), {"st": source_type})
            model_result = model_row.first()

        counter = {
            "count": r.count,
            "lastUpdated": r.last_updated.isoformat() if r.last_updated else None,
            "model": model_result.model if model_result else None,
        }

        redis = await get_redis()
        await redis.set(
            COUNTER_KEY_EMBEDDING.format(source_type=source_type),
            json.dumps(counter),
        )
        logger.debug(
            "[StockProfileTask] Rebuilt %s embedding counter: %d embeddings",
            source_type, r.count,
        )
    except Exception as e:
        logger.warning(
            "[StockProfileTask] Failed to rebuild %s counter: %s",
            source_type, e,
        )


async def _update_kb_progress(
    phase: str, current: int, total: int, stats: Optional[Dict] = None,
) -> None:
    """Write stock profile build progress to Redis for admin dashboard."""
    try:
        from datetime import datetime, timezone
        from app.db.redis import get_redis

        redis = await get_redis()
        pct = int(current * 100 / total) if total > 0 else 0
        progress = {
            "phase": phase,
            "current": current,
            "total": total,
            "percent": pct,
            "stats": stats or {},
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        await redis.set(_PROGRESS_KEY, json.dumps(progress), ex=_PROGRESS_TTL)
    except Exception:
        pass  # Non-critical


async def _build_kb_async() -> Dict[str, Any]:
    """Full knowledge base rebuild (all markets).

    Pipeline architecture: each market starts embedding as soon as its
    collection finishes, overlapping embedding with remaining collection.
    CN (akshare) runs in parallel with the yfinance group (US→HK serial).
    """
    import time as _time

    from app.db.task_session import get_task_session
    from app.services.rag import get_index_service
    from app.services.rag.embedding import get_embedding_config_from_db
    from app.services.stock_profile_service import get_stock_profile_service

    svc = get_stock_profile_service()
    stats: Dict[str, Any] = {
        "cn": 0, "us": 0, "hk": 0,
        "embedded": 0, "errors": 0,
    }
    build_t0 = _time.monotonic()

    await _update_kb_progress("collecting", 0, 1, stats)

    # Fetch embedding config upfront (shared by all markets)
    async with get_task_session() as db:
        embed_config = await get_embedding_config_from_db(db)
    index_service = get_index_service()

    # ── CN pipeline: collect → embed → save concept cache ──
    async def _cn_pipeline() -> tuple:
        t0 = _time.monotonic()
        await _update_kb_progress("collecting_cn", 0, 1, stats)
        profiles = await svc.collect_cn_profiles()
        stats["cn"] = len(profiles)
        collect_s = _time.monotonic() - t0
        logger.info(
            "知识库：CN采集完成 %d个档案 %.0f秒",
            len(profiles), collect_s,
        )
        if not profiles:
            return 0, 0, []

        async def _cn_progress(embedded_so_far: int):
            await _update_kb_progress(
                "embedding_cn", embedded_so_far, len(profiles), stats,
            )

        emb, err = await _batch_embed_profiles(
            profiles, index_service, embed_config, market_label="CN",
            progress_callback=_cn_progress,
        )
        logger.info(
            "知识库：CN嵌入完成 %d成功 %d失败",
            emb, err,
        )
        return emb, err, profiles

    # ── yfinance pipeline: US collect→embed‖HK collect→embed ──
    async def _yfinance_pipeline() -> tuple:
        total_emb, total_err = 0, 0

        # US collection
        t0 = _time.monotonic()
        await _update_kb_progress("collecting_us", 0, 1, stats)
        us_profiles = await svc.collect_us_profiles()
        stats["us"] = len(us_profiles)
        logger.info(
            "知识库：US采集完成 %d个档案 %.0f秒",
            len(us_profiles), _time.monotonic() - t0,
        )

        async def _us_progress(embedded_so_far: int):
            await _update_kb_progress(
                "embedding_us", embedded_so_far, len(us_profiles), stats,
            )

        # Start US embedding in background while HK collects
        us_embed_task: Optional[asyncio.Task] = None
        if us_profiles:
            us_embed_task = asyncio.create_task(
                _batch_embed_profiles(
                    us_profiles, index_service, embed_config, market_label="US",
                    progress_callback=_us_progress,
                )
            )

        # HK collection (serial after US to share yfinance rate limit)
        t0_hk = _time.monotonic()
        hk_profiles = await svc.collect_hk_profiles()
        stats["hk"] = len(hk_profiles)
        logger.info(
            "知识库：HK采集完成 %d个档案 %.0f秒",
            len(hk_profiles), _time.monotonic() - t0_hk,
        )

        async def _hk_progress(embedded_so_far: int):
            await _update_kb_progress(
                "embedding_hk", embedded_so_far, len(hk_profiles), stats,
            )

        # Embed HK (runs concurrently with US embed if still in progress)
        if hk_profiles:
            hk_emb, hk_err = await _batch_embed_profiles(
                hk_profiles, index_service, embed_config, market_label="HK",
                progress_callback=_hk_progress,
            )
            total_emb += hk_emb
            total_err += hk_err

        # Await US embedding result
        if us_embed_task:
            us_emb, us_err = await us_embed_task
            total_emb += us_emb
            total_err += us_err

        logger.info(
            "知识库：US+HK嵌入完成 %d成功 %d失败",
            total_emb, total_err,
        )
        return total_emb, total_err

    # Run both pipelines concurrently
    cn_task = asyncio.create_task(_cn_pipeline())
    yf_task = asyncio.create_task(_yfinance_pipeline())

    cn_emb, cn_err, cn_profiles = await cn_task
    yf_emb, yf_err = await yf_task

    stats["embedded"] = cn_emb + yf_emb
    stats["errors"] = cn_err + yf_err

    # Update concept mapping cache in Redis
    if cn_profiles:
        cn_mapping: Dict[str, List[str]] = {}
        for p in cn_profiles:
            code = p.symbol.split(".")[0]
            cn_mapping[code] = p.concepts
        await _save_concept_cache(cn_mapping)

    total = stats["cn"] + stats["us"] + stats["hk"]
    logger.info(
        "知识库：全量完成 CN=%d US=%d HK=%d 嵌入=%d 错误=%d %.0f秒",
        stats["cn"], stats["us"], stats["hk"],
        stats["embedded"], stats["errors"],
        _time.monotonic() - build_t0,
    )

    # Rebuild counter BEFORE progress is cleared (in finally block)
    await _rebuild_embedding_counter_async("stock_profile")

    # Rebuild per-market counters for admin dashboard
    try:
        from app.api.v1.admin.knowledge_base import rebuild_stock_profile_market_counters
        from app.db.task_session import get_task_session
        async with get_task_session() as session:
            await rebuild_stock_profile_market_counters(session)
    except Exception as e:
        logger.warning("[StockProfileTask] Failed to rebuild per-market counters: %s", e)

    return stats


async def _collect_market_async(market: str) -> Dict[str, Any]:
    """Collect and embed profiles for a single market."""
    import time as _time

    from app.db.task_session import get_task_session
    from app.services.rag import get_index_service
    from app.services.rag.embedding import get_embedding_config_from_db
    from app.services.stock_profile_service import get_stock_profile_service

    svc = get_stock_profile_service()
    stats: Dict[str, Any] = {"collected": 0, "embedded": 0, "errors": 0}
    t0 = _time.monotonic()

    # Phase 1: Collect
    _update_market_progress_sync(market, "collecting", 0, 1)
    logger.info("知识库：开始采集%s市场档案", market)

    if market == "cn":
        profiles = await svc.collect_cn_profiles()
    elif market == "us":
        profiles = await svc.collect_us_profiles()
    else:  # hk
        profiles = await svc.collect_hk_profiles()

    stats["collected"] = len(profiles)
    collect_s = _time.monotonic() - t0
    logger.info(
        "知识库：%s采集完成 %d个档案 %.0f秒",
        market, len(profiles), collect_s,
    )

    if not profiles:
        logger.warning("[StockProfileTask] market=%s: no profiles collected", market)
        return stats

    # Phase 2: Embed
    async with get_task_session() as db:
        embed_config = await get_embedding_config_from_db(db)
    index_service = get_index_service()

    def _progress_sync(embedded_so_far: int):
        _update_market_progress_sync(market, "embedding", embedded_so_far, len(profiles))

    async def _progress(embedded_so_far: int):
        _progress_sync(embedded_so_far)

    emb, err = await _batch_embed_profiles(
        profiles, index_service, embed_config,
        market_label=market.upper(),
        progress_callback=_progress,
    )
    stats["embedded"] = emb
    stats["errors"] = err

    # If CN, update concept cache
    if market == "cn" and profiles:
        cn_mapping: Dict[str, List[str]] = {}
        for p in profiles:
            code = p.symbol.split(".")[0]
            cn_mapping[code] = p.concepts
        await _save_concept_cache(cn_mapping)

    # Rebuild counters
    await _rebuild_embedding_counter_async("stock_profile")
    try:
        from app.api.v1.admin.knowledge_base import rebuild_stock_profile_market_counters
        from app.db.task_session import get_task_session as _get_session
        async with _get_session() as db:
            await rebuild_stock_profile_market_counters(db)
    except Exception as e:
        logger.warning("[StockProfileTask] Failed to rebuild per-market counters: %s", e)

    elapsed = _time.monotonic() - t0
    logger.info(
        "知识库：%s完成 采集%d 嵌入%d 错误%d %.0f秒",
        market, stats["collected"], stats["embedded"], stats["errors"], elapsed,
    )
    return stats


async def _sync_concepts_async() -> Dict[str, Any]:
    """Daily incremental concept board sync.

    TODO: removed in StockPulse migration. The implementation depended on
    ``StockProfileService.collect_cn_concept_mapping``, which in turn called
    the data-service control endpoint ``/v1/reference/cn-concept-mapping``.
    Both have been deleted because control plane responsibilities for CN
    concept-board collection now live in the StockPulse admin UI. This task
    is kept as a no-op so the existing Celery beat schedule does not throw
    ``AttributeError``; once StockPulse exposes a public read endpoint for
    pre-collected concept mappings the sync can be re-implemented on top of
    ``StockPulseClient``.
    """
    stats: Dict[str, Any] = {"changed": 0, "new": 0, "embedded": 0, "errors": 0}
    logger.warning(
        "[StockProfileTask] Concept board sync is a no-op after StockPulse "
        "migration; control plane moved to StockPulse admin UI."
    )
    return stats


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _batch_embed_profiles(
    profiles,
    index_service,
    embed_config,
    market_label: str = "",
    progress_callback=None,
) -> tuple:
    """Batch-embed stock profiles and store in DB.

    Args:
        profiles: List of StockProfile to embed.
        index_service: RAG index service instance.
        embed_config: Embedding model config.
        market_label: Optional label (e.g. "CN", "US") for progress logs.
        progress_callback: Optional async callable(embedded_so_far) for progress reporting.

    Returns:
        Tuple of (embedded_count, error_count).
    """
    from app.db.task_session import get_task_session

    tag = f"[{market_label}] " if market_label else ""
    embedded_count = 0
    error_count = 0

    for i in range(0, len(profiles), EMBED_BATCH_SIZE):
        batch = profiles[i : i + EMBED_BATCH_SIZE]

        # Generate embedding texts — keep profiles and texts paired
        pairs = [(p, p.to_embedding_text()) for p in batch]
        pairs = [(p, t) for p, t in pairs if t.strip()]

        if not pairs:
            continue

        batch_profiles, texts = zip(*pairs)

        try:
            embeddings = await index_service.generate_embeddings_batch(
                list(texts),
                model=embed_config.model,
                api_key=embed_config.api_key,
                base_url=embed_config.base_url,
            )
        except Exception as e:
            logger.error(
                "[StockProfileTask] %sBatch embed failed at offset %d: %s",
                tag, i, e,
            )
            error_count += len(batch_profiles)
            continue

        # Store embeddings — delete old + insert new per stock (idempotent)
        async with get_task_session() as db:
            for profile, text, embedding in zip(batch_profiles, texts, embeddings):
                if embedding is None:
                    error_count += 1
                    continue
                try:
                    await index_service.delete_embeddings(
                        db, "stock_profile", profile.symbol
                    )
                    await index_service.store_embedding(
                        db=db,
                        source_type="stock_profile",
                        source_id=profile.symbol,
                        chunk_text=text,
                        embedding=embedding,
                        symbol=profile.symbol,
                        chunk_index=0,
                        model=embed_config.model,
                    )
                    embedded_count += 1
                except Exception as e:
                    error_count += 1
                    logger.warning(
                        "[StockProfileTask] %sStore error for %s: %s",
                        tag, profile.symbol, e,
                    )
            await db.commit()

        if (i // EMBED_BATCH_SIZE) % 10 == 0 and i > 0:
            logger.debug(
                "[StockProfileTask] %sEmbedded %d/%d profiles so far",
                tag, embedded_count, len(profiles),
            )

        # Report progress after each batch
        if progress_callback:
            try:
                await progress_callback(embedded_count)
            except Exception:
                pass

    return embedded_count, error_count


async def _load_concept_cache() -> Dict[str, List[str]]:
    """Load previous concept mapping from Redis."""
    try:
        from app.db.redis import get_redis

        redis = await get_redis()
        raw = await redis.get(CONCEPT_CACHE_KEY)
        if raw:
            return json.loads(raw)
    except Exception as e:
        logger.warning("[StockProfileTask] Failed to load concept cache: %s", e)
    return {}


async def _save_concept_cache(mapping: Dict[str, List[str]]) -> None:
    """Save concept mapping to Redis with 14-day TTL."""
    try:
        from app.db.redis import get_redis

        redis = await get_redis()
        # 14-day TTL so cache self-heals if weekly rebuild stops running
        await redis.set(
            CONCEPT_CACHE_KEY,
            json.dumps(mapping, ensure_ascii=False),
            ex=86400 * 14,
        )
        logger.debug(
            "[StockProfileTask] Saved concept cache: %d stocks", len(mapping)
        )
    except Exception as e:
        logger.warning("[StockProfileTask] Failed to save concept cache: %s", e)
