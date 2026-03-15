"""Lightweight LLM usage recorder for data-processor.

Writes usage records directly to the shared PostgreSQL ``llm_usage_records``
table via the asyncpg pool from SettingsCache.  Fire-and-forget — failures
are logged at debug level and never propagate to the caller.

This mirrors the backend's LlmCostService.record_usage() but uses raw SQL
instead of SQLAlchemy, since data-processor is an asyncpg-only service.

NOTE: If ``llm_usage_records`` or ``model_pricing`` schema changes,
update the raw SQL here to match.  The ORM source of truth is
``backend/app/models/llm_cost.py``.
"""

import json
import logging
import time
import uuid
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Pricing cache: model -> (pricing_id, input, cached_input, output, cached_at)
_pricing_cache: dict[str, tuple[uuid.UUID, Decimal, Optional[Decimal], Decimal, float]] = {}
_PRICING_CACHE_MAX = 50
_PRICING_CACHE_TTL = 300.0  # 5 minutes, matches backend LlmCostService


async def record_usage(
    purpose: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Record an LLM usage event to llm_usage_records.

    Fire-and-forget: never raises, logs failures at debug level.

    Args:
        purpose: Call category (e.g. "ml_agent", "rdagent").
        model: Model name from upstream response.
        prompt_tokens: Input token count.
        completion_tokens: Output token count.
        cached_tokens: Cached input tokens (if any).
        metadata: Optional JSONB context dict.
    """
    try:
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            logger.debug("Usage recorder: no DB pool available")
            return

        total_tokens = prompt_tokens + completion_tokens
        metadata_json = json.dumps(metadata) if metadata else None

        # Single connection for both pricing lookup and INSERT
        async with pool.acquire(timeout=5) as conn:
            pricing_id, cost_usd = await _get_cost(
                conn, model, prompt_tokens, completion_tokens, cached_tokens,
            )
            await conn.execute(
                """
                INSERT INTO llm_usage_records
                    (id, created_at, model, purpose, user_id,
                     prompt_tokens, completion_tokens, cached_tokens,
                     total_tokens, cost_usd, metadata_, pricing_id)
                VALUES
                    ($1, NOW(), $2, $3, NULL,
                     $4, $5, $6,
                     $7, $8, $9::jsonb, $10)
                """,
                uuid.uuid4(),
                model,
                purpose,
                prompt_tokens,
                completion_tokens,
                cached_tokens,
                total_tokens,
                cost_usd,
                metadata_json,
                pricing_id,
            )
        logger.debug(
            "Recorded usage: purpose=%s model=%s tokens=%d cost=$%.6f",
            purpose, model, total_tokens, cost_usd,
        )
    except Exception as e:
        logger.debug("Failed to record LLM usage: %s: %s", type(e).__name__, e)


async def _get_cost(
    conn: Any,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
) -> tuple[Optional[uuid.UUID], Decimal]:
    """Look up active pricing and calculate cost.

    Uses a single connection (passed in) to avoid double-acquire.
    Returns (pricing_id, cost_usd). Returns (None, 0) if no pricing found.
    """
    now = time.monotonic()

    # Check cache (with TTL)
    if model in _pricing_cache:
        pid, inp, cached_inp, out, cached_at = _pricing_cache[model]
        if now - cached_at < _PRICING_CACHE_TTL:
            return pid, _calculate(inp, cached_inp, out, prompt_tokens, completion_tokens, cached_tokens)
        # Expired — fall through to DB query

    # Query DB for active pricing
    try:
        row = await conn.fetchrow(
            """
            SELECT id, input_price, cached_input_price, output_price
            FROM model_pricing
            WHERE model = $1 AND effective_from <= CURRENT_DATE
            ORDER BY effective_from DESC
            LIMIT 1
            """,
            model,
        )
        if not row:
            return None, Decimal("0")

        pid = row["id"]
        inp = Decimal(str(row["input_price"]))
        cached_inp = Decimal(str(row["cached_input_price"])) if row["cached_input_price"] is not None else None
        out = Decimal(str(row["output_price"]))

        # Evict oldest if cache full
        if len(_pricing_cache) >= _PRICING_CACHE_MAX:
            _pricing_cache.pop(next(iter(_pricing_cache)), None)
        _pricing_cache[model] = (pid, inp, cached_inp, out, now)

        return pid, _calculate(inp, cached_inp, out, prompt_tokens, completion_tokens, cached_tokens)
    except Exception as e:
        logger.debug("Failed to look up pricing for model=%s: %s", model, e)
        return None, Decimal("0")


def _calculate(
    input_price: Decimal,
    cached_input_price: Optional[Decimal],
    output_price: Decimal,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
) -> Decimal:
    """Calculate cost in USD. Prices are per 1M tokens."""
    million = Decimal("1000000")
    # Clamp cached_tokens to prompt_tokens (matches backend LlmCostService)
    safe_cached = min(cached_tokens, prompt_tokens)
    regular_input = prompt_tokens - safe_cached
    cached_rate = cached_input_price if cached_input_price is not None else input_price

    cost = (
        Decimal(regular_input) * input_price / million
        + Decimal(safe_cached) * cached_rate / million
        + Decimal(completion_tokens) * output_price / million
    )
    return cost.quantize(Decimal("0.000001"))
