"""News monitoring Celery tasks."""

import logging
from datetime import datetime, timezone
from typing import List, Dict, Any

from worker.celery_app import celery_app

# Use Celery-safe database utilities (avoids event loop conflicts)
from worker.db_utils import get_task_session

logger = logging.getLogger(__name__)


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
    import asyncio

    try:
        # Run the async monitor function
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_monitor_news_async())
            return result
        finally:
            loop.close()
    except Exception as e:
        logger.exception(f"News monitor task failed: {e}")
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))


async def _monitor_news_async() -> Dict[str, Any]:
    """Async implementation of news monitoring."""
    from sqlalchemy import select

    from app.models.watchlist import Watchlist, WatchlistItem
    from app.models.news import News, NewsAlert
    from app.services.news_service import get_news_service

    logger.info("Starting news monitor task")

    # Use shared database engine and session factory from backend
    # This ensures consistent connection pooling configuration

    stats = {
        "symbols_checked": 0,
        "articles_fetched": 0,
        "articles_stored": 0,
        "alerts_triggered": 0,
    }

    try:
        async with get_task_session() as db:
            # Get all unique symbols from all watchlists
            query = select(WatchlistItem.symbol).distinct()
            result = await db.execute(query)
            symbols = [row[0] for row in result.fetchall()]

            stats["symbols_checked"] = len(symbols)
            logger.info(f"Monitoring news for {len(symbols)} unique symbols")

            if not symbols:
                logger.info("No symbols to monitor")
                return stats

            # Get news service
            news_service = await get_news_service()

            # Fetch news for each symbol (with rate limiting)
            import asyncio

            # Collect important articles for post-commit AI analysis dispatch
            important_articles: List[tuple] = []  # (News obj, importance score, title preview)
            all_new_articles: List = []  # Track all new articles for embedding

            for symbol in symbols[:50]:  # Limit to 50 symbols per run
                try:
                    articles = await news_service.get_news_by_symbol(
                        symbol,
                        force_refresh=True,
                    )
                    stats["articles_fetched"] += len(articles)

                    # Store new articles in database
                    for article_data in articles[:10]:  # Limit per symbol
                        try:
                            # Check if article already exists
                            existing_query = select(News).where(
                                News.url == article_data.get("url")
                            )
                            existing_result = await db.execute(existing_query)
                            if existing_result.scalar_one_or_none():
                                continue

                            # Create new news record
                            news = News(
                                symbol=article_data.get("symbol"),
                                title=article_data.get("title", "")[:500],
                                summary=article_data.get("summary"),
                                source=article_data.get("source", "unknown"),
                                url=article_data.get("url", ""),
                                published_at=_parse_datetime(article_data.get("published_at")),
                                market=article_data.get("market", "US"),
                            )
                            db.add(news)
                            all_new_articles.append(news)
                            stats["articles_stored"] += 1

                            # Score article importance for AI analysis
                            importance = _score_article_importance(article_data)
                            if importance >= 2.0:
                                important_articles.append(
                                    (news, importance, article_data.get("title", "")[:60])
                                )

                        except Exception as e:
                            logger.warning(f"Error storing article: {e}")
                            continue

                    # Small delay to avoid rate limiting
                    await asyncio.sleep(0.5)

                except Exception as e:
                    logger.warning(f"Error fetching news for {symbol}: {e}")
                    continue

            # Commit all new articles (assigns IDs via flush)
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

            # Dispatch full content fetching for ALL newly stored articles
            # This replaces direct embedding - full_content_tasks will handle embedding
            for news_obj in all_new_articles:
                try:
                    await db.refresh(news_obj)
                    if news_obj.id and news_obj.url:
                        from worker.tasks.full_content_tasks import fetch_news_content
                        fetch_news_content.delay(
                            str(news_obj.id),
                            news_obj.url,
                            news_obj.market,
                            news_obj.symbol,
                            None,  # user_id - use default settings
                        )
                        logger.debug(
                            "Dispatched full content fetch for news_id=%s",
                            news_obj.id,
                        )
                except Exception as e:
                    logger.warning("Failed to dispatch full content fetch: %s", e)

            # Check news alerts
            alerts_triggered = await _check_news_alerts(db, stats["articles_stored"])
            stats["alerts_triggered"] = alerts_triggered

    except Exception as e:
        logger.exception(f"Error in news monitor: {e}")
        raise
    # get_task_session handles connection cleanup automatically

    logger.info(
        f"News monitor completed: {stats['symbols_checked']} symbols, "
        f"{stats['articles_fetched']} fetched, {stats['articles_stored']} stored, "
        f"{stats['alerts_triggered']} alerts triggered"
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
    import asyncio

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_analyze_news_async(news_id))
            return result
        finally:
            loop.close()
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

    if not settings.OPENAI_API_KEY:
        logger.warning("OpenAI API key not configured, skipping analysis")
        return {"status": "skipped", "reason": "no_api_key"}

    # Use shared database engine and session factory from backend

    try:
        async with get_task_session() as db:
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

            # Use shared OpenAI client manager
            from app.core.openai_client import get_openai_client, get_openai_model
            client = get_openai_client()

            response = await client.chat.completions.create(
                model=get_openai_model(),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=1000,
                temperature=0.3,
            )

            content = response.choices[0].message.content

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
