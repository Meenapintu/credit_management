from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Optional

from pydantic import Field

from .base import DBSerializableModel


class UserAccount(DBSerializableModel):
    """
    Internal user representation for the credit system.
    This is isolated from any external application's user model.
    """

    collection_name: ClassVar[str] = "credit_users"

    id: Optional[str] = Field(default=None)
    external_user_ref: Optional[str] = Field(
        default=None,
        description="Optional reference to external user identifier from host system.",
    )
    current_credits: int = 0
    reserved_credits: int = 0
    active_subscription_plan_id: Optional[str] = None
    status: str = Field(default="active")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

