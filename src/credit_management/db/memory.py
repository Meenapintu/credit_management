from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Iterable, List, Optional

from .base import BaseDBManager
from ..models.credits import CreditExpiryRecord, ReservedCredits
from ..models.notification import NotificationEvent
from ..models.ledger import LedgerEntry
from ..models.subscription import SubscriptionPlan, UserSubscription
from ..models.transaction import Transaction
from ..models.user import UserAccount, UserCreditInfo
from ..models.payment import PaymentRecord
from ..models.promo import PromoRecord, UserPromoClaim


class InMemoryDBManager(BaseDBManager):
    """
    Simple in-memory implementation used for tests and local development.
    NOT suitable for production, but exercises the abstraction and services.
    """

    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # Prevents re-initialization on subsequent calls
        if not self._initialized:
            self._initialized = True
            self._users: Dict[str, UserAccount] = {}
            self._transactions: Dict[str, Transaction] = {}
            self._expiry_records: List[CreditExpiryRecord] = []
            self._reserved: List[ReservedCredits] = []
            self._plans: Dict[str, SubscriptionPlan] = {}
            self._user_subscriptions: Dict[str, UserSubscription] = {}
            self._notifications: List[NotificationEvent] = []
            self._ledger: List[LedgerEntry] = []
            self._payments: Dict[str, PaymentRecord] = {}
            self._promos: Dict[str, PromoRecord] = {}
            self._promo_claims: List[UserPromoClaim] = []
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

    async def get_user_credits(self, user_id: str) -> float:
        user = self._users.get(user_id)
        if user is not None:
            return user.current_credits
        # Fallback: derive from the latest transaction
        user_txs = [t for t in self._transactions.values() if t.user_id == user_id]
        if not user_txs:
            return 0
        user_txs.sort(key=lambda t: t.timestamp)
        return user_txs[-1].current_credits

    async def get_user_credits_info(self, user_id: str) -> UserCreditInfo:
        """Optimized: compute balance and reserved in a single pass."""
        balance = await self.get_user_credits(user_id)
        reserved = sum(r.credits for r in self._reserved if r.user_id == user_id and not r.committed and not r.released)
        return UserCreditInfo(
            balance=balance,
            reserved=reserved,
            available=balance - reserved,
        )

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
    async def add_credit_expiry_record(self, record: CreditExpiryRecord) -> CreditExpiryRecord:
        if record.id is None:
            record.id = self._next_id()
        self._expiry_records.append(record)
        return record

    async def get_credit_expiry_history(self, user_id: str) -> Iterable[CreditExpiryRecord]:
        return [r for r in self._expiry_records if r.user_id == user_id]

    async def add_reserved_credits(self, reserved: ReservedCredits) -> ReservedCredits:
        if reserved.id is None:
            reserved.id = self._next_id()
        self._reserved.append(reserved)
        return reserved

    async def get_reserved_credits_for_subscription_plan(self, subscription_plan_id: str) -> Iterable[ReservedCredits]:
        return [r for r in self._reserved if r.subscription_plan_id == subscription_plan_id and not r.released]

    async def get_reserved_credits_for_user(self, user_id: str) -> float:
        return sum(r.credits for r in self._reserved if r.user_id == user_id and not r.committed and not r.released)

    # Subscription operations
    async def add_subscription_plan(self, plan: SubscriptionPlan) -> SubscriptionPlan:
        if plan.id is None:
            plan.id = self._next_id()
        self._plans[plan.id] = plan
        return plan

    async def update_subscription_plan(self, plan: SubscriptionPlan) -> SubscriptionPlan:
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

    async def add_user_subscription(self, user_subscription: UserSubscription) -> UserSubscription:
        if user_subscription.id is None:
            user_subscription.id = self._next_id()
        self._user_subscriptions[user_subscription.user_id] = user_subscription
        return user_subscription

    async def get_user_subscription_plan(self, user_id: str) -> Optional[UserSubscription]:
        return self._user_subscriptions.get(user_id)

    async def update_user_subscription_plan(self, user_subscription: UserSubscription) -> UserSubscription:
        if user_subscription.id is None:
            raise ValueError("UserSubscription must have id to be updated")
        self._user_subscriptions[user_subscription.user_id] = user_subscription
        return user_subscription

    async def delete_user_subscription_plan(self, user_id: str) -> None:
        self._user_subscriptions.pop(user_id, None)

    # Notifications
    async def add_notification_event(self, notification: NotificationEvent) -> NotificationEvent:
        if notification.id is None:
            notification.id = self._next_id()
        self._notifications.append(notification)
        return notification

    async def add_ledger_entry(self, entry: LedgerEntry) -> LedgerEntry:
        if entry.id is None:
            entry.id = self._next_id()
        self._ledger.append(entry)
        return entry

    # Payment operations
    async def add_payment_record(self, record: PaymentRecord) -> PaymentRecord:
        if record.id is None:
            record.id = self._next_id()
        self._payments[record.id] = record
        return record

    async def get_payment_by_provider_id(self, provider_payment_id: str) -> Optional[PaymentRecord]:
        for rec in self._payments.values():
            if rec.provider_payment_id == provider_payment_id:
                return rec
        return None

    async def get_payment_by_order_id(self, provider_order_id: str) -> Optional[PaymentRecord]:
        for rec in self._payments.values():
            if rec.provider_order_id == provider_order_id:
                return rec
        return None

    async def get_payment_record(self, payment_id: str, user_id: Optional[str] = None) -> Optional[PaymentRecord]:
        record = self._payments.get(payment_id)
        if record and user_id and record.user_id != user_id:
            return None
        return record

    async def get_payment_records_by_user(
        self, user_id: str, limit: int = 20, skip: int = 0
    ) -> Iterable[PaymentRecord]:
        records = [r for r in self._payments.values() if r.user_id == user_id]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records[skip : skip + limit]

    async def count_payment_records(self, user_id: str) -> int:
        return sum(1 for r in self._payments.values() if r.user_id == user_id)

    # Promo operations
    async def add_promo(self, promo: PromoRecord) -> PromoRecord:
        if promo.id is None:
            promo.id = self._next_id()
        self._promos[promo.id] = promo
        return promo

    async def get_promo_by_id(self, promo_id: str) -> Optional[PromoRecord]:
        return self._promos.get(promo_id)

    async def get_promo_by_code(self, code: str) -> Optional[PromoRecord]:
        for promo in self._promos.values():
            if promo.code == code:
                return promo
        return None

    async def list_promos(self, active_only: bool = True) -> list[PromoRecord]:
        promos = list(self._promos.values())
        if active_only:
            promos = [p for p in promos if p.is_active]
        promos.sort(key=lambda p: p.created_at, reverse=True)
        return promos

    async def update_promo(self, promo: PromoRecord) -> PromoRecord:
        if promo.id is None:
            raise ValueError("Promo must have id to be updated")
        self._promos[promo.id] = promo
        return promo

    async def add_promo_claim(self, claim: UserPromoClaim) -> UserPromoClaim:
        if claim.id is None:
            claim.id = self._next_id()
        self._promo_claims.append(claim)
        return claim

    async def get_user_promo_claims(self, user_id: str) -> list[UserPromoClaim]:
        return [c for c in self._promo_claims if c.user_id == user_id]

    async def count_promo_claims(self, promo_id: str) -> int:
        return sum(1 for c in self._promo_claims if c.promo_id == promo_id)

    async def count_user_promo_claims(self, user_id: str, promo_id: str) -> int:
        return sum(1 for c in self._promo_claims if c.user_id == user_id and c.promo_id == promo_id)
