"""data-processor FastAPI application.

This microservice merges qlib-service (quantitative analysis) with
ML prediction capabilities:

Inherited from qlib-service:
- Expression engine (dynamic quantitative calculator for LLM agents)
- Alpha158/360 factor computation
- Factor analysis (IC, cross-sectional ranking, industry neutralization)
- Backtesting (TopK/Dropout, signal-based, long-short strategies)
- Technical indicator computation
- EOD data sync (via data-service internal API)

New capabilities (phased rollout):
- Phase 2: Settings cache, LLM proxy, APScheduler infrastructure
- Phase 3: ML predictions (LightGBM training, inference, universes)
- Phase 4: RD-Agent automated factor research

Architecture:
- Single uvicorn worker (Qlib global state not safe for multi-process)
- ThreadPoolExecutor(1) for quick queries (<15s)
- ProcessPoolExecutor(1) for long tasks (backtests up to 30min)
- asyncpg pool for settings cache (bypasses SQLAlchemy)
- Redis DB 3 for factor caching, backtest progress, sync status
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.config import get_settings

settings = get_settings()

# ---------------------------------------------------------------------------
# Logging -- configured before any other module import to ensure all
# loggers inherit the correct format and request ID injection.
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [dproc] %(levelname).1s [%(request_id)s] %(message)s"
LOG_DATEFMT = "%H:%M:%S"

_root = logging.getLogger()
_root.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
for _h in _root.handlers[:]:
    _root.removeHandler(_h)

_handler = logging.StreamHandler(sys.stdout)

from app.core.request_id import RequestIdFilter

_handler.addFilter(RequestIdFilter())
_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
_root.addHandler(_handler)

# Suppress noisy third-party loggers
for _name in ("httpx", "httpcore", "urllib3", "asyncio", "watchfiles", "apscheduler"):
    logging.getLogger(_name).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: init Qlib, settings cache, scheduler."""
    logger.info("Starting data-processor...")

    # --- 1. Qlib initialization (inherited from qlib-service) ---
    data_dir = settings.QLIB_DATA_DIR
    if not os.path.exists(data_dir):
        logger.warning(
            "Qlib data directory does not exist: %s (will be created on first sync)",
            data_dir,
        )
        os.makedirs(data_dir, exist_ok=True)

    try:
        from app.context import QlibContext

        market_data_dir = os.path.join(
            data_dir,
            QlibContext.MARKET_TO_DATA_DIR.get(settings.DEFAULT_MARKET, "us_data"),
        )
        if os.path.exists(market_data_dir) and os.listdir(market_data_dir):
            QlibContext.ensure_init(settings.DEFAULT_MARKET, data_dir)
            logger.info(
                "Qlib initialized with default market: %s", settings.DEFAULT_MARKET
            )
        else:
            logger.warning(
                "No data for default market '%s' yet. Run data sync first.",
                settings.DEFAULT_MARKET,
            )
    except Exception as e:
        logger.warning("Qlib initialization deferred: %s", e)

    # --- 2. Settings cache (asyncpg pool → system_settings) ---
    from app.core.settings_cache import settings_cache

    try:
        await settings_cache.init()
    except Exception as e:
        logger.error("SettingsCache init failed (non-fatal): %s", e)

    # --- 3. Prediction data directory ---
    os.makedirs(settings.PREDICTION_DATA_DIR, exist_ok=True)

    # --- 4. APScheduler (prediction + fundamental collection jobs) ---
    scheduler = None
    _retrain_redis_client = None
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from app.core.settings_cache import settings_cache as _sc

        scheduler = AsyncIOScheduler()

        # Helper: check if prediction is enabled before running
        async def _guarded_prediction(market: str, force_retrain: bool = False) -> None:
            """Only run prediction if enabled in settings.

            Checks for performance decay flag (set by backfill_returns)
            and forces retrain if the model's rolling IC has decayed.
            Supports multi-horizon training when PREDICTION_HORIZONS has >1 value.

            Direction model now runs as part of the prediction pipeline
            (inside _run_prediction_async / _run_multi_horizon_async), so
            there is no need to call it separately here.
            """
            try:
                config = await _sc.get_config()
                if not config.llm.enabled:
                    logger.debug("Prediction disabled, skipping %s", market)
                    return
                from app.services.prediction_service import (
                    prediction_service, _get_prediction_horizons,
                )
                ranking_decay = await prediction_service.check_retrain_needed(market)
                direction_decay = await prediction_service.check_direction_retrain_needed(market)
                force = force_retrain or ranking_decay or direction_decay
                if force:
                    logger.info(
                        "Forcing retrain for %s (explicit=%s, ranking_decay=%s, direction_decay=%s)",
                        market, force_retrain, ranking_decay, direction_decay,
                    )
                horizons = _get_prediction_horizons()
                if len(horizons) > 1:
                    task_id = await prediction_service.run_multi_horizon(
                        market, force_retrain=force,
                    )
                else:
                    task_id = await prediction_service.run_prediction(
                        market, force_retrain=force, forward_days=horizons[0],
                    )

                # Await background task to completion so scheduler knows
                # when the full pipeline (ranking + direction) has finished.
                task_obj = prediction_service._tasks.get(task_id)
                if task_obj and task_obj._asyncio_task:
                    await task_obj._asyncio_task

            except Exception as e:
                logger.error("Scheduled prediction failed for %s: %s", market, e, exc_info=True)

        async def _guarded_fundamentals(market: str) -> None:
            """Run fundamental collection (independent of prediction_enabled).

            Fundamental data is used by multiple consumers (financials API,
            analysis agents, chat skills) so it always runs on schedule.
            """
            try:
                from app.services.fundamental_service import fundamental_service
                await fundamental_service.collect_market(market)
            except Exception as e:
                logger.error("Scheduled fundamental collection failed for %s: %s", market, e, exc_info=True)

        async def _backfill_returns() -> None:
            """Daily return backfill."""
            try:
                from app.services.prediction_service import prediction_service
                await prediction_service.backfill_returns()
            except Exception as e:
                logger.error("Scheduled backfill_returns failed: %s", e, exc_info=True)

        # Qlib data sync (after data-service daily bar collection)
        # data-service collects bars at CN 08:00, HK 09:00, US 22:00, Metal 22:30.
        # Sync runs 15 min later to ensure collection has finished.
        async def _sync_qlib(market: str) -> None:
            try:
                from app.services.data_sync import DataSyncService
                svc = DataSyncService()
                result = await svc.sync_market(market, update_only=True)
                logger.info(
                    "Scheduled Qlib sync for %s: %d symbols in %.1fs",
                    market,
                    result.get("symbol_count", 0),
                    result.get("duration_s", 0),
                )
            except Exception as e:
                logger.error("Scheduled Qlib sync failed for %s: %s", market, e, exc_info=True)

        # Common reliability parameters for all cron jobs:
        # misfire_grace_time=600: allow up to 10-min scheduling delay (container restart, etc.)
        # coalesce=True: if multiple triggers accumulated, fire only once
        # max_instances=1: prevent duplicate concurrent executions
        # replace_existing=True: safe for re-registration on warm restarts
        _JOB_KWARGS = dict(
            misfire_grace_time=600,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )

        scheduler.add_job(_sync_qlib, 'cron', args=['cn'], hour=8, minute=15, id='sync_qlib_cn', **_JOB_KWARGS)
        scheduler.add_job(_sync_qlib, 'cron', args=['hk'], hour=9, minute=15, id='sync_qlib_hk', **_JOB_KWARGS)
        scheduler.add_job(_sync_qlib, 'cron', args=['us'], hour=22, minute=15, id='sync_qlib_us', **_JOB_KWARGS)
        scheduler.add_job(_sync_qlib, 'cron', args=['metal'], hour=22, minute=45, id='sync_qlib_metal', **_JOB_KWARGS)

        # Fundamental collection (after market close)
        scheduler.add_job(_guarded_fundamentals, 'cron', args=['cn'], hour=8, minute=30, id='collect_fundamentals_cn', **_JOB_KWARGS)
        scheduler.add_job(_guarded_fundamentals, 'cron', args=['hk'], hour=9, minute=30, id='collect_fundamentals_hk', **_JOB_KWARGS)
        scheduler.add_job(_guarded_fundamentals, 'cron', args=['us'], hour=22, minute=30, id='collect_fundamentals_us', **_JOB_KWARGS)

        # Prediction runs (after fundamentals complete)
        scheduler.add_job(_guarded_prediction, 'cron', args=['cn'], hour=9, minute=30, id='predict_cn', **_JOB_KWARGS)
        scheduler.add_job(_guarded_prediction, 'cron', args=['hk'], hour=10, minute=30, id='predict_hk', **_JOB_KWARGS)
        scheduler.add_job(_guarded_prediction, 'cron', args=['us'], hour=23, minute=30, id='predict_us', **_JOB_KWARGS)

        # Daily return backfill
        scheduler.add_job(_backfill_returns, 'cron', hour=0, minute=0, id='backfill_returns', **_JOB_KWARGS)

        # Market signals: analyst snapshots + options flow + earnings events
        # Runs after US/HK market close, along with fundamental collection.
        async def _collect_market_signals(market: str) -> None:
            """Collect analyst snapshots, options flow, and earnings events."""
            try:
                from app.services.fundamental_service import fundamental_service as _fs
                from app.services.analyst_service import analyst_service as _as
                from app.services.options_service import options_service as _os
                from app.services.earnings_service import earnings_service as _es

                symbols = await _fs._resolve_symbols(market)
                if not symbols:
                    logger.warning("No symbols for market signal collection: %s", market)
                    return

                results = await asyncio.gather(
                    _es.collect_earnings_events(market, symbols),
                    _as.collect_analyst_snapshots(market, symbols),
                    _os.collect_options_flow(market, symbols),
                    return_exceptions=True,
                )
                failed = [r for r in results if isinstance(r, Exception)]
                if failed:
                    logger.warning(
                        "Market signal collection for %s: %d/%d sources failed: %s",
                        market, len(failed), len(results), failed,
                    )
                else:
                    logger.info("Market signal collection done for %s", market)
            except Exception as e:
                logger.error(
                    "Market signal collection failed for %s: %s", market, e, exc_info=True,
                )

        # US signals: 23:45 UTC (after US fundamental collection at 22:30)
        # HK signals: 10:00 UTC (after HK fundamental collection at 09:30)
        scheduler.add_job(
            _collect_market_signals, 'cron', args=['us'],
            hour=23, minute=45, id='collect_signals_us', **_JOB_KWARGS,
        )
        scheduler.add_job(
            _collect_market_signals, 'cron', args=['hk'],
            hour=10, minute=0, id='collect_signals_hk', **_JOB_KWARGS,
        )

        # Sector data collection (weekly, Sunday 02:00 UTC)
        # US/HK sectors are piggybacked during daily fundamental collection.
        # CN sectors need explicit collection via data-service API.
        async def _collect_sectors(market: str) -> None:
            try:
                from app.services.fundamental_service import fundamental_service
                result = await fundamental_service.collect_sector_data(market)
                logger.info(
                    "Sector collection for %s: wrote=%d, skipped=%d",
                    market, result.get("success", 0), result.get("skipped", 0),
                )
            except Exception as e:
                logger.error("Sector collection failed for %s: %s", market, e, exc_info=True)

        scheduler.add_job(
            _collect_sectors, 'cron', args=['cn'],
            day_of_week='sun', hour=2, minute=0, id='collect_sectors_cn', **_JOB_KWARGS,
        )
        scheduler.add_job(
            _collect_sectors, 'cron', args=['us'],
            day_of_week='sun', hour=2, minute=30, id='collect_sectors_us', **_JOB_KWARGS,
        )
        scheduler.add_job(
            _collect_sectors, 'cron', args=['hk'],
            day_of_week='sun', hour=3, minute=0, id='collect_sectors_hk', **_JOB_KWARGS,
        )

        # Model file + RD-Agent workdir cleanup (daily 1:00 UTC)
        async def _cleanup_models() -> None:
            try:
                from app.services.prediction_service import prediction_service
                await prediction_service.cleanup_old_models()
            except Exception as e:
                logger.error("Model cleanup failed: %s", e, exc_info=True)
            try:
                from app.services.rdagent_runner import RDAgentRunner
                RDAgentRunner.cleanup_old_workdirs(max_age_days=7)
            except Exception as e:
                logger.error("RD-Agent workdir cleanup failed: %s", e, exc_info=True)

        scheduler.add_job(_cleanup_models, 'cron', hour=1, minute=0, id='cleanup_models', **_JOB_KWARGS)

        # Auto-retrain: periodic forced retraining based on admin-configured interval.
        # Runs daily at 01:30 UTC and checks each market against its last retrain date.
        # Uses _guarded_prediction to ensure direction model also runs after ranking.

        async def _get_retrain_redis():
            """Lazy singleton Redis client (decode_responses=True) for auto-retrain keys.

            Uses REDIS_URL from settings (DB 3 for data-processor).
            The client is closed during app shutdown via the nonlocal reference.
            """
            nonlocal _retrain_redis_client
            if _retrain_redis_client is None:
                import redis.asyncio as aioredis
                _settings = get_settings()
                _retrain_redis_client = aioredis.from_url(
                    _settings.REDIS_URL, decode_responses=True,
                )
            return _retrain_redis_client

        async def _check_auto_retrain() -> None:
            """Check if auto-retrain is due for any market and trigger if so.

            Guards: prediction must be enabled AND auto_retrain must be enabled.
            Uses Redis SET NX lock to prevent duplicate retrains on container restart.
            Calls _guarded_prediction which runs both ranking + direction models.
            """
            from datetime import date as date_type

            try:
                config = await _sc.get_config()
                if not config.llm.enabled:
                    return
                at = config.auto_tune
                if not at.auto_retrain_enabled:
                    return

                interval = at.auto_retrain_interval_days
                r = await _get_retrain_redis()
                today_str = date_type.today().isoformat()

                for market in ("cn", "us", "hk"):
                    last_key = f"auto_retrain:last:{market}"
                    last_run = await r.get(last_key)
                    if last_run:
                        last_date = date_type.fromisoformat(last_run)
                        if (date_type.today() - last_date).days < interval:
                            continue

                    # Acquire lock to prevent duplicate retrains (2-hour TTL)
                    lock_key = f"auto_retrain:lock:{market}"
                    acquired = await r.set(lock_key, "1", nx=True, ex=7200)
                    if not acquired:
                        logger.info(
                            "Auto-retrain already in progress for %s, skipping",
                            market,
                        )
                        continue

                    logger.info(
                        "Auto-retrain triggered for %s (interval=%d days)",
                        market, interval,
                    )
                    try:
                        # _guarded_prediction handles ranking + direction models
                        await _guarded_prediction(market, force_retrain=True)
                        await r.set(
                            last_key, today_str,
                            ex=86400 * 90,
                        )
                    except Exception as e:
                        logger.error(
                            "Auto-retrain failed for %s: %s",
                            market, e, exc_info=True,
                        )
                    finally:
                        await r.delete(lock_key)
            except Exception as e:
                logger.error(
                    "Auto-retrain check failed: %s", e, exc_info=True,
                )

        scheduler.add_job(
            _check_auto_retrain, 'cron', hour=1, minute=30,
            id='check_auto_retrain', **_JOB_KWARGS,
        )

        scheduler.start()
        logger.info("APScheduler started with %d jobs", len(scheduler.get_jobs()))
    except Exception as e:
        logger.warning("APScheduler start failed (non-fatal): %s", e)

    logger.info("data-processor started successfully")

    yield

    # --- Shutdown ---
    logger.info("Shutting down data-processor...")

    # Stop scheduler
    if scheduler:
        try:
            scheduler.shutdown(wait=False)
            logger.info("APScheduler shut down")
        except Exception as e:
            logger.warning("APScheduler shutdown error: %s", e)

    # Close auto-retrain Redis client
    if _retrain_redis_client is not None:
        try:
            await _retrain_redis_client.aclose()
            logger.info("Auto-retrain Redis client closed")
        except Exception as e:
            logger.warning("Auto-retrain Redis close error: %s", e)

    # Close settings cache (asyncpg pool)
    try:
        await settings_cache.close()
    except Exception as e:
        logger.warning("SettingsCache close error: %s", e)

    # Close LLM proxy httpx client
    try:
        from app.api.llm_proxy import close_llm_client

        await close_llm_client()
    except Exception as e:
        logger.warning("LLM client close error: %s", e)

    # Close prediction service
    try:
        from app.services.prediction_service import prediction_service as _ps
        _ps.shutdown()
    except Exception as e:
        logger.warning("PredictionService shutdown error: %s", e)

    # Kill any running RD-Agent subprocesses
    try:
        from app.services.rdagent_runner import rdagent_runner as _rdr
        _rdr.shutdown()
    except Exception as e:
        logger.warning("RDAgentRunner shutdown error: %s", e)

    # Cancel running ML tools training tasks
    try:
        from app.services.ml_tools_service import ml_tools_service as _mts
        await _mts.shutdown()
    except Exception as e:
        logger.warning("MLToolsService shutdown error: %s", e)

    # Shutdown Qlib executors
    from app.executor import shutdown_executors
    from app.api.indicators import shutdown_indicator_executor

    shutdown_indicator_executor()
    shutdown_executors()

    logger.info("data-processor shut down")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="WebStock Data Processor",
    version="1.0.0",
    description=(
        "Quantitative data processing and ML prediction microservice. "
        "Combines Microsoft Qlib factor analysis with LightGBM-based "
        "stock prediction and RD-Agent automated factor research."
    ),
    lifespan=lifespan,
)

# Middleware -- order matters: outermost middleware runs first.
# RequestIdMiddleware must wrap InternalAuthMiddleware so that
# request IDs are available in auth failure logs.
from app.core.auth import InternalAuthMiddleware
from app.core.request_id import RequestIdMiddleware

app.add_middleware(InternalAuthMiddleware)
app.add_middleware(RequestIdMiddleware)

# ---------------------------------------------------------------------------
# Register routers
# ---------------------------------------------------------------------------

# Health checks (no auth, no prefix)
from app.api.health import router as health_router

app.include_router(health_router)

# Inherited qlib-service routers
from app.api.factors import router as factors_router
from app.api.data import router as data_router
from app.api.expression import router as expression_router
from app.api.backtests import router as backtests_router
from app.api.indicators import router as indicators_router

app.include_router(factors_router)
app.include_router(data_router)
app.include_router(expression_router)
app.include_router(backtests_router)
app.include_router(indicators_router)

# New data-processor routers
from app.api.llm_proxy import router as llm_proxy_router
from app.api.predictions import router as predictions_router
from app.api.rdagent import router as rdagent_router

app.include_router(llm_proxy_router)
app.include_router(predictions_router)
app.include_router(rdagent_router)

# ML Tools API (decomposed backtest steps for ML Agent orchestration)
from app.api.ml_tools import router as ml_tools_router

app.include_router(ml_tools_router)
