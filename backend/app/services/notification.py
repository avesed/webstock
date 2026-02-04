"""Notification service for email and web push notifications."""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

# Maximum retry attempts
MAX_RETRIES = 5

# Base delay for exponential backoff (in seconds)
BASE_DELAY = 1.0


class NotificationType(str, Enum):
    """Notification type enumeration."""

    EMAIL = "email"
    WEB_PUSH = "web_push"


@dataclass
class NotificationResult:
    """Result of a notification attempt."""

    success: bool
    notification_type: NotificationType
    recipient: str
    error: Optional[str] = None
    retries: int = 0


class EmailNotificationService:
    """Email notification service using aiosmtplib."""

    def __init__(self):
        self.host = settings.SMTP_HOST
        self.port = settings.SMTP_PORT
        self.user = settings.SMTP_USER
        self.password = settings.SMTP_PASSWORD
        self.from_email = settings.SMTP_FROM_EMAIL

    def is_configured(self) -> bool:
        """Check if email service is configured."""
        return bool(self.host and self.user and self.password)

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        html_body: Optional[str] = None,
    ) -> NotificationResult:
        """
        Send an email with retry logic.

        Args:
            to_email: Recipient email address
            subject: Email subject
            body: Plain text body
            html_body: Optional HTML body

        Returns:
            NotificationResult indicating success or failure
        """
        if not self.is_configured():
            logger.warning("Email service not configured, skipping notification")
            return NotificationResult(
                success=False,
                notification_type=NotificationType.EMAIL,
                recipient=to_email,
                error="Email service not configured",
            )

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                await self._send_email_impl(to_email, subject, body, html_body)
                logger.info(f"Email sent successfully to {to_email}")
                return NotificationResult(
                    success=True,
                    notification_type=NotificationType.EMAIL,
                    recipient=to_email,
                    retries=attempt,
                )
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Email send attempt {attempt + 1}/{MAX_RETRIES} failed: {e}"
                )
                if attempt < MAX_RETRIES - 1:
                    # Exponential backoff
                    delay = BASE_DELAY * (2 ** attempt)
                    await asyncio.sleep(delay)

        logger.error(f"Failed to send email to {to_email} after {MAX_RETRIES} attempts")
        return NotificationResult(
            success=False,
            notification_type=NotificationType.EMAIL,
            recipient=to_email,
            error=last_error,
            retries=MAX_RETRIES,
        )

    async def _send_email_impl(
        self,
        to_email: str,
        subject: str,
        body: str,
        html_body: Optional[str],
    ) -> None:
        """Internal implementation of email sending."""
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.from_email
        msg["To"] = to_email

        # Attach plain text body
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Attach HTML body if provided
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        # Send email
        await aiosmtplib.send(
            msg,
            hostname=self.host,
            port=self.port,
            username=self.user,
            password=self.password,
            start_tls=True,
        )


class WebPushNotificationService:
    """Web Push notification service using pywebpush."""

    def __init__(self):
        self.vapid_public_key = settings.VAPID_PUBLIC_KEY
        self.vapid_private_key = settings.VAPID_PRIVATE_KEY
        self.vapid_claims_email = settings.VAPID_CLAIMS_EMAIL

    def is_configured(self) -> bool:
        """Check if web push service is configured."""
        return bool(self.vapid_public_key and self.vapid_private_key)

    def get_vapid_public_key(self) -> Optional[str]:
        """Get the VAPID public key for client subscription."""
        return self.vapid_public_key

    async def send_push(
        self,
        endpoint: str,
        p256dh_key: str,
        auth_key: str,
        payload: Dict[str, Any],
    ) -> NotificationResult:
        """
        Send a web push notification with retry logic.

        Args:
            endpoint: Push subscription endpoint URL
            p256dh_key: Client public key for encryption
            auth_key: Authentication secret
            payload: Notification payload (title, body, icon, etc.)

        Returns:
            NotificationResult indicating success or failure
        """
        if not self.is_configured():
            logger.warning("Web push service not configured, skipping notification")
            return NotificationResult(
                success=False,
                notification_type=NotificationType.WEB_PUSH,
                recipient=endpoint[:50],
                error="Web push service not configured",
            )

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                await self._send_push_impl(endpoint, p256dh_key, auth_key, payload)
                logger.info(f"Web push sent successfully to endpoint {endpoint[:50]}...")
                return NotificationResult(
                    success=True,
                    notification_type=NotificationType.WEB_PUSH,
                    recipient=endpoint[:50],
                    retries=attempt,
                )
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Web push attempt {attempt + 1}/{MAX_RETRIES} failed: {e}"
                )

                # Check for unrecoverable errors
                if "410" in str(e) or "404" in str(e):
                    # Subscription expired or invalid
                    logger.info(f"Push subscription invalid, marking for removal")
                    return NotificationResult(
                        success=False,
                        notification_type=NotificationType.WEB_PUSH,
                        recipient=endpoint[:50],
                        error="Subscription expired",
                        retries=attempt,
                    )

                if attempt < MAX_RETRIES - 1:
                    # Exponential backoff
                    delay = BASE_DELAY * (2 ** attempt)
                    await asyncio.sleep(delay)

        logger.error(
            f"Failed to send web push to {endpoint[:50]}... after {MAX_RETRIES} attempts"
        )
        return NotificationResult(
            success=False,
            notification_type=NotificationType.WEB_PUSH,
            recipient=endpoint[:50],
            error=last_error,
            retries=MAX_RETRIES,
        )

    async def _send_push_impl(
        self,
        endpoint: str,
        p256dh_key: str,
        auth_key: str,
        payload: Dict[str, Any],
    ) -> None:
        """Internal implementation of web push sending."""
        from pywebpush import webpush, WebPushException

        subscription_info = {
            "endpoint": endpoint,
            "keys": {
                "p256dh": p256dh_key,
                "auth": auth_key,
            },
        }

        vapid_claims = {
            "sub": f"mailto:{self.vapid_claims_email}",
        }

        # Run in executor since pywebpush is synchronous
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: webpush(
                subscription_info=subscription_info,
                data=json.dumps(payload),
                vapid_private_key=self.vapid_private_key,
                vapid_claims=vapid_claims,
            ),
        )


class NotificationService:
    """
    Unified notification service for all notification types.

    Supports:
    - Email notifications via SMTP
    - Web Push notifications via VAPID

    Features:
    - Retry logic with exponential backoff (max 5 retries)
    - Configurable notification channels
    - Graceful degradation when services are not configured
    """

    def __init__(self):
        self.email_service = EmailNotificationService()
        self.push_service = WebPushNotificationService()

    async def send_alert_notification(
        self,
        user_email: str,
        push_subscriptions: List[Dict[str, str]],
        alert_data: Dict[str, Any],
    ) -> List[NotificationResult]:
        """
        Send alert notification through all available channels.

        Args:
            user_email: User's email address
            push_subscriptions: List of push subscription info dicts
            alert_data: Alert information for the notification

        Returns:
            List of NotificationResult for each channel
        """
        results = []

        # Prepare notification content
        symbol = alert_data.get("symbol", "UNKNOWN")
        condition = alert_data.get("condition_type", "threshold")
        threshold = alert_data.get("threshold", 0)
        current_price = alert_data.get("current_price", 0)

        # Build message
        if condition == "above":
            message = f"{symbol} has risen above ${threshold:.2f}"
        elif condition == "below":
            message = f"{symbol} has fallen below ${threshold:.2f}"
        else:  # change_percent
            message = f"{symbol} has changed by {threshold}%"

        subject = f"Price Alert: {symbol}"
        body = f"""
Price Alert Triggered!

Stock: {symbol}
Condition: Price {condition} {threshold}
Current Price: ${current_price:.2f}
Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}

---
This is an automated notification from WebStock.
        """.strip()

        html_body = f"""
<html>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
    <h2 style="color: #2563eb;">Price Alert Triggered!</h2>
    <div style="background: #f8fafc; padding: 20px; border-radius: 8px; margin: 20px 0;">
        <p><strong>Stock:</strong> {symbol}</p>
        <p><strong>Condition:</strong> Price {condition} ${threshold:.2f}</p>
        <p><strong>Current Price:</strong> <span style="color: {'#16a34a' if condition == 'above' else '#dc2626'};">${current_price:.2f}</span></p>
        <p><strong>Time:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
    </div>
    <p style="color: #6b7280; font-size: 12px;">
        This is an automated notification from WebStock.
    </p>
</body>
</html>
        """.strip()

        # Send email notification
        if user_email and self.email_service.is_configured():
            email_result = await self.email_service.send_email(
                to_email=user_email,
                subject=subject,
                body=body,
                html_body=html_body,
            )
            results.append(email_result)

        # Send web push notifications
        if push_subscriptions and self.push_service.is_configured():
            push_payload = {
                "title": subject,
                "body": message,
                "icon": "/icon-192x192.png",
                "badge": "/badge-72x72.png",
                "tag": f"price-alert-{alert_data.get('alert_id', 'unknown')}",
                "data": {
                    "url": f"/alerts/{alert_data.get('alert_id', '')}",
                    "symbol": symbol,
                    "alert_id": alert_data.get("alert_id"),
                },
            }

            for sub in push_subscriptions:
                push_result = await self.push_service.send_push(
                    endpoint=sub.get("endpoint", ""),
                    p256dh_key=sub.get("p256dh_key", ""),
                    auth_key=sub.get("auth_key", ""),
                    payload=push_payload,
                )
                results.append(push_result)

        return results

    def get_vapid_public_key(self) -> Optional[str]:
        """Get VAPID public key for client subscription."""
        return self.push_service.get_vapid_public_key()


# Singleton instance
_notification_service: Optional[NotificationService] = None


def get_notification_service() -> NotificationService:
    """Get singleton notification service instance."""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service
