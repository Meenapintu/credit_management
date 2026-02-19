from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional

from ..cache.base import AsyncCacheBackend
from ..db.base import BaseDBManager
from ..logging.ledger_logger import LedgerLogger
from ..models.subscription import BillingPeriod, SubscriptionPlan, UserSubscription


class SubscriptionService:
    """
    Subscription management: plans CRUD and user plan assignments.
    """

    def __init__(
        self,
        db: BaseDBManager,
        ledger: LedgerLogger,
        cache: Optional[AsyncCacheBackend] = None,
    ) -> None:
        self._db = db
        self._ledger = ledger
        self._cache = cache

    async def add_subscription_plan(self, plan: SubscriptionPlan) -> SubscriptionPlan:
        plan = await self._db.add_subscription_plan(plan)
        await self._invalidate_plan_cache()
        return plan

    async def update_subscription_plan(self, plan: SubscriptionPlan) -> SubscriptionPlan:
        plan = await self._db.update_subscription_plan(plan)
        await self._invalidate_plan_cache()
        return plan

    async def delete_subscription_plan(self, plan_id: str) -> None:
        await self._db.delete_subscription_plan(plan_id)
        await self._invalidate_plan_cache()

    async def get_subscription_plan(self, plan_id: str) -> Optional[SubscriptionPlan]:
        cache_key = self._plan_cache_key(plan_id)
        if self._cache:
            cached = await self._cache.get(cache_key)
            if isinstance(cached, SubscriptionPlan):
                return cached
        plan = await self._db.get_subscription_plan(plan_id)
        if plan and self._cache:
            await self._cache.set(cache_key, plan, ttl_seconds=300)
        return plan

    async def list_subscription_plans(self) -> Iterable[SubscriptionPlan]:
        # For simplicity, we don't cache the full list; production code might.
        return await self._db.get_all_subscription_plans()

    async def get_user_subscription_plan(
        self, user_id: str
    ) -> Optional[UserSubscription]:
        return await self._db.get_user_subscription_plan(user_id)

    async def set_user_subscription_plan(
        self,
        user_id: str,
        plan: SubscriptionPlan,
        auto_renew: bool = True,
    ) -> UserSubscription:
        valid_until = self._compute_valid_until(plan.billing_period)
        user_sub = UserSubscription(
            user_id=user_id,
            subscription_plan_id=plan.id or "",
            valid_until=valid_until,
            auto_renew=auto_renew,
        )
        user_sub = await self._db.add_user_subscription(user_sub)

        await self._ledger.log_transaction(
            user_id=user_id,
            message="User subscription set",
            details={
                "subscription_plan_id": plan.id,
                "valid_until": valid_until.isoformat() if valid_until else None,
            },
            correlation_id=None,
        )
        return user_sub

    async def upgrade_user_subscription_plan(
        self,
        user_id: str,
        new_plan: SubscriptionPlan,
    ) -> UserSubscription:
        current = await self._db.get_user_subscription_plan(user_id)
        valid_until = self._compute_valid_until(new_plan.billing_period)
        if current:
            current.subscription_plan_id = new_plan.id or ""
            current.valid_until = valid_until
            current = await self._db.update_user_subscription_plan(current)
            user_sub = current
        else:
            user_sub = await self.set_user_subscription_plan(user_id, new_plan)

        await self._ledger.log_transaction(
            user_id=user_id,
            message="User subscription upgraded",
            details={
                "new_plan_id": new_plan.id,
                "valid_until": valid_until.isoformat() if valid_until else None,
            },
            correlation_id=None,
        )
        return user_sub

    async def delete_user_subscription_plan(self, user_id: str) -> None:
        await self._db.delete_user_subscription_plan(user_id)

        await self._ledger.log_transaction(
            user_id=user_id,
            message="User subscription deleted",
            details={},
            correlation_id=None,
        )

    @staticmethod
    def _compute_valid_until(period: BillingPeriod) -> datetime:
        now = datetime.utcnow()
        if period == BillingPeriod.DAILY:
            return now + timedelta(days=1)
        if period == BillingPeriod.MONTHLY:
            return now + timedelta(days=30)
        if period == BillingPeriod.YEARLY:
            return now + timedelta(days=365)
        return now

    @staticmethod
    def _plan_cache_key(plan_id: str) -> str:
        return f"credit:subscription_plan:{plan_id}"

    async def _invalidate_plan_cache(self) -> None:
        # For a real cache we might track keys. Here we keep it simple.
        return None

