from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import ClassVar, Optional

from pydantic import Field

from .base import DBSerializableModel


class NotificationType(str, Enum):
    LOW_CREDITS = "low_credits"
    EXPIRING_CREDITS = "expiring_credits"
    TRANSACTION_ERROR = "transaction_error"


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class NotificationEvent(DBSerializableModel):
    """
    Stored representation of notifications for auditing/monitoring.
    """

    collection_name: ClassVar[str] = "credit_notifications"

    id: Optional[str] = Field(default=None)
    user_id: str
    notification_type: NotificationType
    payload: dict = Field(default_factory=dict)
    status: NotificationStatus = NotificationStatus.PENDING
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    sent_at: Optional[datetime] = None

