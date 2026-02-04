"""Portfolio API endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user
from app.db.database import get_db
from app.models.portfolio import Holding, Portfolio
from app.models.user import User
from app.schemas.portfolio import (
    HoldingDetailResponse,
    HoldingsListResponse,
    HoldingWithQuote,
    MessageResponse,
    PortfolioCreate,
    PortfolioDetailResponse,
    PortfolioDetailWithQuotes,
    PortfolioListResponse,
    PortfolioResponse,
    PortfolioSummary,
    PortfolioUpdate,
)
from app.services.portfolio_service import PortfolioService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portfolios", tags=["Portfolios"])


async def get_portfolio_or_404(
    portfolio_id: str,
    user_id: int,
    db: AsyncSession,
    load_holdings: bool = False,
) -> Portfolio:
    """Get portfolio by ID or raise 404."""
    service = PortfolioService(db)
    portfolio = await service.get_portfolio_by_id(
        portfolio_id, user_id, load_holdings=load_holdings
    )

    if portfolio is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Portfolio not found",
        )

    return portfolio


# ============== Portfolio Endpoints ==============


@router.get(
    "",
    response_model=PortfolioListResponse,
    summary="List user portfolios",
    description="Get all portfolios for the current user with summary information.",
    dependencies=[Depends(rate_limit(max_requests=100, window_seconds=60))],
)
async def list_portfolios(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all portfolios for the current user.

    Returns list of portfolios with holdings count.
    """
    service = PortfolioService(db)
    results = await service.get_user_portfolios(current_user.id)

    portfolios = []
    for portfolio, holdings_count in results:
        portfolio_dict = {
            "id": portfolio.id,
            "user_id": portfolio.user_id,
            "name": portfolio.name,
            "description": portfolio.description,
            "currency": portfolio.currency,
            "is_default": portfolio.is_default,
            "created_at": portfolio.created_at,
            "updated_at": portfolio.updated_at,
            "holdings_count": holdings_count,
        }
        portfolios.append(PortfolioResponse(**portfolio_dict))

    return PortfolioListResponse(portfolios=portfolios, total=len(portfolios))


@router.post(
    "",
    response_model=PortfolioResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create portfolio",
    description="Create a new investment portfolio.",
    dependencies=[Depends(rate_limit(max_requests=100, window_seconds=60))],
)
async def create_portfolio(
    data: PortfolioCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new portfolio.

    - **name**: Portfolio name (required)
    - **description**: Optional description
    - **currency**: Currency code (USD, HKD, CNY)

    The first portfolio created will be set as default.
    """
    service = PortfolioService(db)
    portfolio = await service.create_portfolio(
        user_id=current_user.id,
        name=data.name,
        description=data.description,
        currency=data.currency.value,
    )

    return PortfolioResponse(
        id=portfolio.id,
        user_id=portfolio.user_id,
        name=portfolio.name,
        description=portfolio.description,
        currency=portfolio.currency,
        is_default=portfolio.is_default,
        created_at=portfolio.created_at,
        updated_at=portfolio.updated_at,
        holdings_count=0,
    )


@router.get(
    "/{portfolio_id}",
    response_model=PortfolioDetailResponse,
    summary="Get portfolio",
    description="Get a portfolio with its holdings (without live prices).",
    dependencies=[Depends(rate_limit(max_requests=100, window_seconds=60))],
)
async def get_portfolio(
    portfolio_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get a portfolio with its holdings.

    - **portfolio_id**: UUID of the portfolio
    """
    portfolio = await get_portfolio_or_404(
        portfolio_id, current_user.id, db, load_holdings=True
    )

    return PortfolioDetailResponse(
        id=portfolio.id,
        user_id=portfolio.user_id,
        name=portfolio.name,
        description=portfolio.description,
        currency=portfolio.currency,
        is_default=portfolio.is_default,
        created_at=portfolio.created_at,
        updated_at=portfolio.updated_at,
        holdings_count=len(portfolio.holdings),
        holdings=portfolio.holdings,
    )


@router.get(
    "/{portfolio_id}/summary",
    response_model=PortfolioSummary,
    summary="Get portfolio summary",
    description="Get portfolio summary with live prices and profit/loss calculations.",
    dependencies=[Depends(rate_limit(max_requests=100, window_seconds=60))],
)
async def get_portfolio_summary(
    portfolio_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get portfolio summary with live prices.

    This endpoint fetches real-time prices and calculates:
    - Total market value
    - Total profit/loss
    - Day change
    - Best/worst performers
    """
    portfolio = await get_portfolio_or_404(portfolio_id, current_user.id, db)

    service = PortfolioService(db)
    summary = await service.get_portfolio_summary(portfolio)

    return summary


@router.put(
    "/{portfolio_id}",
    response_model=PortfolioResponse,
    summary="Update portfolio",
    description="Update a portfolio's name, description, or currency.",
    dependencies=[Depends(rate_limit(max_requests=100, window_seconds=60))],
)
async def update_portfolio(
    portfolio_id: str,
    data: PortfolioUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update a portfolio.

    - **portfolio_id**: UUID of the portfolio
    - **name**: New name (optional)
    - **description**: New description (optional)
    - **currency**: New currency (optional)
    """
    portfolio = await get_portfolio_or_404(portfolio_id, current_user.id, db)

    service = PortfolioService(db)
    portfolio = await service.update_portfolio(
        portfolio,
        name=data.name,
        description=data.description,
        currency=data.currency.value if data.currency else None,
    )

    # Get holdings count
    count_query = select(func.count(Holding.id)).where(
        Holding.portfolio_id == portfolio.id
    )
    result = await db.execute(count_query)
    holdings_count = result.scalar()

    return PortfolioResponse(
        id=portfolio.id,
        user_id=portfolio.user_id,
        name=portfolio.name,
        description=portfolio.description,
        currency=portfolio.currency,
        is_default=portfolio.is_default,
        created_at=portfolio.created_at,
        updated_at=portfolio.updated_at,
        holdings_count=holdings_count,
    )


@router.delete(
    "/{portfolio_id}",
    response_model=MessageResponse,
    summary="Delete portfolio",
    description="Delete a portfolio and all its holdings and transactions.",
    dependencies=[Depends(rate_limit(max_requests=100, window_seconds=60))],
)
async def delete_portfolio(
    portfolio_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a portfolio.

    - **portfolio_id**: UUID of the portfolio

    Warning: This will delete all holdings and transactions in the portfolio.
    """
    portfolio = await get_portfolio_or_404(portfolio_id, current_user.id, db)

    # Check if this is the only portfolio
    count_query = select(func.count(Portfolio.id)).where(
        Portfolio.user_id == current_user.id
    )
    result = await db.execute(count_query)
    portfolio_count = result.scalar()

    if portfolio.is_default and portfolio_count == 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the only portfolio. Create another one first.",
        )

    service = PortfolioService(db)
    await service.delete_portfolio(portfolio)

    return MessageResponse(message="Portfolio deleted successfully")


@router.post(
    "/{portfolio_id}/set-default",
    response_model=PortfolioResponse,
    summary="Set default portfolio",
    description="Set a portfolio as the user's default.",
    dependencies=[Depends(rate_limit(max_requests=100, window_seconds=60))],
)
async def set_default_portfolio(
    portfolio_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Set a portfolio as the default.

    - **portfolio_id**: UUID of the portfolio to make default
    """
    portfolio = await get_portfolio_or_404(portfolio_id, current_user.id, db)

    service = PortfolioService(db)
    portfolio = await service.set_default_portfolio(portfolio, current_user.id)

    # Get holdings count
    count_query = select(func.count(Holding.id)).where(
        Holding.portfolio_id == portfolio.id
    )
    result = await db.execute(count_query)
    holdings_count = result.scalar()

    return PortfolioResponse(
        id=portfolio.id,
        user_id=portfolio.user_id,
        name=portfolio.name,
        description=portfolio.description,
        currency=portfolio.currency,
        is_default=portfolio.is_default,
        created_at=portfolio.created_at,
        updated_at=portfolio.updated_at,
        holdings_count=holdings_count,
    )


# ============== Holdings Endpoints ==============


@router.get(
    "/{portfolio_id}/holdings",
    response_model=HoldingsListResponse,
    summary="List holdings",
    description="Get all holdings in a portfolio with live price data.",
    dependencies=[Depends(rate_limit(max_requests=100, window_seconds=60))],
)
async def list_holdings(
    portfolio_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all holdings in a portfolio with live quotes.

    Returns holdings with current prices, market value, and profit/loss.
    """
    await get_portfolio_or_404(portfolio_id, current_user.id, db)

    service = PortfolioService(db)
    holdings = await service.get_holdings_with_quotes(portfolio_id)

    return HoldingsListResponse(holdings=holdings, total=len(holdings))


@router.get(
    "/{portfolio_id}/holdings/{symbol}",
    response_model=HoldingWithQuote,
    summary="Get holding detail",
    description="Get detailed information about a specific holding.",
    dependencies=[Depends(rate_limit(max_requests=100, window_seconds=60))],
)
async def get_holding_detail(
    portfolio_id: str,
    symbol: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get detailed information about a specific holding.

    - **portfolio_id**: UUID of the portfolio
    - **symbol**: Stock symbol
    """
    await get_portfolio_or_404(portfolio_id, current_user.id, db)

    service = PortfolioService(db)
    holding = await service.get_holding_detail(portfolio_id, symbol.upper())

    if holding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Holding {symbol} not found in this portfolio",
        )

    return holding
