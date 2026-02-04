"""Price Alert API endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user
from app.db.database import get_db
from app.models.alert import PriceAlert, PushSubscription
from app.models.user import User
from app.schemas.alert import (
    MessageResponse,
    PriceAlertCreate,
    PriceAlertListResponse,
    PriceAlertResponse,
    PriceAlertUpdate,
    PriceAlertWithPrice,
    PushSubscriptionCreate,
    PushSubscriptionListResponse,
    PushSubscriptionResponse,
    VAPIDKeysResponse,
)
from app.services.alert_service import AlertService
from app.services.notification import get_notification_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["Alerts"])


async def get_alert_or_404(
    alert_id: str,
    user_id: int,
    db: AsyncSession,
) -> PriceAlert:
    """Get alert by ID or raise 404."""
    service = AlertService(db)
    alert = await service.get_alert_by_id(alert_id, user_id)

    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Alert not found",
        )

    return alert


# ============== Price Alert Endpoints ==============


@router.get(
    "",
    response_model=PriceAlertListResponse,
    summary="List user alerts",
    description="Get all active price alerts for the current user.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def list_alerts(
    include_triggered: bool = Query(False, description="Include triggered alerts"),
    include_inactive: bool = Query(False, description="Include inactive alerts"),
    with_prices: bool = Query(False, description="Include current price information"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all price alerts for the current user.

    - **include_triggered**: Include alerts that have already triggered
    - **include_inactive**: Include deactivated alerts
    - **with_prices**: Fetch current prices and show distance to threshold
    """
    service = AlertService(db)

    if with_prices:
        alerts = await service.get_alerts_with_prices(current_user.id)
        return PriceAlertListResponse(alerts=alerts, total=len(alerts))

    alerts = await service.get_user_alerts(
        current_user.id,
        include_triggered=include_triggered,
        include_inactive=include_inactive,
    )

    alert_responses = [
        PriceAlertResponse(
            id=a.id,
            user_id=a.user_id,
            symbol=a.symbol,
            condition_type=a.condition_type,
            threshold=a.threshold,
            note=a.note,
            is_active=a.is_active,
            is_triggered=a.is_triggered,
            triggered_at=a.triggered_at,
            created_at=a.created_at,
            updated_at=a.updated_at,
        )
        for a in alerts
    ]

    return PriceAlertListResponse(alerts=alert_responses, total=len(alert_responses))


@router.post(
    "",
    response_model=PriceAlertResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create alert",
    description="Create a new price alert.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def create_alert(
    data: PriceAlertCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new price alert.

    - **symbol**: Stock symbol (e.g., AAPL, 0700.HK, 600519.SS)
    - **condition_type**: Alert condition (above, below, change_percent)
    - **threshold**: Price threshold or percentage change to trigger alert
    - **note**: Optional note for the alert

    Maximum 50 alerts per user.
    """
    service = AlertService(db)

    try:
        alert = await service.create_alert(current_user.id, data)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return PriceAlertResponse(
        id=alert.id,
        user_id=alert.user_id,
        symbol=alert.symbol,
        condition_type=alert.condition_type,
        threshold=alert.threshold,
        note=alert.note,
        is_active=alert.is_active,
        is_triggered=alert.is_triggered,
        triggered_at=alert.triggered_at,
        created_at=alert.created_at,
        updated_at=alert.updated_at,
    )


@router.get(
    "/{alert_id}",
    response_model=PriceAlertWithPrice,
    summary="Get alert detail",
    description="Get a specific price alert with current price information.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def get_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get a specific price alert with current price information.

    - **alert_id**: UUID of the alert
    """
    alert = await get_alert_or_404(alert_id, current_user.id, db)

    # Get current price
    from app.services.stock_service import get_stock_service

    stock_service = await get_stock_service()
    quote = await stock_service.get_quote(alert.symbol)

    current_price = quote.get("price") if quote else None
    price_distance = None
    price_distance_percent = None

    if current_price is not None:
        threshold_float = float(alert.threshold)
        if alert.condition_type == "above":
            price_distance = threshold_float - current_price
        elif alert.condition_type == "below":
            price_distance = current_price - threshold_float
        else:
            current_change = quote.get("change_percent", 0) if quote else 0
            price_distance = abs(threshold_float) - abs(current_change)

        if current_price > 0:
            price_distance_percent = (price_distance / current_price) * 100

    return PriceAlertWithPrice(
        id=alert.id,
        user_id=alert.user_id,
        symbol=alert.symbol,
        condition_type=alert.condition_type,
        threshold=alert.threshold,
        note=alert.note,
        is_active=alert.is_active,
        is_triggered=alert.is_triggered,
        triggered_at=alert.triggered_at,
        created_at=alert.created_at,
        updated_at=alert.updated_at,
        current_price=current_price,
        price_distance=round(price_distance, 4) if price_distance else None,
        price_distance_percent=(
            round(price_distance_percent, 2) if price_distance_percent else None
        ),
    )


@router.put(
    "/{alert_id}",
    response_model=PriceAlertResponse,
    summary="Update alert",
    description="Update a price alert's threshold, status, or note.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def update_alert(
    alert_id: str,
    data: PriceAlertUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update a price alert.

    - **alert_id**: UUID of the alert
    - **threshold**: New threshold value (optional)
    - **is_active**: Enable/disable the alert (optional)
    - **note**: Update note (optional)
    """
    alert = await get_alert_or_404(alert_id, current_user.id, db)

    service = AlertService(db)
    alert = await service.update_alert(alert, data)

    return PriceAlertResponse(
        id=alert.id,
        user_id=alert.user_id,
        symbol=alert.symbol,
        condition_type=alert.condition_type,
        threshold=alert.threshold,
        note=alert.note,
        is_active=alert.is_active,
        is_triggered=alert.is_triggered,
        triggered_at=alert.triggered_at,
        created_at=alert.created_at,
        updated_at=alert.updated_at,
    )


@router.delete(
    "/{alert_id}",
    response_model=MessageResponse,
    summary="Delete alert",
    description="Delete a price alert.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def delete_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a price alert.

    - **alert_id**: UUID of the alert
    """
    alert = await get_alert_or_404(alert_id, current_user.id, db)

    service = AlertService(db)
    await service.delete_alert(alert)

    return MessageResponse(message="Alert deleted successfully")


@router.post(
    "/{alert_id}/reset",
    response_model=PriceAlertResponse,
    summary="Reset triggered alert",
    description="Reset a triggered alert so it can fire again.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def reset_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Reset a triggered alert.

    This clears the triggered status and re-activates the alert
    so it can fire again when conditions are met.

    - **alert_id**: UUID of the alert
    """
    alert = await get_alert_or_404(alert_id, current_user.id, db)

    if not alert.is_triggered:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Alert has not been triggered",
        )

    service = AlertService(db)
    alert = await service.reset_alert(alert)

    return PriceAlertResponse(
        id=alert.id,
        user_id=alert.user_id,
        symbol=alert.symbol,
        condition_type=alert.condition_type,
        threshold=alert.threshold,
        note=alert.note,
        is_active=alert.is_active,
        is_triggered=alert.is_triggered,
        triggered_at=alert.triggered_at,
        created_at=alert.created_at,
        updated_at=alert.updated_at,
    )


@router.post(
    "/{alert_id}/toggle",
    response_model=PriceAlertResponse,
    summary="Toggle alert status",
    description="Toggle the active status of a price alert.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def toggle_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Toggle the active status of an alert.

    This enables/disables the alert by toggling its is_active field.

    - **alert_id**: UUID of the alert
    """
    alert = await get_alert_or_404(alert_id, current_user.id, db)

    service = AlertService(db)
    alert = await service.toggle_alert(alert)

    return PriceAlertResponse(
        id=alert.id,
        user_id=alert.user_id,
        symbol=alert.symbol,
        condition_type=alert.condition_type,
        threshold=alert.threshold,
        note=alert.note,
        is_active=alert.is_active,
        is_triggered=alert.is_triggered,
        triggered_at=alert.triggered_at,
        created_at=alert.created_at,
        updated_at=alert.updated_at,
    )


# ============== Push Subscription Endpoints ==============


push_router = APIRouter(prefix="/push", tags=["Push Notifications"])


@push_router.get(
    "/vapid-key",
    response_model=VAPIDKeysResponse,
    summary="Get VAPID public key",
    description="Get the VAPID public key for push subscription.",
)
async def get_vapid_public_key():
    """
    Get the VAPID public key for client-side push subscription.

    The client uses this key when calling PushManager.subscribe().
    """
    notification_service = get_notification_service()
    public_key = notification_service.get_vapid_public_key()

    if not public_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Web push notifications are not configured",
        )

    return VAPIDKeysResponse(public_key=public_key)


@push_router.get(
    "/subscriptions",
    response_model=PushSubscriptionListResponse,
    summary="List push subscriptions",
    description="Get all active push subscriptions for the current user.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def list_subscriptions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all active push subscriptions for the current user.
    """
    service = AlertService(db)
    subscriptions = await service.get_user_subscriptions(current_user.id)

    sub_responses = [
        PushSubscriptionResponse(
            id=s.id,
            user_id=s.user_id,
            endpoint=s.endpoint,
            is_active=s.is_active,
            created_at=s.created_at,
        )
        for s in subscriptions
    ]

    return PushSubscriptionListResponse(
        subscriptions=sub_responses,
        total=len(sub_responses),
    )


@push_router.post(
    "/subscriptions",
    response_model=PushSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Subscribe to push",
    description="Register a push subscription for notifications.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def create_subscription(
    data: PushSubscriptionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Register a push subscription for notifications.

    - **endpoint**: Push service endpoint URL
    - **keys**: Subscription keys (p256dh and auth)
    - **user_agent**: Optional user agent for debugging

    The subscription info should come from browser's PushManager.subscribe().
    """
    service = AlertService(db)
    subscription = await service.create_subscription(
        user_id=current_user.id,
        endpoint=data.endpoint,
        p256dh_key=data.keys.p256dh,
        auth_key=data.keys.auth,
        user_agent=data.user_agent,
    )

    return PushSubscriptionResponse(
        id=subscription.id,
        user_id=subscription.user_id,
        endpoint=subscription.endpoint,
        is_active=subscription.is_active,
        created_at=subscription.created_at,
    )


@push_router.delete(
    "/subscriptions/{subscription_id}",
    response_model=MessageResponse,
    summary="Unsubscribe from push",
    description="Remove a push subscription.",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def delete_subscription(
    subscription_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Remove a push subscription.

    - **subscription_id**: UUID of the subscription
    """
    service = AlertService(db)
    subscription = await service.get_subscription_by_id(
        subscription_id, current_user.id
    )

    if subscription is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )

    await service.delete_subscription(subscription)

    return MessageResponse(message="Subscription removed successfully")
