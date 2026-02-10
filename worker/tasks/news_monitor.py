"""News monitoring Celery tasks."""

import asyncio
import json
import logging
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


async def _run_initial_filter_if_enabled(
    db,
    system_settings,
    articles: List[Dict[str, str]],
) -> tuple[Dict[str, Dict], bool]:
    """
    Run initial filter if two-phase filtering is enabled.

    Args:
        db: Database session
        system_settings: System settings with feature flag
        articles: List of dicts with url, headline, summary

    Returns:
        Tuple of (filter_results dict, is_enabled bool)
    """
    if not system_settings.use_two_phase_filter:
        return {}, False

    from app.services.two_phase_filter_service import get_two_phase_filter_service
    from app.services.filter_stats_service import get_filter_stats_service

    filter_service = get_two_phase_filter_service()
    stats_service = get_filter_stats_service()

    results = await filter_service.batch_initial_filter(db, articles)

    # Track stats
    useful_count = sum(1 for r in results.values() if r["decision"] == "useful")
    uncertain_count = sum(1 for r in results.values() if r["decision"] == "uncertain")
    skip_count = sum(1 for r in results.values() if r["decision"] == "skip")

    await stats_service.increment("initial_useful", useful_count)
    await stats_service.increment("initial_uncertain", uncertain_count)
    await stats_service.increment("initial_skip", skip_count)

    return results, True


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

    Two-layer news acquisition strategy:
    - Layer 1: Global market news (Finnhub general + AKShare trending → initial filter)
    - Layer 2: Watchlist symbol-specific news (per-symbol queries, direct store)
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
    from app.services.two_phase_filter_service import get_two_phase_filter_service

    logger.info("Starting news monitor task (two-layer strategy)")
    await _update_progress("init", "Initializing news monitor...", 0)

    stats = {
        "global_fetched": 0,
        "global_finnhub": 0,
        "global_akshare": 0,
        "watchlist_fetched": 0,
        "articles_stored": 0,
        "alerts_triggered": 0,
        "entities_extracted": 0,
        "high_relevance_count": 0,  # score >= 0.7
        "stock_count": 0,
        "index_count": 0,
        "macro_count": 0,
        # Two-phase filter stats
        "initial_useful": 0,
        "initial_uncertain": 0,
        "initial_skipped": 0,
        "two_phase_enabled": False,
    }

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
            all_new_articles: List = []  # Track all new articles for embedding

            # ===== Layer 1: Global Market News (Finnhub + AKShare → Initial Filter) =====
            await _update_progress("layer1", "Layer 1: Fetching global market news...", 5)
            use_two_phase = system_settings.use_two_phase_filter
            stats["two_phase_enabled"] = use_two_phase

            try:
                # --- Fetch from both sources ---
                # Source 1: Finnhub news (all categories)
                finnhub_articles = []
                finnhub_categories = ["general", "forex", "crypto", "merger"]
                for cat in finnhub_categories:
                    try:
                        if use_two_phase:
                            cat_articles = await FinnhubProvider.get_general_news(
                                category=cat,
                                api_key=finnhub_api_key,
                            )
                        else:
                            cat_articles = await FinnhubProvider.get_market_news_with_entities(
                                db=db,
                                category=cat,
                                api_key=finnhub_api_key,
                            )
                        finnhub_articles.extend(cat_articles)
                        logger.info(f"Layer 1: Finnhub [{cat}] fetched {len(cat_articles)} articles")
                    except Exception as e:
                        logger.warning(f"Layer 1: Finnhub [{cat}] fetch failed: {e}")
                stats["global_finnhub"] = len(finnhub_articles)

                await _update_progress("layer1_akshare", "Layer 1: Fetching AKShare news...", 10)

                # Source 2: AKShare trending (东财全球快讯)
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

                if use_two_phase:
                    # Two-phase mode: run initial filter on all new global articles
                    articles_for_filter = [
                        {
                            "url": a.url,
                            "headline": a.title,
                            "summary": a.summary or "",
                        }
                        for a in new_articles
                    ]

                    await _update_progress("layer1_filter", f"Layer 1: Filtering {len(new_articles)} new articles...", 20)
                    try:
                        filter_results, _ = await _run_initial_filter_if_enabled(
                            db, system_settings, articles_for_filter
                        )
                    except Exception as e:
                        logger.warning("Layer 1: Initial filter failed, proceeding without filter: %s", e)
                        filter_results = {}

                    # Store articles that passed initial filter
                    filter_service = get_two_phase_filter_service()
                    for article in new_articles:
                        filter_result = filter_results.get(article.url, {})
                        decision = filter_result.get("decision", "uncertain")

                        if decision == "skip":
                            stats["initial_skipped"] += 1
                            continue

                        if decision == "useful":
                            stats["initial_useful"] += 1
                        else:
                            stats["initial_uncertain"] += 1

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
                            filter_status=filter_service.map_initial_decision_to_status(decision).value,
                        )
                        db.add(news)
                        all_new_articles.append(news)
                        stats["articles_stored"] += 1

                        article_dict = article.to_dict()
                        importance = _score_article_importance(article_dict)
                        if importance >= 2.0:
                            important_articles.append(
                                (news, importance, article.title[:60] if article.title else "")
                            )

                    logger.info(
                        f"Layer 1 (two-phase): useful={stats['initial_useful']}, "
                        f"uncertain={stats['initial_uncertain']}, skipped={stats['initial_skipped']}"
                    )

                else:
                    # Legacy mode: Finnhub articles have entities, AKShare don't
                    # Count entity stats from Finnhub articles
                    stats["entities_extracted"] = sum(
                        1 for a in finnhub_articles if a.related_entities
                    )
                    for article in finnhub_articles:
                        if article.related_entities:
                            for entity in article.related_entities:
                                if entity.get("score", 0) >= 0.7:
                                    stats["high_relevance_count"] += 1
                                entity_type = entity.get("type")
                                if entity_type == "stock":
                                    stats["stock_count"] += 1
                                elif entity_type == "index":
                                    stats["index_count"] += 1
                                elif entity_type == "macro":
                                    stats["macro_count"] += 1

                    # Store all new articles
                    for article in new_articles:
                        # Finnhub articles have entities, AKShare articles don't
                        entities = getattr(article, "related_entities", None) or []
                        has_stock = any(e["type"] == "stock" for e in entities) if entities else False
                        has_macro = any(e["type"] == "macro" for e in entities) if entities else False
                        max_score = max((e["score"] for e in entities), default=None) if entities else None

                        primary_entity = None
                        primary_entity_type = None
                        if entities:
                            stock_entities = [e for e in entities if e["type"] == "stock"]
                            if stock_entities:
                                primary_entity = stock_entities[0]["entity"]
                                primary_entity_type = "stock"
                            else:
                                primary_entity = entities[0]["entity"]
                                primary_entity_type = entities[0]["type"]

                        news = News(
                            symbol=article.symbol,
                            title=article.title[:500] if article.title else "",
                            summary=article.summary,
                            source=article.source,
                            url=article.url,
                            published_at=article.published_at,
                            market=article.market,
                            related_entities=entities if entities else None,
                            has_stock_entities=has_stock,
                            has_macro_entities=has_macro,
                            max_entity_score=max_score,
                            primary_entity=primary_entity,
                            primary_entity_type=primary_entity_type,
                        )
                        db.add(news)
                        all_new_articles.append(news)
                        stats["articles_stored"] += 1

                        article_dict = article.to_dict()
                        importance = _score_article_importance(article_dict)
                        if importance >= 2.0:
                            important_articles.append(
                                (news, importance, article.title[:60] if article.title else "")
                            )

                    logger.info(
                        f"Layer 1 (legacy): stored={stats['articles_stored']}, "
                        f"with_entities={stats['entities_extracted']}, "
                        f"stocks={stats['stock_count']}, indices={stats['index_count']}, macro={stats['macro_count']}"
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

                    # Company news: related_entities = the stock itself with score 1.0
                    entities = [{
                        "entity": symbol,
                        "type": "stock",
                        "score": 1.0,
                    }]

                    news = News(
                        symbol=article_data.get("symbol", symbol),
                        title=article_data.get("title", "")[:500],
                        summary=article_data.get("summary"),
                        source=article_data.get("source", "unknown"),
                        url=url,
                        published_at=_parse_datetime(article_data.get("publishedAt")),
                        market=article_data.get("market", "US"),
                        related_entities=entities,
                        has_stock_entities=True,
                        has_macro_entities=False,
                        max_entity_score=1.0,
                        primary_entity=symbol,
                        primary_entity_type="stock",
                    )
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

            await _update_progress("dispatch", f"Dispatching news pipeline for {len(all_new_articles)} articles...", 92)
            # Dispatch LangGraph news pipeline for ALL newly stored articles
            for news_obj in all_new_articles:
                try:
                    await db.refresh(news_obj)
                    if news_obj.id and news_obj.url:
                        from worker.tasks.full_content_tasks import process_news_article
                        process_news_article.delay(
                            news_id=str(news_obj.id),
                            url=news_obj.url,
                            market=news_obj.market or "US",
                            symbol=news_obj.symbol or "",
                            title=news_obj.title or "",
                            summary=news_obj.summary or "",
                            published_at=news_obj.published_at.isoformat() if news_obj.published_at else None,
                            use_two_phase=use_two_phase,
                        )
                        logger.debug(
                            "Dispatched news pipeline for news_id=%s (two_phase=%s)",
                            news_obj.id,
                            use_two_phase,
                        )
                except Exception as e:
                    logger.warning("Failed to dispatch news pipeline: %s", e)

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
        f"News monitor completed: "
        f"global={stats['global_fetched']} (finnhub={stats['global_finnhub']}, akshare={stats['global_akshare']}), "
        f"watchlist={stats['watchlist_fetched']}, stored={stats['articles_stored']}, "
        f"entities={stats['entities_extracted']}, alerts={stats['alerts_triggered']}"
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
