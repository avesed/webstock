"""News monitoring Celery tasks."""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, TypeVar

from worker.celery_app import celery_app

# Use Celery-safe database utilities (avoids event loop conflicts)
from worker.db_utils import get_task_session

logger = logging.getLogger(__name__)

# Redis keys for monitor progress tracking
MONITOR_STATUS_KEY = "news:monitor:status"
MONITOR_PROGRESS_KEY = "news:monitor:progress"
MONITOR_LAST_RUN_KEY = "news:monitor:last_run"


async def _update_progress(stage: str, message: str, percent: int = 0):
    """Update monitor progress in Redis for the admin dashboard."""
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        progress = {
            "stage": stage,
            "message": message,
            "percent": percent,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await redis.set(MONITOR_PROGRESS_KEY, json.dumps(progress), ex=600)
        await redis.set(MONITOR_STATUS_KEY, "running", ex=600)
    except Exception:
        pass  # Non-critical, don't break task


async def _finish_progress(stats: Dict[str, Any]):
    """Mark monitor task as complete in Redis."""
    try:
        from app.db.redis import get_redis
        redis = await get_redis()
        now = datetime.now(timezone.utc).isoformat()
        await redis.set(MONITOR_STATUS_KEY, "idle", ex=1800)
        await redis.set(MONITOR_LAST_RUN_KEY, json.dumps({
            "finished_at": now,
            "stats": stats,
        }), ex=1800)
        await redis.delete(MONITOR_PROGRESS_KEY)
    except Exception:
        pass

T = TypeVar("T")


def run_async_task(coro_func: Callable[..., T], *args, **kwargs) -> T:
    """
    Run an async function in a new event loop, properly cleaning up afterwards.

    This helper ensures all singleton async clients are reset after each task
    to avoid "Event loop is closed" errors when tasks reuse singleton clients
    that were bound to different (now closed) event loops.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro_func(*args, **kwargs))
    finally:
        loop.close()
        # Reset all singleton clients that may have bound to this event loop
        try:
            from app.core.llm import reset_llm_gateway
            reset_llm_gateway()
        except Exception as e:
            logger.warning("Failed to reset LLM gateway: %s", e)
        try:
            from app.db.redis import reset_redis
            reset_redis()
        except Exception as e:
            logger.warning("Failed to reset Redis client: %s", e)


# ---------------------------------------------------------------------------
# Layer 1 scoring
# ---------------------------------------------------------------------------
async def _run_layer1_scoring_if_enabled(
    db,
    system_settings,
    articles: List[Dict[str, str]],
) -> tuple[List, bool]:
    """
    Run Layer 1 scoring if LLM pipeline is enabled.

    Args:
        db: Database session
        system_settings: System settings with feature flag
        articles: List of dicts with url, title, text (summary)

    Returns:
        Tuple of (list of Layer1ScoringResult, is_enabled bool)
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


def _build_score_details(scoring_result) -> dict:
    """Build score_details dict from Layer1ScoringResult for DB storage."""
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


@celery_app.task(bind=True, max_retries=3)
def monitor_news(self):
    """
    Periodic task to fetch and store new news articles.

    Runs every 15 minutes to:
    1. Fetch news for all stocks in users' watchlists
    2. Store new articles in database
    3. Check for keyword matches in news alerts
    4. Trigger notifications for matched alerts

    This task is registered with Celery Beat schedule.
    """
    try:
        return run_async_task(_monitor_news_async)
    except Exception as e:
        logger.exception(f"News monitor task failed: {e}")
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


async def _monitor_news_async() -> Dict[str, Any]:
    """
    Async implementation of news monitoring.

    Two operating modes controlled by enable_llm_pipeline:
    - OFF: Articles saved to DB with title+summary only. No scoring, no
      fetching, no analysis, no embedding.
    - ON:  Full 3-layer pipeline -- Layer 1 scoring -> Layer 2 fetch+clean
      -> Layer 3 analyze/embed.

    Two-layer news acquisition strategy:
    - Layer 1: Global market news (Finnhub general + AKShare trending)
    - Layer 2: Watchlist symbol-specific news (per-symbol queries)
    """
    from sqlalchemy import select

    from app.config import settings as app_settings
    from app.models.watchlist import WatchlistItem
    from app.models.news import News, NewsAlert, FilterStatus
    from app.services.news_service import (
        FinnhubProvider,
        AKShareProvider,
        get_news_service,
    )
    from app.services.settings_service import SettingsService

    logger.info("Starting news monitor task")
    await _update_progress("init", "Initializing news monitor...", 0)

    stats = {
        "global_fetched": 0,
        "global_finnhub": 0,
        "global_akshare": 0,
        "watchlist_fetched": 0,
        "articles_stored": 0,
        "alerts_triggered": 0,
        "llm_pipeline_enabled": False,
        # Layer 1 scoring stats (only when pipeline enabled)
        "layer1_discard": 0,
        "layer1_lightweight": 0,
        "layer1_full_analysis": 0,
        "layer1_critical": 0,
    }

    trace_batch = []  # Pipeline trace events to write after commit

    try:
        async with get_task_session() as db:
            # Get Finnhub API key from system settings
            settings_service = SettingsService()
            system_settings = await settings_service.get_system_settings(db)
            finnhub_api_key = system_settings.finnhub_api_key or app_settings.FINNHUB_API_KEY

            # Track seen URLs to avoid duplicates
            seen_urls: set = set()

            # Collect important articles for post-commit AI analysis dispatch
            important_articles: List[tuple] = []  # (News obj, importance score, title preview)
            all_new_articles: List = []  # Track all new articles for dispatch

            # ===== Layer 1: Global Market News (Finnhub + AKShare) =====
            await _update_progress("layer1", "Layer 1: Fetching global market news...", 5)
            enable_pipeline = system_settings.enable_llm_pipeline
            stats["llm_pipeline_enabled"] = enable_pipeline

            try:
                # --- Fetch from both sources ---
                # Source 1: Finnhub news (all categories) -- always use get_general_news
                finnhub_articles = []
                finnhub_categories = ["general", "forex", "crypto", "merger"]
                for cat in finnhub_categories:
                    try:
                        cat_articles = await FinnhubProvider.get_general_news(
                            category=cat,
                            api_key=finnhub_api_key,
                        )
                        finnhub_articles.extend(cat_articles)
                        logger.info(f"Layer 1: Finnhub [{cat}] fetched {len(cat_articles)} articles")
                    except Exception as e:
                        logger.warning(f"Layer 1: Finnhub [{cat}] fetch failed: {e}")
                stats["global_finnhub"] = len(finnhub_articles)

                await _update_progress("layer1_akshare", "Layer 1: Fetching AKShare news...", 10)

                # Source 2: AKShare trending
                akshare_articles = []
                try:
                    akshare_articles = await AKShareProvider.get_trending_news_cn()
                    stats["global_akshare"] = len(akshare_articles)
                    logger.info(f"Layer 1: AKShare fetched {len(akshare_articles)} articles")
                except Exception as e:
                    logger.warning(f"Layer 1: AKShare fetch failed: {e}")

                # Combine all global articles
                global_articles = finnhub_articles + akshare_articles
                stats["global_fetched"] = len(global_articles)

                # Batch dedup: query DB once for all URLs
                candidate_urls = [a.url for a in global_articles if a.url and a.url not in seen_urls]
                existing_urls = set()
                if candidate_urls:
                    dedup_query = select(News.url).where(News.url.in_(candidate_urls))
                    dedup_result = await db.execute(dedup_query)
                    existing_urls = {row[0] for row in dedup_result.fetchall()}

                new_articles = []
                for a in global_articles:
                    if a.url and a.url not in seen_urls and a.url not in existing_urls:
                        new_articles.append(a)
                    if a.url:
                        seen_urls.add(a.url)

                logger.info(
                    f"Layer 1: {len(global_articles)} fetched (Finnhub={stats['global_finnhub']}, "
                    f"AKShare={stats['global_akshare']}), "
                    f"{len(existing_urls)} already in DB, {len(new_articles)} new"
                )

                if enable_pipeline:
                    # Pipeline ON: run Layer 1 scoring on all new global articles
                    articles_for_scoring = [
                        {
                            "url": a.url,
                            "headline": a.title,
                            "summary": a.summary or "",
                        }
                        for a in new_articles
                    ]

                    await _update_progress("layer1_scoring", f"Layer 1: Scoring {len(new_articles)} new articles...", 20)

                    scoring_results = []
                    try:
                        scoring_results, _ = await _run_layer1_scoring_if_enabled(
                            db, system_settings, articles_for_scoring
                        )
                    except Exception as e:
                        logger.warning("Layer 1: Scoring failed, all articles default to lightweight: %s", e)

                    # Build URL -> scoring result map
                    scoring_map = {}
                    for r in scoring_results:
                        scoring_map[r.url] = r

                    # Store articles with scoring data
                    for article in new_articles:
                        scoring = scoring_map.get(article.url)

                        if scoring and scoring.routing_decision == "discard":
                            # Discarded: store with discarded status, do NOT dispatch
                            news = News(
                                symbol=article.symbol,
                                title=article.title[:500] if article.title else "",
                                summary=article.summary,
                                source=article.source,
                                url=article.url,
                                published_at=article.published_at,
                                market=article.market,
                                related_entities=None,
                                has_stock_entities=False,
                                has_macro_entities=False,
                                max_entity_score=None,
                                primary_entity=None,
                                primary_entity_type=None,
                                filter_status=FilterStatus.DISCARDED.value,
                                content_score=scoring.total_score,
                                processing_path="discarded",
                                score_details=_build_score_details(scoring),
                            )
                            db.add(news)
                            stats["articles_stored"] += 1
                            stats["layer1_discard"] += 1
                            if scoring.is_critical:
                                stats["layer1_critical"] += 1

                            trace_batch.append({
                                "news_obj": news,
                                "decision": "discarded",
                                "reason": scoring.reasoning,
                                "score": scoring.total_score,
                            })
                            continue

                        # Not discarded: store with scoring data and dispatch later
                        if scoring:
                            filter_status = FilterStatus.INITIAL_USEFUL.value
                            content_score = scoring.total_score
                            processing_path = scoring.routing_decision  # "lightweight" or "full_analysis"
                            score_details = _build_score_details(scoring)
                            is_critical = scoring.is_critical
                            decision_label = scoring.routing_decision
                            reasoning = scoring.reasoning

                            if scoring.routing_decision == "full_analysis":
                                stats["layer1_full_analysis"] += 1
                            else:
                                stats["layer1_lightweight"] += 1
                            if scoring.is_critical:
                                stats["layer1_critical"] += 1
                        else:
                            # No scoring result (scoring failed) -- default to lightweight
                            filter_status = FilterStatus.INITIAL_USEFUL.value
                            content_score = 0
                            processing_path = "lightweight"
                            score_details = None
                            is_critical = False
                            decision_label = "lightweight"
                            reasoning = "scoring_unavailable"

                        news = News(
                            symbol=article.symbol,
                            title=article.title[:500] if article.title else "",
                            summary=article.summary,
                            source=article.source,
                            url=article.url,
                            published_at=article.published_at,
                            market=article.market,
                            related_entities=None,
                            has_stock_entities=False,
                            has_macro_entities=False,
                            max_entity_score=None,
                            primary_entity=None,
                            primary_entity_type=None,
                            filter_status=filter_status,
                            content_score=content_score,
                            processing_path=processing_path,
                            score_details=score_details,
                        )
                        db.add(news)
                        all_new_articles.append(news)
                        stats["articles_stored"] += 1

                        trace_batch.append({
                            "news_obj": news,
                            "decision": decision_label,
                            "reason": reasoning,
                            "score": content_score,
                        })

                        article_dict = article.to_dict()
                        importance = _score_article_importance(article_dict)
                        if importance >= 2.0:
                            important_articles.append(
                                (news, importance, article.title[:60] if article.title else "")
                            )

                    logger.info(
                        "Layer 1 (pipeline ON): discard=%d, lightweight=%d, "
                        "full_analysis=%d, critical=%d",
                        stats["layer1_discard"],
                        stats["layer1_lightweight"],
                        stats["layer1_full_analysis"],
                        stats["layer1_critical"],
                    )

                else:
                    # Pipeline OFF: store all new articles with basic metadata only
                    for article in new_articles:
                        news = News(
                            symbol=article.symbol,
                            title=article.title[:500] if article.title else "",
                            summary=article.summary,
                            source=article.source,
                            url=article.url,
                            published_at=article.published_at,
                            market=article.market,
                            related_entities=None,
                            has_stock_entities=False,
                            has_macro_entities=False,
                            max_entity_score=None,
                            primary_entity=None,
                            primary_entity_type=None,
                        )
                        db.add(news)
                        stats["articles_stored"] += 1

                    logger.info(
                        "Layer 1 (pipeline OFF): stored=%d articles with basic metadata",
                        stats["articles_stored"],
                    )

            except Exception as e:
                logger.exception(f"Error in Layer 1 (Global news): {e}")

            # ===== Layer 2: Watchlist Symbol-Specific News =====
            await _update_progress("layer2", "Layer 2: Fetching watchlist news...", 45)
            try:
                # Get all unique symbols from watchlists (all markets)
                query = select(WatchlistItem.symbol).distinct()
                result = await db.execute(query)
                watchlist_symbols = [row[0] for row in result.fetchall()]

                news_service = await get_news_service()
                import asyncio

                # Pass 1: Collect all watchlist articles
                watchlist_collected = []  # (symbol, article_data)
                for symbol in watchlist_symbols[:40]:  # Limit to 40 symbols per run
                    try:
                        articles = await news_service.get_news_by_symbol(
                            symbol,
                            force_refresh=True,
                        )
                        stats["watchlist_fetched"] += len(articles)
                        for article_data in articles[:5]:  # Limit per symbol
                            url = article_data.get("url", "")
                            if url and url not in seen_urls:
                                watchlist_collected.append((symbol, article_data))
                            if url:
                                seen_urls.add(url)
                        await asyncio.sleep(0.3)  # Rate limiting
                    except Exception as e:
                        logger.warning(f"Error fetching watchlist news for {symbol}: {e}")
                        continue

                # Pass 2: Batch dedup against DB (single query)
                wl_urls = [a[1].get("url", "") for a in watchlist_collected if a[1].get("url")]
                existing_wl_urls = set()
                if wl_urls:
                    dedup_query = select(News.url).where(News.url.in_(wl_urls))
                    dedup_result = await db.execute(dedup_query)
                    existing_wl_urls = {row[0] for row in dedup_result.fetchall()}

                # Pass 3: Store only new articles
                for symbol, article_data in watchlist_collected:
                    url = article_data.get("url", "")
                    if url in existing_wl_urls:
                        continue

                    news = News(
                        symbol=article_data.get("symbol", symbol),
                        title=article_data.get("title", "")[:500],
                        summary=article_data.get("summary"),
                        source=article_data.get("source", "unknown"),
                        url=url,
                        published_at=_parse_datetime(article_data.get("publishedAt")),
                        market=article_data.get("market", "US"),
                        related_entities=None,
                        has_stock_entities=False,
                        has_macro_entities=False,
                        max_entity_score=None,
                        primary_entity=None,
                        primary_entity_type=None,
                    )

                    if enable_pipeline:
                        # Watchlist articles get default full_analysis routing
                        news.filter_status = FilterStatus.INITIAL_USEFUL.value
                        news.processing_path = "full_analysis"
                        news.content_score = 0

                    db.add(news)
                    all_new_articles.append(news)
                    stats["articles_stored"] += 1

                    importance = _score_article_importance(article_data)
                    if importance >= 2.0:
                        important_articles.append(
                            (news, importance, article_data.get("title", "")[:60])
                        )

                logger.info(
                    f"Layer 2 (Watchlist): fetched={stats['watchlist_fetched']}, "
                    f"collected={len(watchlist_collected)}, dupes={len(existing_wl_urls)}"
                )

            except Exception as e:
                logger.exception(f"Error in Layer 2 (Watchlist news): {e}")

            # Commit all new articles
            await _update_progress("saving", f"Saving {stats['articles_stored']} new articles...", 85)
            await db.commit()

            # Dispatch AI analysis for important articles AFTER commit
            # so that rows exist in DB and IDs are assigned
            for news_obj, importance, title_preview in important_articles:
                await db.refresh(news_obj)  # Ensure ID is loaded
                if news_obj.id:
                    analyze_important_news.delay(str(news_obj.id))
                    logger.info(
                        "Queued AI analysis for important article: %s (score=%.1f)",
                        title_preview,
                        importance,
                    )

            # Write pipeline trace events for Layer 1
            if trace_batch:
                from app.services.pipeline_trace_service import PipelineTraceService
                try:
                    for item in trace_batch:
                        news_obj = item["news_obj"]
                        if news_obj.id:
                            await PipelineTraceService.record_event(
                                db, news_id=str(news_obj.id), layer="1",
                                node="layer1_scoring", status="success",
                                metadata={
                                    "decision": item["decision"],
                                    "reason": item.get("reason", ""),
                                    "score": item.get("score", 0),
                                },
                            )
                    await db.commit()
                except Exception as e:
                    logger.warning("Failed to write pipeline trace events: %s", e)

            # Dispatch Layer 1.5: batch fetch content (only when pipeline is ON)
            if enable_pipeline and all_new_articles:
                await _update_progress("dispatch", f"Dispatching batch fetch for {len(all_new_articles)} articles...", 92)
                batch = []
                for news_obj in all_new_articles:
                    try:
                        await db.refresh(news_obj)
                        if news_obj.id and news_obj.url:
                            batch.append({
                                "news_id": str(news_obj.id),
                                "url": news_obj.url,
                                "market": news_obj.market or "US",
                                "symbol": news_obj.symbol or "",
                                "title": news_obj.title or "",
                                "summary": news_obj.summary or "",
                                "source": news_obj.source or "",
                                "published_at": news_obj.published_at.isoformat() if news_obj.published_at else None,
                                "content_source": "trafilatura",
                                # Scoring data flows through to Layer 3
                                "content_score": news_obj.content_score or 0,
                                "processing_path": news_obj.processing_path or "lightweight",
                                "score_details": news_obj.score_details,
                            })
                    except Exception as e:
                        logger.warning("Failed to prepare article for batch fetch: %s", e)

                if batch:
                    from worker.tasks.full_content_tasks import batch_fetch_content, BATCH_CHUNK_SIZE
                    # Split large batches into chunks to keep task duration reasonable
                    for i in range(0, len(batch), BATCH_CHUNK_SIZE):
                        chunk = batch[i:i + BATCH_CHUNK_SIZE]
                        batch_fetch_content.delay(chunk)
                        logger.info(
                            "Dispatched batch_fetch_content: chunk %d/%d (%d articles)",
                            i // BATCH_CHUNK_SIZE + 1,
                            (len(batch) + BATCH_CHUNK_SIZE - 1) // BATCH_CHUNK_SIZE,
                            len(chunk),
                        )
            elif not enable_pipeline:
                logger.info("Pipeline OFF: skipping batch fetch dispatch for %d articles", len(all_new_articles))

            # Check news alerts
            alerts_triggered = await _check_news_alerts(db, stats["articles_stored"])
            stats["alerts_triggered"] = alerts_triggered

    except Exception as e:
        logger.exception(f"Error in news monitor: {e}")
        raise
    finally:
        # Always mark progress as finished, even if an error occurred
        await _finish_progress(stats)

    logger.info(
        "News monitor completed: "
        "global=%d (finnhub=%d, akshare=%d), "
        "watchlist=%d, stored=%d, alerts=%d, "
        "pipeline=%s, discard=%d, lightweight=%d, full=%d, critical=%d",
        stats["global_fetched"],
        stats["global_finnhub"],
        stats["global_akshare"],
        stats["watchlist_fetched"],
        stats["articles_stored"],
        stats["alerts_triggered"],
        stats["llm_pipeline_enabled"],
        stats["layer1_discard"],
        stats["layer1_lightweight"],
        stats["layer1_full_analysis"],
        stats["layer1_critical"],
    )

    return stats


async def _check_news_alerts(db, new_article_count: int) -> int:
    """Check news alerts against recent articles and trigger notifications."""
    from sqlalchemy import select
    from app.models.news import News, NewsAlert

    if new_article_count == 0:
        return 0

    alerts_triggered = 0

    try:
        # Get all active alerts
        alerts_query = select(NewsAlert).where(NewsAlert.is_active == True)
        alerts_result = await db.execute(alerts_query)
        alerts = alerts_result.scalars().all()

        if not alerts:
            return 0

        # Get recent news (last hour)
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)

        news_query = select(News).where(News.created_at >= cutoff)
        news_result = await db.execute(news_query)
        recent_news = news_result.scalars().all()

        # Check each alert against recent news
        for alert in alerts:
            for news in recent_news:
                # Check symbol match (if specified)
                if alert.symbol and alert.symbol != news.symbol:
                    continue

                # Check keyword match
                title_lower = news.title.lower() if news.title else ""
                summary_lower = news.summary.lower() if news.summary else ""
                combined_text = f"{title_lower} {summary_lower}"

                for keyword in alert.keywords:
                    if keyword.lower() in combined_text:
                        # Trigger notification
                        _trigger_alert_notification.delay(
                            str(alert.id),
                            str(alert.user_id),
                            str(news.id),
                            news.title,
                            keyword,
                        )
                        alerts_triggered += 1
                        break  # Only trigger once per alert-news pair

    except Exception as e:
        logger.error(f"Error checking news alerts: {e}")

    return alerts_triggered


@celery_app.task
def _trigger_alert_notification(
    alert_id: str,
    user_id: str,
    news_id: str,
    news_title: str,
    matched_keyword: str,
):
    """Send notification for triggered news alert."""
    logger.info(
        f"News alert triggered: alert={alert_id}, user={user_id}, "
        f"news={news_id}, keyword={matched_keyword}"
    )

    # TODO: Implement actual notification sending
    # - Email notification
    # - WebPush notification
    # - In-app notification

    return {
        "status": "sent",
        "alert_id": alert_id,
        "user_id": user_id,
        "news_id": news_id,
        "keyword": matched_keyword,
    }


@celery_app.task(bind=True, max_retries=2)
def analyze_important_news(self, news_id: str):
    """
    AI analyze important news articles.

    This task is triggered for news articles that match alerts
    or are deemed significant based on source/content.

    Args:
        news_id: UUID of the news article to analyze
    """
    try:
        return run_async_task(_analyze_news_async, news_id)
    except Exception as e:
        logger.exception(f"News analysis task failed: {e}")
        raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))


async def _analyze_news_async(news_id: str) -> Dict[str, Any]:
    """Async implementation of news analysis."""
    import json
    from sqlalchemy import select

    from app.config import settings
    from app.models.news import News
    from app.agents.prompts.news_prompt import (
        build_news_analysis_prompt,
        get_news_analysis_system_prompt,
    )

    # Use shared database engine and session factory from backend

    try:
        async with get_task_session() as db:
            # Load system AI config from DB (fallback to env)
            from worker.db_utils import get_system_ai_config
            sys_config = await get_system_ai_config(db)

            if not sys_config.api_key:
                logger.warning("OpenAI API key not configured, skipping analysis")
                return {"status": "skipped", "reason": "no_api_key"}

            # Get news article
            query = select(News).where(News.id == news_id)
            result = await db.execute(query)
            news = result.scalar_one_or_none()

            if not news:
                logger.warning(f"News article not found: {news_id}")
                return {"status": "error", "reason": "not_found"}

            # Skip if already analyzed
            if news.ai_analysis:
                logger.info(f"News article already analyzed: {news_id}")
                return {"status": "skipped", "reason": "already_analyzed"}

            # Build prompt
            system_prompt = get_news_analysis_system_prompt()
            user_prompt = build_news_analysis_prompt(
                symbol=news.symbol,
                title=news.title,
                summary=news.summary,
                source=news.source,
                published_at=news.published_at.isoformat() if news.published_at else "unknown",
                market=news.market,
            )

            # Check background rate limit - raise to trigger Celery retry
            from app.core.token_bucket import get_background_rate_limiter
            rate_limiter = await get_background_rate_limiter()
            if not await rate_limiter.acquire():
                logger.warning("Background rate limit reached, will retry news analysis for %s", news_id)
                raise RuntimeError(f"Background rate limit exceeded for news {news_id}")

            # Use LLM gateway for news analysis
            from app.core.llm import (
                get_llm_gateway,
                ChatRequest as _ChatRequest,
                Message as _Message,
                Role as _Role,
            )
            gateway = get_llm_gateway()
            model = sys_config.model
            if not model:
                logger.warning("No LLM model configured in Admin Settings, skipping analysis")
                return {"status": "skipped", "reason": "no_model_configured"}
            response = await gateway.chat(
                _ChatRequest(
                    model=model,
                    messages=[
                        _Message(role=_Role.SYSTEM, content=system_prompt),
                        _Message(role=_Role.USER, content=user_prompt),
                    ],
                    max_tokens=1000,
                    temperature=0.3,
                ),
                system_api_key=sys_config.api_key,
                system_base_url=sys_config.base_url,
                use_user_config=False,
            )

            content = response.content

            # Parse sentiment score from response
            sentiment_score = 0.0
            try:
                start = content.find("{")
                end = content.rfind("}") + 1
                if start >= 0 and end > start:
                    analysis = json.loads(content[start:end])
                    sentiment_score = float(analysis.get("sentiment_score", 0))
            except (json.JSONDecodeError, ValueError):
                pass

            # Update news article
            news.sentiment_score = sentiment_score
            news.ai_analysis = content
            await db.commit()

            # Re-embed with enriched content (original + AI analysis)
            try:
                from worker.tasks.embedding_tasks import embed_news_article
                enriched = f"{news.title or ''}\n\n{news.summary or ''}\n\n{content}"
                embed_news_article.delay(str(news.id), enriched, news.symbol)
                logger.info("Queued re-embedding for analyzed article %s", news_id)
            except Exception as embed_err:
                logger.warning("Failed to dispatch re-embedding for %s: %s", news_id, embed_err)

            logger.info(f"Analyzed news article {news_id}: sentiment={sentiment_score}")

            return {
                "status": "success",
                "news_id": news_id,
                "sentiment_score": sentiment_score,
            }

    except Exception as e:
        logger.exception(f"Error analyzing news: {e}")
        raise
    # get_task_session handles connection cleanup automatically


def _score_article_importance(article: Dict[str, Any]) -> float:
    """
    Score an article's importance to determine if it warrants AI analysis.

    Returns a score >= 1.0, where >= 2.0 triggers AI analysis.

    Factors:
    - Source reputation (Reuters, Bloomberg etc. score higher)
    - Title keywords (earnings, merger, FDA etc.)
    """
    score = 1.0

    SOURCE_WEIGHTS = {
        "reuters": 1.5, "bloomberg": 1.5, "wsj": 1.4,
        "cnbc": 1.3, "ft": 1.4, "barrons": 1.3,
        "marketwatch": 1.2, "eastmoney": 1.2,
    }

    KEYWORD_WEIGHTS = {
        "earnings": 2.0, "revenue": 1.8, "profit": 1.8, "loss": 1.8,
        "acquisition": 2.0, "merger": 2.0, "buyout": 2.0,
        "bankruptcy": 2.5, "fraud": 2.5, "investigation": 2.0,
        "fda": 2.0, "approval": 1.8, "dividend": 1.5,
        "upgrade": 1.5, "downgrade": 1.5, "guidance": 1.5,
        "利润": 1.8, "营收": 1.8, "亏损": 1.8,
        "收购": 2.0, "合并": 2.0, "涨停": 1.5, "跌停": 1.5,
    }

    source = (article.get("source") or "").lower()
    for src_key, weight in SOURCE_WEIGHTS.items():
        if src_key in source:
            score *= weight
            break

    title = (article.get("title") or "").lower()
    summary = (article.get("summary") or "").lower()
    text = f"{title} {summary}"

    max_kw = 1.0
    for keyword, weight in KEYWORD_WEIGHTS.items():
        if keyword in text:
            max_kw = max(max_kw, weight)
    score *= max_kw

    return score


def _parse_datetime(dt_str) -> datetime:
    """Parse datetime string to datetime object."""
    if isinstance(dt_str, datetime):
        return dt_str

    if not dt_str:
        return datetime.now(timezone.utc)

    try:
        # Try ISO format first
        if isinstance(dt_str, str):
            # Remove timezone info if present for parsing
            dt_str = dt_str.replace("Z", "+00:00")
            return datetime.fromisoformat(dt_str)
    except Exception:
        pass

    return datetime.now(timezone.utc)
