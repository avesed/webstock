"""Watchlist API endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.models.watchlist import Watchlist, WatchlistItem
from app.schemas.watchlist import (
    MessageResponse,
    WatchlistCreate,
    WatchlistDetailResponse,
    WatchlistDetailWithQuotes,
    WatchlistItemCreate,
    WatchlistItemResponse,
    WatchlistItemUpdate,
    WatchlistItemWithQuote,
    WatchlistListResponse,
    WatchlistResponse,
    WatchlistUpdate,
)
from app.services.stock_service import get_stock_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/watchlists", tags=["Watchlists"])


async def get_watchlist_or_404(
    watchlist_id: int,
    user_id: int,
    db: AsyncSession,
    load_items: bool = False,
) -> Watchlist:
    """Get watchlist by ID or raise 404."""
    query = select(Watchlist).where(
        Watchlist.id == watchlist_id,
        Watchlist.user_id == user_id,
    )
    if load_items:
        query = query.options(selectinload(Watchlist.items))

    result = await db.execute(query)
    watchlist = result.scalar_one_or_none()

    if watchlist is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Watchlist not found",
        )

    return watchlist


@router.get(
    "",
    response_model=WatchlistListResponse,
    summary="Get user's watchlists",
    description="Get all watchlists for the current user.",
)
async def get_watchlists(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all watchlists for the current user.

    Returns list of watchlists with item counts.
    """
    # Query watchlists with item counts
    query = (
        select(
            Watchlist,
            func.count(WatchlistItem.id).label("item_count"),
        )
        .outerjoin(WatchlistItem)
        .where(Watchlist.user_id == current_user.id)
        .group_by(Watchlist.id)
        .order_by(Watchlist.is_default.desc(), Watchlist.created_at)
    )

    result = await db.execute(query)
    rows = result.all()

    watchlists = []
    for watchlist, item_count in rows:
        watchlist_dict = {
            "id": watchlist.id,
            "user_id": watchlist.user_id,
            "name": watchlist.name,
            "description": watchlist.description,
            "is_default": watchlist.is_default,
            "created_at": watchlist.created_at,
            "updated_at": watchlist.updated_at,
            "item_count": item_count,
        }
        watchlists.append(WatchlistResponse(**watchlist_dict))

    return WatchlistListResponse(watchlists=watchlists, total=len(watchlists))


@router.post(
    "",
    response_model=WatchlistResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create watchlist",
    description="Create a new watchlist for the current user.",
)
async def create_watchlist(
    data: WatchlistCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new watchlist.

    - **name**: Watchlist name (required)
    - **description**: Optional description
    """
    # Check if this is the user's first watchlist (make it default)
    count_query = select(func.count(Watchlist.id)).where(
        Watchlist.user_id == current_user.id
    )
    result = await db.execute(count_query)
    existing_count = result.scalar()

    watchlist = Watchlist(
        user_id=current_user.id,
        name=data.name,
        description=data.description,
        is_default=existing_count == 0,  # First watchlist is default
    )

    db.add(watchlist)
    await db.commit()
    await db.refresh(watchlist)

    logger.info(f"Created watchlist {watchlist.id} for user {current_user.id}")

    return WatchlistResponse(
        id=watchlist.id,
        user_id=watchlist.user_id,
        name=watchlist.name,
        description=watchlist.description,
        is_default=watchlist.is_default,
        created_at=watchlist.created_at,
        updated_at=watchlist.updated_at,
        item_count=0,
    )


@router.get(
    "/{watchlist_id}",
    response_model=WatchlistDetailResponse,
    summary="Get watchlist details",
    description="Get a watchlist with all its items.",
)
async def get_watchlist(
    watchlist_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get a watchlist with all its items.

    - **watchlist_id**: ID of the watchlist
    """
    watchlist = await get_watchlist_or_404(
        watchlist_id, current_user.id, db, load_items=True
    )

    return WatchlistDetailResponse(
        id=watchlist.id,
        user_id=watchlist.user_id,
        name=watchlist.name,
        description=watchlist.description,
        is_default=watchlist.is_default,
        created_at=watchlist.created_at,
        updated_at=watchlist.updated_at,
        items=[WatchlistItemResponse.model_validate(item) for item in watchlist.items],
    )


@router.get(
    "/{watchlist_id}/quotes",
    response_model=WatchlistDetailWithQuotes,
    summary="Get watchlist with live quotes",
    description="Get a watchlist with items and their current prices.",
)
async def get_watchlist_with_quotes(
    watchlist_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get a watchlist with items and their current market quotes.

    This endpoint fetches live price data for all items in the watchlist.
    """
    watchlist = await get_watchlist_or_404(
        watchlist_id, current_user.id, db, load_items=True
    )

    # Get quotes for all symbols
    symbols = [item.symbol for item in watchlist.items]
    quotes = {}

    if symbols:
        stock_service = await get_stock_service()
        quotes = await stock_service.get_batch_quotes(symbols)

    # Build items with quotes
    items_with_quotes = []
    for item in watchlist.items:
        quote = quotes.get(item.symbol)
        item_dict = {
            "id": item.id,
            "watchlist_id": item.watchlist_id,
            "symbol": item.symbol,
            "notes": item.notes,
            "alert_price_above": item.alert_price_above,
            "alert_price_below": item.alert_price_below,
            "added_at": item.added_at,
            "name": quote.get("name") if quote else None,
            "price": quote.get("price") if quote else None,
            "change": quote.get("change") if quote else None,
            "change_percent": quote.get("change_percent") if quote else None,
            "volume": quote.get("volume") if quote else None,
        }
        items_with_quotes.append(WatchlistItemWithQuote(**item_dict))

    return WatchlistDetailWithQuotes(
        id=watchlist.id,
        user_id=watchlist.user_id,
        name=watchlist.name,
        description=watchlist.description,
        is_default=watchlist.is_default,
        created_at=watchlist.created_at,
        updated_at=watchlist.updated_at,
        items=items_with_quotes,
    )


@router.put(
    "/{watchlist_id}",
    response_model=WatchlistResponse,
    summary="Update watchlist",
    description="Update a watchlist's name or description.",
)
async def update_watchlist(
    watchlist_id: int,
    data: WatchlistUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update a watchlist.

    - **watchlist_id**: ID of the watchlist
    - **name**: New name (optional)
    - **description**: New description (optional)
    """
    watchlist = await get_watchlist_or_404(watchlist_id, current_user.id, db)

    if data.name is not None:
        watchlist.name = data.name
    if data.description is not None:
        watchlist.description = data.description

    await db.commit()
    await db.refresh(watchlist)

    # Get item count
    count_query = select(func.count(WatchlistItem.id)).where(
        WatchlistItem.watchlist_id == watchlist.id
    )
    result = await db.execute(count_query)
    item_count = result.scalar()

    logger.info(f"Updated watchlist {watchlist_id}")

    return WatchlistResponse(
        id=watchlist.id,
        user_id=watchlist.user_id,
        name=watchlist.name,
        description=watchlist.description,
        is_default=watchlist.is_default,
        created_at=watchlist.created_at,
        updated_at=watchlist.updated_at,
        item_count=item_count,
    )


@router.delete(
    "/{watchlist_id}",
    response_model=MessageResponse,
    summary="Delete watchlist",
    description="Delete a watchlist and all its items.",
)
async def delete_watchlist(
    watchlist_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a watchlist.

    - **watchlist_id**: ID of the watchlist

    Note: The default watchlist cannot be deleted if it's the only one.
    """
    watchlist = await get_watchlist_or_404(watchlist_id, current_user.id, db)

    # Check if this is the only watchlist
    count_query = select(func.count(Watchlist.id)).where(
        Watchlist.user_id == current_user.id
    )
    result = await db.execute(count_query)
    watchlist_count = result.scalar()

    if watchlist.is_default and watchlist_count == 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the only watchlist. Create another one first.",
        )

    # If deleting default watchlist, make another one default
    if watchlist.is_default:
        other_query = (
            select(Watchlist)
            .where(
                Watchlist.user_id == current_user.id,
                Watchlist.id != watchlist_id,
            )
            .limit(1)
        )
        result = await db.execute(other_query)
        other_watchlist = result.scalar_one_or_none()
        if other_watchlist:
            other_watchlist.is_default = True

    await db.delete(watchlist)
    await db.commit()

    logger.info(f"Deleted watchlist {watchlist_id}")

    return MessageResponse(message="Watchlist deleted successfully")


@router.post(
    "/{watchlist_id}/items",
    response_model=WatchlistItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add stock to watchlist",
    description="Add a stock symbol to a watchlist.",
)
async def add_watchlist_item(
    watchlist_id: int,
    data: WatchlistItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add a stock to a watchlist.

    - **watchlist_id**: ID of the watchlist
    - **symbol**: Stock symbol to add
    - **notes**: Optional notes about this stock
    - **alert_price_above**: Optional price alert threshold (above)
    - **alert_price_below**: Optional price alert threshold (below)
    """
    watchlist = await get_watchlist_or_404(watchlist_id, current_user.id, db)

    # Normalize symbol
    symbol = data.symbol.strip().upper()

    # Check if symbol already exists in watchlist
    existing_query = select(WatchlistItem).where(
        WatchlistItem.watchlist_id == watchlist_id,
        WatchlistItem.symbol == symbol,
    )
    result = await db.execute(existing_query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Symbol {symbol} is already in this watchlist",
        )

    # Optionally validate symbol exists by fetching quote
    # (skip for now to avoid blocking on API calls)

    item = WatchlistItem(
        watchlist_id=watchlist_id,
        symbol=symbol,
        notes=data.notes,
        alert_price_above=data.alert_price_above,
        alert_price_below=data.alert_price_below,
    )

    db.add(item)
    await db.commit()
    await db.refresh(item)

    logger.info(f"Added {symbol} to watchlist {watchlist_id}")

    return WatchlistItemResponse.model_validate(item)


@router.put(
    "/{watchlist_id}/items/{symbol}",
    response_model=WatchlistItemResponse,
    summary="Update watchlist item",
    description="Update notes or alerts for a stock in a watchlist.",
)
async def update_watchlist_item(
    watchlist_id: int,
    symbol: str,
    data: WatchlistItemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update a stock item in a watchlist.

    - **watchlist_id**: ID of the watchlist
    - **symbol**: Stock symbol to update
    - **notes**: New notes (optional)
    - **alert_price_above**: New price alert threshold (above, optional)
    - **alert_price_below**: New price alert threshold (below, optional)
    """
    await get_watchlist_or_404(watchlist_id, current_user.id, db)

    symbol = symbol.strip().upper()

    # Get the item
    item_query = select(WatchlistItem).where(
        WatchlistItem.watchlist_id == watchlist_id,
        WatchlistItem.symbol == symbol,
    )
    result = await db.execute(item_query)
    item = result.scalar_one_or_none()

    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Symbol {symbol} not found in this watchlist",
        )

    if data.notes is not None:
        item.notes = data.notes
    if data.alert_price_above is not None:
        item.alert_price_above = data.alert_price_above
    if data.alert_price_below is not None:
        item.alert_price_below = data.alert_price_below

    await db.commit()
    await db.refresh(item)

    logger.info(f"Updated {symbol} in watchlist {watchlist_id}")

    return WatchlistItemResponse.model_validate(item)


@router.delete(
    "/{watchlist_id}/items/{symbol}",
    response_model=MessageResponse,
    summary="Remove stock from watchlist",
    description="Remove a stock symbol from a watchlist.",
)
async def remove_watchlist_item(
    watchlist_id: int,
    symbol: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Remove a stock from a watchlist.

    - **watchlist_id**: ID of the watchlist
    - **symbol**: Stock symbol to remove
    """
    await get_watchlist_or_404(watchlist_id, current_user.id, db)

    symbol = symbol.strip().upper()

    # Delete the item
    delete_query = delete(WatchlistItem).where(
        WatchlistItem.watchlist_id == watchlist_id,
        WatchlistItem.symbol == symbol,
    )
    result = await db.execute(delete_query)

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Symbol {symbol} not found in this watchlist",
        )

    await db.commit()

    logger.info(f"Removed {symbol} from watchlist {watchlist_id}")

    return MessageResponse(message=f"Removed {symbol} from watchlist")


@router.post(
    "/{watchlist_id}/set-default",
    response_model=WatchlistResponse,
    summary="Set as default watchlist",
    description="Set a watchlist as the user's default.",
)
async def set_default_watchlist(
    watchlist_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Set a watchlist as the default.

    - **watchlist_id**: ID of the watchlist to make default
    """
    watchlist = await get_watchlist_or_404(watchlist_id, current_user.id, db)

    if watchlist.is_default:
        # Already default, get item count and return
        count_query = select(func.count(WatchlistItem.id)).where(
            WatchlistItem.watchlist_id == watchlist.id
        )
        result = await db.execute(count_query)
        item_count = result.scalar()

        return WatchlistResponse(
            id=watchlist.id,
            user_id=watchlist.user_id,
            name=watchlist.name,
            description=watchlist.description,
            is_default=watchlist.is_default,
            created_at=watchlist.created_at,
            updated_at=watchlist.updated_at,
            item_count=item_count,
        )

    # Clear default from other watchlists
    clear_query = (
        select(Watchlist)
        .where(
            Watchlist.user_id == current_user.id,
            Watchlist.is_default == True,
        )
    )
    result = await db.execute(clear_query)
    old_default = result.scalar_one_or_none()
    if old_default:
        old_default.is_default = False

    # Set new default
    watchlist.is_default = True

    await db.commit()
    await db.refresh(watchlist)

    # Get item count
    count_query = select(func.count(WatchlistItem.id)).where(
        WatchlistItem.watchlist_id == watchlist.id
    )
    result = await db.execute(count_query)
    item_count = result.scalar()

    logger.info(f"Set watchlist {watchlist_id} as default for user {current_user.id}")

    return WatchlistResponse(
        id=watchlist.id,
        user_id=watchlist.user_id,
        name=watchlist.name,
        description=watchlist.description,
        is_default=watchlist.is_default,
        created_at=watchlist.created_at,
        updated_at=watchlist.updated_at,
        item_count=item_count,
    )
