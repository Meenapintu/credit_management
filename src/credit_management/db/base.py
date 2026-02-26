from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import AsyncIterator, Iterable, Optional, Protocol

from ..models.credits import CreditExpiryRecord, ReservedCredits
from ..models.notification import NotificationEvent
from ..models.ledger import LedgerEntry
from ..models.subscription import SubscriptionPlan, UserSubscription
from ..models.transaction import Transaction
from ..models.user import UserAccount, UserCreditInfo


class AsyncTransaction(Protocol):
    async def __aenter__(self) -> "AsyncTransaction":  # pragma: no cover - trivial
        ...

    async def __aexit__(self, exc_type, exc, tb) -> None:  # pragma: no cover
        ...


class BaseDBManager(ABC):
    """
    DB-agnostic async manager interface.

    Concrete implementations (SQLAlchemy, MongoDB, etc.) should implement
    these methods. All methods are designed for atomicity through the
    `transaction()` context manager.
    """

    @abstractmethod
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """
        Provide an atomic transaction context if the backend supports it.
        Should rollback on exception and commit on success.
        """
        yield

    # User operations
    @abstractmethod
    async def add_user(self, user: UserAccount) -> UserAccount: ...

    @abstractmethod
    async def get_user(self, user_id: str) -> Optional[UserAccount]: ...

    @abstractmethod
    async def update_user(self, user: UserAccount) -> UserAccount: ...

    @abstractmethod
    async def get_user_credits(self, user_id: str) -> float: ...

    @abstractmethod
    async def get_user_credits_info(self, user_id: str) -> UserCreditInfo:
        """
        Get balance, reserved, and available credits in a single optimized call.
        This is more efficient than calling get_user_credits + get_reserved_credits_for_user separately.
        """
        ...

    # Transaction / ledger operations
    @abstractmethod
    async def add_transaction(self, tx: Transaction) -> Transaction: ...

    @abstractmethod
    async def get_transaction(self, transaction_id: str) -> Optional[Transaction]: ...

    @abstractmethod
    async def get_transactions(self, user_id: str) -> Iterable[Transaction]: ...

    # Credit expiry / reservation
    @abstractmethod
    async def add_credit_expiry_record(self, record: CreditExpiryRecord) -> CreditExpiryRecord: ...

    @abstractmethod
    async def get_credit_expiry_history(self, user_id: str) -> Iterable[CreditExpiryRecord]: ...

    @abstractmethod
    async def add_reserved_credits(self, reserved: ReservedCredits) -> ReservedCredits: ...

    @abstractmethod
    async def get_reserved_credits_for_subscription_plan(
        self, subscription_plan_id: str
    ) -> Iterable[ReservedCredits]: ...

    @abstractmethod
    async def get_reserved_credits_for_user(self, user_id: str) -> float:
        """
        Sum of credits currently reserved for this user (not yet committed or released).
        Used to compute available balance = get_user_credits - get_reserved_credits_for_user.
        """
        ...

    # Subscription operations
    @abstractmethod
    async def add_subscription_plan(self, plan: SubscriptionPlan) -> SubscriptionPlan: ...

    @abstractmethod
    async def update_subscription_plan(self, plan: SubscriptionPlan) -> SubscriptionPlan: ...

    @abstractmethod
    async def delete_subscription_plan(self, plan_id: str) -> None: ...

    @abstractmethod
    async def get_subscription_plan(self, plan_id: str) -> Optional[SubscriptionPlan]: ...

    @abstractmethod
    async def get_all_subscription_plans(self) -> Iterable[SubscriptionPlan]: ...

    @abstractmethod
    async def add_user_subscription(self, user_subscription: UserSubscription) -> UserSubscription: ...

    @abstractmethod
    async def get_user_subscription_plan(self, user_id: str) -> Optional[UserSubscription]: ...

    @abstractmethod
    async def update_user_subscription_plan(self, user_subscription: UserSubscription) -> UserSubscription: ...

    @abstractmethod
    async def delete_user_subscription_plan(self, user_id: str) -> None: ...

    # Notifications
    @abstractmethod
    async def add_notification_event(self, notification: NotificationEvent) -> NotificationEvent: ...

    # Ledger
    @abstractmethod
    async def add_ledger_entry(self, entry: LedgerEntry) -> LedgerEntry: ...
