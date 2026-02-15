"""Admin LLM cost tracking and model pricing endpoints."""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import rate_limit
from app.core.security import require_admin
from app.db.database import get_db
from app.models.user import User
from app.schemas.admin import (
    CategoryBreakdownItem,
    CostSummaryResponse,
    DailyCostItem,
    ModelPricingCreate,
    ModelPricingResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin - LLM Costs"])


# ============== LLM Cost Tracking Endpoints ==============


@router.get(
    "/llm-costs/summary",
    response_model=CostSummaryResponse,
    summary="Get LLM cost summary",
    description="Get aggregated LLM cost summary with breakdowns by purpose and model.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_cost_summary(
    days: int = Query(default=7, ge=1, le=365),
    start_date: Optional[str] = Query(default=None, description="ISO date start"),
    end_date: Optional[str] = Query(default=None, description="ISO date end"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Get cost summary with breakdowns."""
    from app.services.llm_cost_service import get_llm_cost_service

    service = get_llm_cost_service()
    return await service.get_cost_summary(
        db, days=days, start_date=start_date, end_date=end_date,
    )


@router.get(
    "/llm-costs/daily",
    response_model=List[DailyCostItem],
    summary="Get daily LLM costs",
    description="Get daily cost data for charts with optional purpose/model filters.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_daily_costs(
    days: int = Query(default=30, ge=1, le=365),
    purpose: Optional[str] = Query(default=None),
    model: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None, description="ISO date start"),
    end_date: Optional[str] = Query(default=None, description="ISO date end"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Get daily cost data for charts."""
    from app.services.llm_cost_service import get_llm_cost_service

    service = get_llm_cost_service()
    return await service.get_daily_costs(
        db, days=days, purpose=purpose, model=model,
        start_date=start_date, end_date=end_date,
    )


@router.get(
    "/llm-costs/category-breakdown",
    response_model=List[CategoryBreakdownItem],
    summary="Get LLM cost category breakdown",
    description="Get usage breakdown with sub-group detail extracted from JSONB metadata.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_category_breakdown(
    days: int = Query(default=7, ge=1, le=365),
    start_date: Optional[str] = Query(default=None, description="ISO date start"),
    end_date: Optional[str] = Query(default=None, description="ISO date end"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Get category breakdown with sub-group detail from metadata."""
    from app.services.llm_cost_service import get_llm_cost_service

    service = get_llm_cost_service()
    return await service.get_category_breakdown(
        db, days=days, start_date=start_date, end_date=end_date,
    )


@router.get(
    "/model-pricing",
    response_model=List[ModelPricingResponse],
    summary="Get all model pricing",
    description="Get all model pricing rows for admin display.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def get_model_pricing(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Get all model pricing rows."""
    from app.services.llm_cost_service import get_llm_cost_service

    service = get_llm_cost_service()
    rows = await service.get_all_pricing(db)
    return [
        ModelPricingResponse(
            id=str(r.id),
            model=r.model,
            input_price=float(r.input_price or 0),
            cached_input_price=float(r.cached_input_price) if r.cached_input_price is not None else None,
            output_price=float(r.output_price or 0),
            effective_from=r.effective_from.isoformat(),
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
    ]


@router.post(
    "/model-pricing",
    response_model=ModelPricingResponse,
    status_code=201,
    summary="Create or update model pricing",
    description="Create or update model pricing for a given model and effective date.",
    dependencies=[Depends(rate_limit(max_requests=20, window_seconds=60))],
)
async def create_model_pricing(
    data: ModelPricingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Create or update model pricing."""
    from datetime import date
    from app.services.llm_cost_service import get_llm_cost_service

    service = get_llm_cost_service()
    eff_date = date.fromisoformat(data.effective_from) if data.effective_from else None
    row = await service.set_pricing(
        db,
        model=data.model,
        input_price=data.input_price,
        output_price=data.output_price,
        cached_input_price=data.cached_input_price,
        effective_from=eff_date,
    )
    await db.commit()
    return ModelPricingResponse(
        id=str(row.id),
        model=row.model,
        input_price=float(row.input_price or 0),
        cached_input_price=float(row.cached_input_price) if row.cached_input_price is not None else None,
        output_price=float(row.output_price or 0),
        effective_from=row.effective_from.isoformat(),
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


@router.delete(
    "/model-pricing/{pricing_id}",
    status_code=204,
    summary="Delete model pricing",
    description="Delete a model pricing row by ID.",
    dependencies=[Depends(rate_limit(max_requests=10, window_seconds=60))],
)
async def delete_model_pricing(
    pricing_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Delete a model pricing row."""
    from app.services.llm_cost_service import get_llm_cost_service

    service = get_llm_cost_service()
    deleted = await service.delete_pricing(db, pricing_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Pricing not found")
    await db.commit()
