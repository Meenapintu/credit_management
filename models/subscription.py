from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import ClassVar, Optional

from pydantic import Field

from .base import DBSerializableModel


class BillingPeriod(str, Enum):
    DAILY = "daily"
    MONTHLY = "monthly"
    YEARLY = "yearly"


class SubscriptionPlan(DBSerializableModel):
    """
    Subscription plan definition shared across users.
    """

    collection_name: ClassVar[str] = "credit_subscription_plans"

    id: Optional[str] = Field(default=None)
    name: str
    description: Optional[str] = None
    credit_limit: int = Field(description="Total credits allocated per billing period.")
    price: float
    billing_period: BillingPeriod
    validity_days: int = Field(
        description="Number of days before allocated credits expire."
    )
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserSubscription(DBSerializableModel):
    """
    Tracks which subscription plan a user is on, with lifecycle dates.
    """

    collection_name: ClassVar[str] = "credit_user_subscriptions"

    id: Optional[str] = Field(default=None)
    user_id: str
    subscription_plan_id: str
    started_at: datetime = Field(default_factory=datetime.utcnow)
    valid_until: Optional[datetime] = None
    auto_renew: bool = True
    is_active: bool = True

