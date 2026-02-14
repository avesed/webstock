"""LLM cost tracking service — pricing management, usage recording, aggregation.

Records every LLM API call permanently in PostgreSQL with token counts and
cost (calculated at insert time using active pricing). Provides aggregation
queries for the admin cost tracking dashboard.
"""

import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import case, func, literal_column, select, and_, desc, cast, Date as SADate
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_async_session
from app.models.llm_cost import LlmUsageRecord, ModelPricing

logger = logging.getLogger(__name__)

# In-memory pricing cache: cache_key -> (ModelPricing row, cached_at)
_pricing_cache: Dict[str, tuple] = {}
_CACHE_TTL = 300  # 5 minutes
_CACHE_MAX_SIZE = 200  # Prevent unbounded growth (date-keyed entries)


class LlmCostService:
    """
    Permanent LLM usage and cost tracking service.

    - Pricing management with time-effective history
    - Usage recording with cost calculation at insert time
    - Aggregation queries for admin dashboard
    """

    # ------------------------------------------------------------------
    # Pricing management
    # ------------------------------------------------------------------

    async def get_active_pricing(
        self, model: str, ref_date: Optional[date] = None,
    ) -> Optional[ModelPricing]:
        """Get the active pricing for a model on a given date.

        Uses in-memory cache (5-min TTL) for performance.
        """
        d = ref_date or date.today()
        cache_key = f"{model}:{d.isoformat()}"
        cached = _pricing_cache.get(cache_key)
        if cached and (time.monotonic() - cached[1]) < _CACHE_TTL:
            return cached[0]

        async with get_async_session() as db:
            result = await db.execute(
                select(ModelPricing)
                .where(
                    and_(
                        ModelPricing.model == model,
                        ModelPricing.effective_from <= d,
                    )
                )
                .order_by(desc(ModelPricing.effective_from))
                .limit(1)
            )
            pricing = result.scalar_one_or_none()

        # Evict oldest entries if cache exceeds max size
        if len(_pricing_cache) >= _CACHE_MAX_SIZE:
            now = time.monotonic()
            expired = [k for k, v in _pricing_cache.items() if (now - v[1]) >= _CACHE_TTL]
            for k in expired:
                _pricing_cache.pop(k, None)
            # If still over limit, remove oldest half
            if len(_pricing_cache) >= _CACHE_MAX_SIZE:
                sorted_keys = sorted(_pricing_cache, key=lambda k: _pricing_cache[k][1])
                for k in sorted_keys[: len(sorted_keys) // 2]:
                    _pricing_cache.pop(k, None)

        _pricing_cache[cache_key] = (pricing, time.monotonic())
        return pricing

    async def get_all_pricing(
        self, db: AsyncSession,
    ) -> List[ModelPricing]:
        """Get all pricing rows for admin display (latest first)."""
        result = await db.execute(
            select(ModelPricing).order_by(
                ModelPricing.model, desc(ModelPricing.effective_from)
            )
        )
        return list(result.scalars().all())

    async def get_current_pricing(
        self, db: AsyncSession,
    ) -> List[ModelPricing]:
        """Get only the currently active pricing per model (latest per model)."""
        # Subquery: max effective_from per model where <= today
        today = date.today()
        subq = (
            select(
                ModelPricing.model,
                func.max(ModelPricing.effective_from).label("max_date"),
            )
            .where(ModelPricing.effective_from <= today)
            .group_by(ModelPricing.model)
            .subquery()
        )
        result = await db.execute(
            select(ModelPricing).join(
                subq,
                and_(
                    ModelPricing.model == subq.c.model,
                    ModelPricing.effective_from == subq.c.max_date,
                ),
            )
            .order_by(ModelPricing.model)
        )
        return list(result.scalars().all())

    async def set_pricing(
        self,
        db: AsyncSession,
        model: str,
        input_price: float,
        output_price: float,
        cached_input_price: Optional[float] = None,
        effective_from: Optional[date] = None,
    ) -> ModelPricing:
        """Set pricing for a model. Creates a new row (time-effective).

        If a row already exists for the same model + effective_from, it is
        updated (upsert by unique constraint).
        """
        eff_date = effective_from or date.today()

        # Check for existing row on same date
        result = await db.execute(
            select(ModelPricing).where(
                and_(
                    ModelPricing.model == model,
                    ModelPricing.effective_from == eff_date,
                )
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.input_price = Decimal(str(input_price))
            existing.cached_input_price = (
                Decimal(str(cached_input_price))
                if cached_input_price is not None else None
            )
            existing.output_price = Decimal(str(output_price))
            pricing = existing
        else:
            pricing = ModelPricing(
                model=model,
                input_price=Decimal(str(input_price)),
                cached_input_price=(
                    Decimal(str(cached_input_price))
                    if cached_input_price is not None else None
                ),
                output_price=Decimal(str(output_price)),
                effective_from=eff_date,
            )
            db.add(pricing)

        await db.flush()

        # Invalidate cache for this model
        keys_to_remove = [k for k in _pricing_cache if k.startswith(f"{model}:")]
        for k in keys_to_remove:
            _pricing_cache.pop(k, None)

        return pricing

    async def delete_pricing(
        self, db: AsyncSession, pricing_id: str,
    ) -> bool:
        """Delete a pricing row. Returns True if found and deleted."""
        import uuid as uuid_mod
        result = await db.execute(
            select(ModelPricing).where(ModelPricing.id == uuid_mod.UUID(pricing_id))
        )
        row = result.scalar_one_or_none()
        if not row:
            return False
        model = row.model
        await db.delete(row)
        # Invalidate cache
        keys_to_remove = [k for k in _pricing_cache if k.startswith(f"{model}:")]
        for k in keys_to_remove:
            _pricing_cache.pop(k, None)
        return True

    # ------------------------------------------------------------------
    # Cost calculation
    # ------------------------------------------------------------------

    def calculate_cost(
        self,
        pricing: Optional[ModelPricing],
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
    ) -> Decimal:
        """Calculate cost in USD from token counts and pricing.

        Formula:
          regular_input = prompt_tokens - cached_tokens
          cost = regular_input * input_rate/1M
               + cached_tokens * cached_rate/1M
               + completion_tokens * output_rate/1M

        When cached_input_price is NULL, input_price is used (no discount).
        When pricing is None, cost is 0 (unpriced model).
        """
        if not pricing:
            return Decimal("0")

        million = Decimal("1000000")
        input_rate = pricing.input_price or Decimal("0")
        cached_rate = (
            pricing.cached_input_price
            if pricing.cached_input_price is not None
            else input_rate
        )
        output_rate = pricing.output_price or Decimal("0")

        # Clamp cached_tokens to not exceed prompt_tokens
        safe_cached = min(cached_tokens, prompt_tokens)
        regular_input = prompt_tokens - safe_cached

        cost = (
            Decimal(regular_input) * input_rate / million
            + Decimal(safe_cached) * cached_rate / million
            + Decimal(completion_tokens) * output_rate / million
        )
        return cost.quantize(Decimal("0.000001"))

    # ------------------------------------------------------------------
    # Usage recording
    # ------------------------------------------------------------------

    async def record_usage(
        self,
        purpose: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_tokens: int = 0,
        user_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a single LLM usage event. Fire-and-forget with own session.

        Never raises — failures are logged at debug level.
        """
        try:
            pricing = await self.get_active_pricing(model)
            cost = self.calculate_cost(
                pricing, prompt_tokens, completion_tokens, cached_tokens
            )

            record = LlmUsageRecord(
                model=model,
                purpose=purpose,
                user_id=user_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                cost_usd=cost,
                metadata_=metadata,
                pricing_id=pricing.id if pricing else None,
            )

            async with get_async_session() as db:
                db.add(record)
                await db.commit()

        except Exception:
            logger.warning("Failed to record LLM usage", exc_info=True)

    # ------------------------------------------------------------------
    # Aggregation queries
    # ------------------------------------------------------------------

    def _time_filter(
        self,
        days: int = 7,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        """Build a WHERE clause for created_at time range.

        If start_date/end_date are provided (ISO date strings), use them.
        Otherwise fall back to ``days`` relative to now.
        """
        if start_date:
            start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        else:
            start_dt = datetime.now(timezone.utc) - timedelta(days=days)

        conditions = [LlmUsageRecord.created_at >= start_dt]
        if end_date:
            # end_date is inclusive: add 1 day
            end_dt = (
                datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
                + timedelta(days=1)
            )
            conditions.append(LlmUsageRecord.created_at < end_dt)
        return and_(*conditions) if len(conditions) > 1 else conditions[0]

    async def get_cost_summary(
        self,
        db: AsyncSession,
        days: int = 7,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get cost summary with breakdowns by purpose and model."""
        time_cond = self._time_filter(days, start_date, end_date)

        # Totals
        totals = await db.execute(
            select(
                func.coalesce(func.sum(LlmUsageRecord.cost_usd), 0).label("cost"),
                func.coalesce(func.sum(LlmUsageRecord.prompt_tokens), 0).label("prompt"),
                func.coalesce(func.sum(LlmUsageRecord.completion_tokens), 0).label("completion"),
                func.coalesce(func.sum(LlmUsageRecord.cached_tokens), 0).label("cached"),
                func.coalesce(func.sum(LlmUsageRecord.total_tokens), 0).label("total"),
                func.count().label("calls"),
            )
            .where(time_cond)
        )
        t = totals.one()

        # By purpose
        by_purpose_q = await db.execute(
            select(
                LlmUsageRecord.purpose,
                func.sum(LlmUsageRecord.cost_usd).label("cost"),
                func.sum(LlmUsageRecord.prompt_tokens).label("prompt"),
                func.sum(LlmUsageRecord.completion_tokens).label("completion"),
                func.sum(LlmUsageRecord.cached_tokens).label("cached"),
                func.sum(LlmUsageRecord.total_tokens).label("total"),
                func.count().label("calls"),
            )
            .where(time_cond)
            .group_by(LlmUsageRecord.purpose)
            .order_by(desc("cost"))
        )
        by_purpose = [
            {
                "group": row.purpose,
                "costUsd": float(row.cost or 0),
                "promptTokens": int(row.prompt or 0),
                "completionTokens": int(row.completion or 0),
                "cachedTokens": int(row.cached or 0),
                "totalTokens": int(row.total or 0),
                "calls": int(row.calls),
            }
            for row in by_purpose_q.all()
        ]

        # By model
        by_model_q = await db.execute(
            select(
                LlmUsageRecord.model,
                func.sum(LlmUsageRecord.cost_usd).label("cost"),
                func.sum(LlmUsageRecord.prompt_tokens).label("prompt"),
                func.sum(LlmUsageRecord.completion_tokens).label("completion"),
                func.sum(LlmUsageRecord.cached_tokens).label("cached"),
                func.sum(LlmUsageRecord.total_tokens).label("total"),
                func.count().label("calls"),
            )
            .where(time_cond)
            .group_by(LlmUsageRecord.model)
            .order_by(desc("cost"))
        )
        by_model = [
            {
                "group": row.model,
                "costUsd": float(row.cost or 0),
                "promptTokens": int(row.prompt or 0),
                "completionTokens": int(row.completion or 0),
                "cachedTokens": int(row.cached or 0),
                "totalTokens": int(row.total or 0),
                "calls": int(row.calls),
            }
            for row in by_model_q.all()
        ]

        return {
            "periodDays": days,
            "totalCostUsd": float(t.cost or 0),
            "totalPromptTokens": int(t.prompt or 0),
            "totalCompletionTokens": int(t.completion or 0),
            "totalCachedTokens": int(t.cached or 0),
            "totalTokens": int(t.total or 0),
            "totalCalls": int(t.calls),
            "byPurpose": by_purpose,
            "byModel": by_model,
        }

    @staticmethod
    async def cleanup_old_records(
        db: AsyncSession, retention_days: int = 90,
    ) -> int:
        """Delete usage records older than retention_days. Returns deleted count."""
        from sqlalchemy import delete

        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        result = await db.execute(
            delete(LlmUsageRecord).where(LlmUsageRecord.created_at < cutoff)
        )
        return result.rowcount  # type: ignore[return-value]

    async def get_daily_costs(
        self,
        db: AsyncSession,
        days: int = 30,
        purpose: Optional[str] = None,
        model: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get daily cost breakdown for charts."""
        time_cond = self._time_filter(days, start_date, end_date)

        day_col = cast(LlmUsageRecord.created_at, SADate).label("day")
        query = (
            select(
                day_col,
                func.sum(LlmUsageRecord.cost_usd).label("cost"),
                func.sum(LlmUsageRecord.prompt_tokens).label("prompt"),
                func.sum(LlmUsageRecord.completion_tokens).label("completion"),
                func.sum(LlmUsageRecord.cached_tokens).label("cached"),
                func.count().label("calls"),
            )
            .where(time_cond)
            .group_by(day_col)
            .order_by(day_col)
        )

        if purpose:
            query = query.where(LlmUsageRecord.purpose == purpose)
        if model:
            query = query.where(LlmUsageRecord.model == model)

        result = await db.execute(query)
        return [
            {
                "date": row.day.isoformat(),
                "costUsd": float(row.cost or 0),
                "promptTokens": int(row.prompt or 0),
                "completionTokens": int(row.completion or 0),
                "cachedTokens": int(row.cached or 0),
                "calls": int(row.calls),
            }
            for row in result.all()
        ]

    async def get_category_breakdown(
        self,
        db: AsyncSession,
        days: int = 7,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get usage breakdown with sub-group detail from JSONB metadata.

        Extracts agent/sub-type from metadata for purposes that have it:
        - analysis → metadata_->>'agent_type' (fundamental/technical/etc.)
        - layer1_scoring → metadata_->>'agent' (macro/market/signal)
        - layer3_analysis/layer3_lightweight/deep_filter → metadata_->>'agent'

        Returns flat list of {purpose, subGroup, costUsd, tokens, calls}.
        Frontend maps purposes into display categories.
        """
        time_cond = self._time_filter(days, start_date, end_date)

        # Build CASE expression to extract sub-group from JSONB metadata
        sub_group_expr = case(
            (
                LlmUsageRecord.purpose == "analysis",
                LlmUsageRecord.metadata_["agent_type"].astext,
            ),
            (
                LlmUsageRecord.purpose == "layer1_scoring",
                LlmUsageRecord.metadata_["agent"].astext,
            ),
            (
                LlmUsageRecord.purpose.in_(
                    ["layer3_analysis", "layer3_lightweight", "deep_filter"]
                ),
                LlmUsageRecord.metadata_["agent"].astext,
            ),
            else_=literal_column("''"),
        ).label("sub_group")

        result = await db.execute(
            select(
                LlmUsageRecord.purpose,
                sub_group_expr,
                func.sum(LlmUsageRecord.cost_usd).label("cost"),
                func.sum(LlmUsageRecord.prompt_tokens).label("prompt"),
                func.sum(LlmUsageRecord.completion_tokens).label("completion"),
                func.sum(LlmUsageRecord.cached_tokens).label("cached"),
                func.sum(LlmUsageRecord.total_tokens).label("total"),
                func.count().label("calls"),
            )
            .where(time_cond)
            .group_by(LlmUsageRecord.purpose, sub_group_expr)
            .order_by(LlmUsageRecord.purpose, desc("cost"))
        )

        return [
            {
                "purpose": row.purpose,
                "subGroup": row.sub_group or "",
                "costUsd": float(row.cost or 0),
                "promptTokens": int(row.prompt or 0),
                "completionTokens": int(row.completion or 0),
                "cachedTokens": int(row.cached or 0),
                "totalTokens": int(row.total or 0),
                "calls": int(row.calls),
            }
            for row in result.all()
        ]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_service: Optional[LlmCostService] = None


def get_llm_cost_service() -> LlmCostService:
    """Get singleton instance of LlmCostService."""
    global _service
    if _service is None:
        _service = LlmCostService()
    return _service
