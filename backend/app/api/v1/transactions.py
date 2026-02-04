"""Transaction API endpoints."""

import logging
import math
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.portfolio import (
    MessageResponse,
    TransactionCreate,
    TransactionListResponse,
    TransactionResponse,
    TransactionTypeEnum,
)
from app.services.portfolio_service import PortfolioService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portfolios/{portfolio_id}/transactions", tags=["Transactions"])


async def get_portfolio_or_404(
    portfolio_id: str,
    user_id: int,
    db: AsyncSession,
):
    """Get portfolio by ID or raise 404."""
    service = PortfolioService(db)
    portfolio = await service.get_portfolio_by_id(portfolio_id, user_id)

    if portfolio is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Portfolio not found",
        )

    return portfolio


@router.post(
    "",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create transaction",
    description="Record a new transaction (buy, sell, or dividend).",
    dependencies=[Depends(rate_limit(max_requests=200, window_seconds=60))],
)
async def create_transaction(
    portfolio_id: str,
    data: TransactionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Record a new transaction.

    - **symbol**: Stock symbol (e.g., AAPL, 0700.HK)
    - **type**: Transaction type (buy, sell, dividend)
    - **quantity**: Number of shares
    - **price**: Price per share
    - **fee**: Transaction fee (default: 0)
    - **date**: Transaction date
    - **notes**: Optional notes

    For BUY transactions:
    - Adds shares to holdings
    - Recalculates average cost

    For SELL transactions:
    - Reduces shares from holdings
    - Validates sufficient quantity exists
    - Average cost remains unchanged

    For DIVIDEND transactions:
    - Records the dividend income
    - No change to holdings
    """
    await get_portfolio_or_404(portfolio_id, current_user.id, db)

    service = PortfolioService(db)

    try:
        transaction = await service.create_transaction(portfolio_id, data)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return TransactionResponse(
        id=transaction.id,
        portfolio_id=transaction.portfolio_id,
        symbol=transaction.symbol,
        type=TransactionTypeEnum(transaction.type),
        quantity=transaction.quantity,
        price=transaction.price,
        fee=transaction.fee,
        total=transaction.total,
        date=transaction.date,
        notes=transaction.notes,
        created_at=transaction.created_at,
    )


@router.get(
    "",
    response_model=TransactionListResponse,
    summary="List transactions",
    description="Get all transactions in a portfolio with pagination and filters.",
    dependencies=[Depends(rate_limit(max_requests=200, window_seconds=60))],
)
async def list_transactions(
    portfolio_id: str,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    type: Optional[TransactionTypeEnum] = Query(None, description="Filter by type"),
    start_date: Optional[datetime] = Query(None, description="Filter by start date"),
    end_date: Optional[datetime] = Query(None, description="Filter by end date"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all transactions in a portfolio.

    - **page**: Page number (default: 1)
    - **page_size**: Items per page (default: 20, max: 100)
    - **symbol**: Filter by stock symbol
    - **type**: Filter by transaction type (buy, sell, dividend)
    - **start_date**: Filter transactions on or after this date
    - **end_date**: Filter transactions on or before this date
    """
    await get_portfolio_or_404(portfolio_id, current_user.id, db)

    service = PortfolioService(db)
    transactions, total = await service.get_transactions(
        portfolio_id=portfolio_id,
        page=page,
        page_size=page_size,
        symbol=symbol,
        txn_type=type.value if type else None,
        start_date=start_date,
        end_date=end_date,
    )

    total_pages = math.ceil(total / page_size) if total > 0 else 1

    return TransactionListResponse(
        transactions=[
            TransactionResponse(
                id=txn.id,
                portfolio_id=txn.portfolio_id,
                symbol=txn.symbol,
                type=TransactionTypeEnum(txn.type),
                quantity=txn.quantity,
                price=txn.price,
                fee=txn.fee,
                total=txn.total,
                date=txn.date,
                notes=txn.notes,
                created_at=txn.created_at,
            )
            for txn in transactions
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/{transaction_id}",
    response_model=TransactionResponse,
    summary="Get transaction",
    description="Get details of a specific transaction.",
    dependencies=[Depends(rate_limit(max_requests=200, window_seconds=60))],
)
async def get_transaction(
    portfolio_id: str,
    transaction_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get details of a specific transaction.

    - **portfolio_id**: UUID of the portfolio
    - **transaction_id**: UUID of the transaction
    """
    await get_portfolio_or_404(portfolio_id, current_user.id, db)

    service = PortfolioService(db)
    transaction = await service.get_transaction_by_id(portfolio_id, transaction_id)

    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not found",
        )

    return TransactionResponse(
        id=transaction.id,
        portfolio_id=transaction.portfolio_id,
        symbol=transaction.symbol,
        type=TransactionTypeEnum(transaction.type),
        quantity=transaction.quantity,
        price=transaction.price,
        fee=transaction.fee,
        total=transaction.total,
        date=transaction.date,
        notes=transaction.notes,
        created_at=transaction.created_at,
    )


@router.delete(
    "/{transaction_id}",
    response_model=MessageResponse,
    summary="Delete transaction",
    description="Delete a transaction and recalculate holdings.",
    dependencies=[Depends(rate_limit(max_requests=200, window_seconds=60))],
)
async def delete_transaction(
    portfolio_id: str,
    transaction_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a transaction.

    - **portfolio_id**: UUID of the portfolio
    - **transaction_id**: UUID of the transaction

    Warning: This will recalculate holdings based on remaining transactions.
    The effect of the deleted transaction will be reversed:
    - Deleting a BUY: Reduces quantity
    - Deleting a SELL: Increases quantity
    """
    await get_portfolio_or_404(portfolio_id, current_user.id, db)

    service = PortfolioService(db)
    transaction = await service.get_transaction_by_id(portfolio_id, transaction_id)

    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not found",
        )

    await service.delete_transaction(transaction)

    return MessageResponse(message="Transaction deleted successfully")
