from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..db.base import BaseDBManager
from ..logging.ledger_logger import LedgerLogger
from ..models.subscription import SubscriptionPlan, UserSubscription
from .credit_service import CreditService


class ExpirationService:
    """
    Handles credit expiration checks and periodic allocation of subscription credits.
    """

    def __init__(
        self,
        db: BaseDBManager,
        ledger: LedgerLogger,
        credit_service: CreditService,
    ) -> None:
        self._db = db
        self._ledger = ledger
        self._credit_service = credit_service

    async def check_credit_expiration(
        self, user_id: str, as_of: Optional[datetime] = None
    ) -> int:
        """
        Public façade over CreditService.expire_credits, for orchestration code.
        """
        return await self._credit_service.expire_credits(user_id=user_id, as_of=as_of)

    async def allocate_subscription_credits(
        self, user_subscription: UserSubscription, plan: SubscriptionPlan
    ) -> None:
        """
        Allocate credits according to the user's subscription plan.

        This is typically invoked by a scheduler (daily or monthly).
        """
        await self._credit_service.add_credits(
            user_id=user_subscription.user_id,
            amount=plan.credit_limit,
            subscription_plan_id=plan.id,
            description=f"Subscription allocation for plan {plan.name}",
        )

        await self._ledger.log_transaction(
            user_id=user_subscription.user_id,
            message="Subscription credits allocated",
            details={
                "subscription_plan_id": plan.id,
                "credit_limit": plan.credit_limit,
            },
            correlation_id=None,
        )

