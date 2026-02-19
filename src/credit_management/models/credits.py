from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Optional

from pydantic import Field

from .base import DBSerializableModel


class CreditExpiryRecord(DBSerializableModel):
    """
    Represents a chunk of credits with an explicit expiry.
    Used to implement per-plan expiration rules.
    """

    collection_name: ClassVar[str] = "credit_expiry_records"

    id: Optional[str] = Field(default=None)
    user_id: str
    subscription_plan_id: Optional[str] = None
    credits: int
    remaining_credits: int
    expires_at: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expired: bool = False


class ReservedCredits(DBSerializableModel):
    """
    Credits that have been reserved for a pending operation but not yet consumed.
    """

    collection_name: ClassVar[str] = "credit_reserved"

    id: Optional[str] = Field(default=None)
    user_id: str
    subscription_plan_id: Optional[str] = None
    credits: int
    reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    committed: bool = False
    released: bool = False

