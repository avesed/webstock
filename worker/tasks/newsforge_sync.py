"""NewsForge watched-symbol sync Celery task.

Periodically aggregates the full set of distinct symbols across every user's
watchlists and POSTs them to NewsForge's
``/api/internal/watched-symbols/sync`` endpoint. NewsForge uses this list to
drive its StockPulse poller (per-symbol news fetching, hot/warm/cold tiering).

`last_viewed_at` is set to the most recent reading_history / watchlist
update for each symbol, so symbols actively in use stay in NewsForge's hot
tier. Bare 6-digit A-share codes are sent with an explicit ``market="sh"``
or ``market="sz"`` because StockPulse's auto-detection treats them as US.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import func, select

from worker.celery_app import celery_app
from worker.task_helpers import run_async_task

logger = logging.getLogger(__name__)

_BARE_A_SHARE = re.compile(r"^\d{6}$")


def _market_hint_for(symbol: str) -> str | None:
    """Return 'sh' / 'sz' for bare 6-digit A-share codes, else None.

    StockPulse handles `.HK`, `.SS`, `.SZ` suffixes and US tickers itself.
    Only bare 6-digit codes need an explicit hint to avoid being misrouted
    to US providers.
    """
    s = (symbol or "").strip().upper()
    if not _BARE_A_SHARE.match(s):
        return None
    if s.startswith("6"):
        return "sh"
    if s.startswith(("0", "3")):
        return "sz"
    return None


async def _aggregate_watched_symbols() -> list[dict]:
    """Build the watched-symbols payload from the watchlists table.

    Returns a list of {symbol, market, last_viewed_at} dicts, one per
    distinct (symbol, market). `last_viewed_at` uses the most recent
    `watchlist_items.added_at` across all users that have the symbol —
    a coarse proxy for active interest until per-user view tracking
    is added.
    """
    from app.db.task_session import get_task_session
    from app.models.watchlist import WatchlistItem

    async with get_task_session() as session:
        # Per-symbol most-recent added_at, across all users
        stmt = (
            select(
                WatchlistItem.symbol,
                func.max(WatchlistItem.added_at).label("last_viewed_at"),
            )
            .group_by(WatchlistItem.symbol)
        )
        rows = (await session.execute(stmt)).all()

    payload: list[dict] = []
    for row in rows:
        sym = (row.symbol or "").strip().upper()
        if not sym:
            continue
        last_viewed = row.last_viewed_at
        if last_viewed and last_viewed.tzinfo is None:
            last_viewed = last_viewed.replace(tzinfo=timezone.utc)
        payload.append({
            "symbol": sym,
            "market": _market_hint_for(sym),
            "lastViewedAt": last_viewed.isoformat() if last_viewed else None,
        })
    return payload


async def _sync_to_newsforge() -> dict:
    """Run one sync iteration: aggregate watchlists → POST to NewsForge."""
    from app.services.newsforge_client import NewsForgeClient

    client = NewsForgeClient()
    if not client.enabled:
        logger.debug("NewsForge client not configured, skipping watched-symbol sync")
        return {"skipped": True, "reason": "newsforge_disabled"}

    started = datetime.now(timezone.utc)
    symbols = await _aggregate_watched_symbols()
    if not symbols:
        logger.debug("No watchlist symbols to sync to NewsForge")
        return {"skipped": True, "reason": "no_symbols"}

    try:
        result = await client.sync_watched_symbols(symbols)
    except Exception as e:
        logger.exception("NewsForge watched-symbol sync failed: %s", e)
        return {"error": str(e)[:200]}

    elapsed_ms = int(
        (datetime.now(timezone.utc) - started).total_seconds() * 1000
    )
    logger.info(
        "NewsForge watched-symbol sync: %d symbols → received=%s upserted=%s (%dms)",
        len(symbols),
        result.get("received"),
        result.get("upserted"),
        elapsed_ms,
    )
    return {"sent": len(symbols), "result": result, "elapsed_ms": elapsed_ms}


@celery_app.task(
    name="worker.tasks.newsforge_sync.sync_watched_symbols_to_newsforge",
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def sync_watched_symbols_to_newsforge() -> dict:
    """Celery task: sync watchlist symbols to NewsForge."""
    return run_async_task(_sync_to_newsforge())
