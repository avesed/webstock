"""Alert service for price alert business logic."""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertConditionType, PriceAlert, PushSubscription
from app.models.user import User
from app.schemas.alert import (
    AlertConditionType as AlertConditionTypeSchema,
    PriceAlertCreate,
    PriceAlertUpdate,
    PriceAlertWithPrice,
)
from app.services.notification import get_notification_service, NotificationResult
from app.services.stock_service import get_stock_service

logger = logging.getLogger(__name__)

# Maximum alerts per user
MAX_ALERTS_PER_USER = 50


class AlertService:
    """Service for price alert operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ============== Price Alert Operations ==============

    async def get_user_alerts(
        self,
        user_id: int,
        include_triggered: bool = False,
        include_inactive: bool = False,
    ) -> List[PriceAlert]:
        """
        Get all alerts for a user.

        Args:
            user_id: User ID
            include_triggered: Include already triggered alerts
            include_inactive: Include inactive alerts

        Returns:
            List of price alerts
        """
        query = select(PriceAlert).where(PriceAlert.user_id == user_id)

        if not include_triggered:
            query = query.where(PriceAlert.is_triggered == False)

        if not include_inactive:
            query = query.where(PriceAlert.is_active == True)

        query = query.order_by(PriceAlert.created_at.desc())

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_alert_by_id(
        self, alert_id: str, user_id: int
    ) -> Optional[PriceAlert]:
        """Get an alert by ID, ensuring it belongs to the user."""
        query = select(PriceAlert).where(
            and_(
                PriceAlert.id == alert_id,
                PriceAlert.user_id == user_id,
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_user_alert_count(self, user_id: int) -> int:
        """Get the count of active alerts for a user."""
        query = select(func.count(PriceAlert.id)).where(
            and_(
                PriceAlert.user_id == user_id,
                PriceAlert.is_active == True,
            )
        )
        result = await self.db.execute(query)
        return result.scalar() or 0

    async def create_alert(
        self, user_id: int, data: PriceAlertCreate
    ) -> PriceAlert:
        """
        Create a new price alert.

        Args:
            user_id: User ID
            data: Alert creation data

        Returns:
            Created price alert

        Raises:
            ValueError: If user has reached max alerts
        """
        # Check alert limit
        alert_count = await self.get_user_alert_count(user_id)
        if alert_count >= MAX_ALERTS_PER_USER:
            raise ValueError(
                f"Maximum alert limit ({MAX_ALERTS_PER_USER}) reached. "
                "Please delete some alerts before creating new ones."
            )

        alert = PriceAlert(
            user_id=user_id,
            symbol=data.symbol.upper(),
            condition_type=data.condition_type.value,
            threshold=data.threshold,
            note=data.note,
        )

        self.db.add(alert)
        await self.db.commit()
        await self.db.refresh(alert)

        logger.info(
            f"Created alert {alert.id} for user {user_id}: "
            f"{data.symbol} {data.condition_type.value} {data.threshold}"
        )
        return alert

    async def update_alert(
        self, alert: PriceAlert, data: PriceAlertUpdate
    ) -> PriceAlert:
        """Update an existing alert."""
        if data.threshold is not None:
            alert.threshold = data.threshold
        if data.is_active is not None:
            alert.is_active = data.is_active
        if data.note is not None:
            alert.note = data.note

        await self.db.commit()
        await self.db.refresh(alert)

        logger.info(f"Updated alert {alert.id}")
        return alert

    async def delete_alert(self, alert: PriceAlert) -> None:
        """Delete an alert."""
        alert_id = alert.id
        await self.db.delete(alert)
        await self.db.commit()
        logger.info(f"Deleted alert {alert_id}")

    async def reset_alert(self, alert: PriceAlert) -> PriceAlert:
        """
        Reset a triggered alert so it can fire again.

        Clears the triggered status and timestamp.
        """
        alert.is_triggered = False
        alert.triggered_at = None
        alert.is_active = True

        await self.db.commit()
        await self.db.refresh(alert)

        logger.info(f"Reset alert {alert.id}")
        return alert

    async def toggle_alert(self, alert: PriceAlert) -> PriceAlert:
        """Toggle the active status of an alert."""
        alert.is_active = not alert.is_active
        await self.db.commit()
        await self.db.refresh(alert)
        logger.info(f"Toggled alert {alert.id} active status to {alert.is_active}")
        return alert

    async def get_alerts_with_prices(
        self, user_id: int
    ) -> List[PriceAlertWithPrice]:
        """
        Get user alerts with current price information.

        Fetches live prices and calculates distance to threshold.
        """
        alerts = await self.get_user_alerts(user_id, include_triggered=True)

        if not alerts:
            return []

        # Get unique symbols
        symbols = list(set(a.symbol for a in alerts))

        # Fetch current prices
        stock_service = await get_stock_service()
        quotes = await stock_service.get_batch_quotes(symbols)

        # Build response with price info
        result = []
        for alert in alerts:
            quote = quotes.get(alert.symbol)
            current_price = quote.get("price") if quote else None

            # Calculate distance to threshold
            price_distance = None
            price_distance_percent = None

            if current_price is not None:
                threshold_float = float(alert.threshold)
                if alert.condition_type == AlertConditionType.ABOVE.value:
                    price_distance = threshold_float - current_price
                    if current_price > 0:
                        price_distance_percent = (price_distance / current_price) * 100
                elif alert.condition_type == AlertConditionType.BELOW.value:
                    price_distance = current_price - threshold_float
                    if current_price > 0:
                        price_distance_percent = (price_distance / current_price) * 100
                else:  # change_percent - distance is current change vs threshold
                    current_change = quote.get("change_percent", 0) if quote else 0
                    price_distance = abs(threshold_float) - abs(current_change)

            result.append(
                PriceAlertWithPrice(
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
                        round(price_distance_percent, 2)
                        if price_distance_percent
                        else None
                    ),
                )
            )

        return result

    # ============== Push Subscription Operations ==============

    async def get_user_subscriptions(self, user_id: int) -> List[PushSubscription]:
        """Get all active push subscriptions for a user."""
        query = (
            select(PushSubscription)
            .where(
                and_(
                    PushSubscription.user_id == user_id,
                    PushSubscription.is_active == True,
                )
            )
            .order_by(PushSubscription.created_at.desc())
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_subscription_by_id(
        self, subscription_id: str, user_id: int
    ) -> Optional[PushSubscription]:
        """Get a subscription by ID."""
        query = select(PushSubscription).where(
            and_(
                PushSubscription.id == subscription_id,
                PushSubscription.user_id == user_id,
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def create_subscription(
        self,
        user_id: int,
        endpoint: str,
        p256dh_key: str,
        auth_key: str,
        user_agent: Optional[str] = None,
    ) -> PushSubscription:
        """
        Create or update a push subscription.

        If the endpoint already exists, update the keys.
        """
        # Check if subscription exists
        query = select(PushSubscription).where(
            PushSubscription.endpoint == endpoint
        )
        result = await self.db.execute(query)
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing subscription
            existing.p256dh_key = p256dh_key
            existing.auth_key = auth_key
            existing.user_id = user_id
            existing.is_active = True
            if user_agent:
                existing.user_agent = user_agent

            await self.db.commit()
            await self.db.refresh(existing)
            logger.info(f"Updated push subscription {existing.id}")
            return existing

        # Create new subscription
        subscription = PushSubscription(
            user_id=user_id,
            endpoint=endpoint,
            p256dh_key=p256dh_key,
            auth_key=auth_key,
            user_agent=user_agent,
        )

        self.db.add(subscription)
        await self.db.commit()
        await self.db.refresh(subscription)

        logger.info(f"Created push subscription {subscription.id} for user {user_id}")
        return subscription

    async def delete_subscription(self, subscription: PushSubscription) -> None:
        """Delete a push subscription."""
        sub_id = subscription.id
        await self.db.delete(subscription)
        await self.db.commit()
        logger.info(f"Deleted push subscription {sub_id}")

    async def deactivate_subscription(self, subscription: PushSubscription) -> None:
        """Deactivate a push subscription (e.g., when expired)."""
        subscription.is_active = False
        await self.db.commit()
        logger.info(f"Deactivated push subscription {subscription.id}")

    # ============== Alert Checking Logic ==============

    async def check_alerts_against_prices(
        self, prices: Dict[str, Dict[str, Any]]
    ) -> List[Tuple[PriceAlert, float]]:
        """
        Check all active alerts against provided prices.

        Args:
            prices: Dict mapping symbol to price data

        Returns:
            List of (alert, current_price) tuples for triggered alerts
        """
        # Get all active, non-triggered alerts for symbols we have prices for
        symbols = list(prices.keys())
        if not symbols:
            return []

        query = select(PriceAlert).where(
            and_(
                PriceAlert.symbol.in_(symbols),
                PriceAlert.is_active == True,
                PriceAlert.is_triggered == False,
            )
        )
        result = await self.db.execute(query)
        alerts = result.scalars().all()

        triggered = []

        for alert in alerts:
            quote = prices.get(alert.symbol)
            if not quote:
                continue

            current_price = quote.get("price")
            if current_price is None:
                continue

            threshold = float(alert.threshold)
            is_triggered = False

            if alert.condition_type == AlertConditionType.ABOVE.value:
                is_triggered = current_price >= threshold
            elif alert.condition_type == AlertConditionType.BELOW.value:
                is_triggered = current_price <= threshold
            elif alert.condition_type == AlertConditionType.CHANGE_PERCENT.value:
                change_percent = abs(quote.get("change_percent", 0))
                is_triggered = change_percent >= abs(threshold)

            if is_triggered:
                triggered.append((alert, current_price))

        return triggered

    async def trigger_alert(
        self, alert: PriceAlert, current_price: float
    ) -> None:
        """
        Mark an alert as triggered and send notifications.

        Args:
            alert: The alert to trigger
            current_price: Current price that triggered the alert
        """
        # Update alert status
        alert.is_triggered = True
        alert.triggered_at = datetime.now(timezone.utc)
        await self.db.commit()

        logger.info(
            f"Alert {alert.id} triggered: {alert.symbol} "
            f"{alert.condition_type} {alert.threshold}, current: {current_price}"
        )

        # Send notifications
        await self._send_alert_notifications(alert, current_price)

    async def _send_alert_notifications(
        self, alert: PriceAlert, current_price: float
    ) -> List[NotificationResult]:
        """Send notifications for a triggered alert."""
        # Get user email
        user_query = select(User).where(User.id == alert.user_id)
        user_result = await self.db.execute(user_query)
        user = user_result.scalar_one_or_none()

        if not user:
            logger.warning(f"User {alert.user_id} not found for alert {alert.id}")
            return []

        # Get push subscriptions
        subscriptions = await self.get_user_subscriptions(alert.user_id)
        push_subs = [
            {
                "endpoint": sub.endpoint,
                "p256dh_key": sub.p256dh_key,
                "auth_key": sub.auth_key,
            }
            for sub in subscriptions
        ]

        # Prepare alert data
        alert_data = {
            "alert_id": alert.id,
            "symbol": alert.symbol,
            "condition_type": alert.condition_type,
            "threshold": float(alert.threshold),
            "current_price": current_price,
        }

        # Send notifications
        notification_service = get_notification_service()
        results = await notification_service.send_alert_notification(
            user_email=user.email,
            push_subscriptions=push_subs,
            alert_data=alert_data,
        )

        # Handle failed push subscriptions (mark as inactive)
        for i, result in enumerate(results):
            if (
                not result.success
                and result.error == "Subscription expired"
                and i > 0  # Skip email result (index 0)
            ):
                sub_index = i - 1
                if sub_index < len(subscriptions):
                    await self.deactivate_subscription(subscriptions[sub_index])

        return results


# ============== Standalone Functions for Celery Tasks ==============


async def check_and_trigger_alerts(
    db: AsyncSession, prices: Dict[str, Dict[str, Any]]
) -> int:
    """
    Check all alerts against prices and trigger matching ones.

    This is the main function called by the Celery price monitor task.

    Args:
        db: Database session
        prices: Dict mapping symbol to price data

    Returns:
        Number of alerts triggered
    """
    service = AlertService(db)
    triggered_alerts = await service.check_alerts_against_prices(prices)

    for alert, current_price in triggered_alerts:
        await service.trigger_alert(alert, current_price)

    return len(triggered_alerts)


async def get_all_active_alert_symbols(db: AsyncSession) -> List[str]:
    """
    Get all unique symbols that have active alerts.

    Used by price monitor to know which symbols to fetch.
    """
    query = (
        select(PriceAlert.symbol)
        .where(
            and_(
                PriceAlert.is_active == True,
                PriceAlert.is_triggered == False,
            )
        )
        .distinct()
    )
    result = await db.execute(query)
    return [row[0] for row in result.fetchall()]
