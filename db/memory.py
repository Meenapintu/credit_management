from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Iterable, List, Optional

from .base import BaseDBManager
from ..models.credits import CreditExpiryRecord, ReservedCredits
from ..models.notification import NotificationEvent
from ..models.ledger import LedgerEntry
from ..models.subscription import SubscriptionPlan, UserSubscription
from ..models.transaction import Transaction
from ..models.user import UserAccount


class InMemoryDBManager(BaseDBManager):
    """
    Simple in-memory implementation used for tests and local development.
    NOT suitable for production, but exercises the abstraction and services.
    """

    def __init__(self) -> None:
        self._users: Dict[str, UserAccount] = {}
        self._transactions: Dict[str, Transaction] = {}
        self._expiry_records: List[CreditExpiryRecord] = []
        self._reserved: List[ReservedCredits] = []
        self._plans: Dict[str, SubscriptionPlan] = {}
        self._user_subscriptions: Dict[str, UserSubscription] = {}
        self._notifications: List[NotificationEvent] = []
        self._ledger: List[LedgerEntry] = []
        self._id_counter: int = 0

    def _next_id(self) -> str:
        self._id_counter += 1
        return str(self._id_counter)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        # In-memory backend cannot provide real rollback; this is a no-op.
        yield

    # User operations
    async def add_user(self, user: UserAccount) -> UserAccount:
        if user.id is None:
            user.id = self._next_id()
        self._users[user.id] = user
        return user

    async def get_user(self, user_id: str) -> Optional[UserAccount]:
        return self._users.get(user_id)

    async def update_user(self, user: UserAccount) -> UserAccount:
        if user.id is None:
            raise ValueError("User must have id to be updated")
        self._users[user.id] = user
        return user

    async def get_user_credits(self, user_id: str) -> int:
        user = self._users.get(user_id)
        if user is not None:
            return user.current_credits
        # Fallback: derive from the latest transaction
        user_txs = [t for t in self._transactions.values() if t.user_id == user_id]
        if not user_txs:
            return 0
        user_txs.sort(key=lambda t: t.timestamp)
        return user_txs[-1].current_credits

    # Transaction operations
    async def add_transaction(self, tx: Transaction) -> Transaction:
        if tx.id is None:
            tx.id = self._next_id()
        self._transactions[tx.id] = tx
        return tx

    async def get_transaction(self, transaction_id: str) -> Optional[Transaction]:
        return self._transactions.get(transaction_id)

    async def get_transactions(self, user_id: str) -> Iterable[Transaction]:
        return [t for t in self._transactions.values() if t.user_id == user_id]

    # Credit expiry / reservation
    async def add_credit_expiry_record(
        self, record: CreditExpiryRecord
    ) -> CreditExpiryRecord:
        if record.id is None:
            record.id = self._next_id()
        self._expiry_records.append(record)
        return record

    async def get_credit_expiry_history(
        self, user_id: str
    ) -> Iterable[CreditExpiryRecord]:
        return [r for r in self._expiry_records if r.user_id == user_id]

    async def add_reserved_credits(
        self, reserved: ReservedCredits
    ) -> ReservedCredits:
        if reserved.id is None:
            reserved.id = self._next_id()
        self._reserved.append(reserved)
        return reserved

    async def get_reserved_credits_for_subscription_plan(
        self, subscription_plan_id: str
    ) -> Iterable[ReservedCredits]:
        return [
            r
            for r in self._reserved
            if r.subscription_plan_id == subscription_plan_id and not r.released
        ]

    # Subscription operations
    async def add_subscription_plan(
        self, plan: SubscriptionPlan
    ) -> SubscriptionPlan:
        if plan.id is None:
            plan.id = self._next_id()
        self._plans[plan.id] = plan
        return plan

    async def update_subscription_plan(
        self, plan: SubscriptionPlan
    ) -> SubscriptionPlan:
        if plan.id is None:
            raise ValueError("Plan must have id to be updated")
        self._plans[plan.id] = plan
        return plan

    async def delete_subscription_plan(self, plan_id: str) -> None:
        self._plans.pop(plan_id, None)

    async def get_subscription_plan(self, plan_id: str) -> Optional[SubscriptionPlan]:
        return self._plans.get(plan_id)

    async def get_all_subscription_plans(self) -> Iterable[SubscriptionPlan]:
        return list(self._plans.values())

    async def add_user_subscription(
        self, user_subscription: UserSubscription
    ) -> UserSubscription:
        if user_subscription.id is None:
            user_subscription.id = self._next_id()
        self._user_subscriptions[user_subscription.user_id] = user_subscription
        return user_subscription

    async def get_user_subscription_plan(
        self, user_id: str
    ) -> Optional[UserSubscription]:
        return self._user_subscriptions.get(user_id)

    async def update_user_subscription_plan(
        self, user_subscription: UserSubscription
    ) -> UserSubscription:
        if user_subscription.id is None:
            raise ValueError("UserSubscription must have id to be updated")
        self._user_subscriptions[user_subscription.user_id] = user_subscription
        return user_subscription

    async def delete_user_subscription_plan(self, user_id: str) -> None:
        self._user_subscriptions.pop(user_id, None)

    # Notifications
    async def add_notification_event(
        self, notification: NotificationEvent
    ) -> NotificationEvent:
        if notification.id is None:
            notification.id = self._next_id()
        self._notifications.append(notification)
        return notification

    async def add_ledger_entry(self, entry: LedgerEntry) -> LedgerEntry:
        if entry.id is None:
            entry.id = self._next_id()
        self._ledger.append(entry)
        return entry

