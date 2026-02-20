from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from ..db.base import BaseDBManager
from ..models.notification import (
    NotificationEvent,
    NotificationStatus,
    NotificationType,
)
from ..notifications.queue import AsyncNotificationQueue
from .credit_service import CreditService


class NotificationService:
    """
    Orchestrates notification creation and dispatch via a message queue.
    """

    def __init__(
        self,
        db: BaseDBManager,
        queue: AsyncNotificationQueue,
        credit_service: CreditService,
        low_credit_threshold: int,
    ) -> None:
        self._db = db
        self._queue = queue
        self._credit_service = credit_service
        self._low_credit_threshold = low_credit_threshold

    async def notify_low_credits(self, user_id: str) -> None:
        current = await self._credit_service.get_user_credits_info(user_id)
        if current.available > self._low_credit_threshold:
            return

        event = NotificationEvent(
            user_id=user_id,
            notification_type=NotificationType.LOW_CREDITS,
            payload={"current_credits": current},
            status=NotificationStatus.PENDING,
        )
        event = await self._db.add_notification_event(event)

        await self._queue.enqueue(
            {
                "notification_id": event.id,
                "type": event.notification_type.value,
                "user_id": user_id,
                "payload": event.payload,
            }
        )

    async def notify_expiring_credits(
        self, user_id: str, within_days: int
    ) -> None:
        expiring = await self._credit_service.get_expiring_credits_in_days(
            user_id=user_id, days=within_days
        )
        total = sum(r.remaining_credits for r in expiring)
        if total <= 0:
            return

        event = NotificationEvent(
            user_id=user_id,
            notification_type=NotificationType.EXPIRING_CREDITS,
            payload={"expiring_credits": total, "within_days": within_days},
            status=NotificationStatus.PENDING,
        )
        event = await self._db.add_notification_event(event)

        await self._queue.enqueue(
            {
                "notification_id": event.id,
                "type": event.notification_type.value,
                "user_id": user_id,
                "payload": event.payload,
            }
        )

    async def notify_transaction_error(
        self, user_id: str, message: str, details: dict
    ) -> None:
        event = NotificationEvent(
            user_id=user_id,
            notification_type=NotificationType.TRANSACTION_ERROR,
            payload={"message": message, "details": details},
            status=NotificationStatus.PENDING,
        )
        event = await self._db.add_notification_event(event)

        await self._queue.enqueue(
            {
                "notification_id": event.id,
                "type": event.notification_type.value,
                "user_id": user_id,
                "payload": event.payload,
            }
        )

