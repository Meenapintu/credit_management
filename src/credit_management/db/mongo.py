from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Iterable, List, Mapping, Optional, Type, TypeVar
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from .base import BaseDBManager
from ..models.base import DBSerializableModel
from ..models.credits import CreditExpiryRecord, ReservedCredits
from ..models.ledger import LedgerEntry
from ..models.notification import NotificationEvent
from ..models.subscription import SubscriptionPlan, UserSubscription
from ..models.transaction import Transaction
from ..models.user import UserAccount, UserCreditInfo


TModel = TypeVar("TModel", bound=DBSerializableModel)


class MongoDBManager(BaseDBManager):
    """
    MongoDB implementation of BaseDBManager using motor (async driver).

    IDs are stored as string-based `_id` fields and mirrored in the `id`
    attribute of each Pydantic model, which keeps the rest of the system
    agnostic of MongoDB specifics.

    Note: The `transaction()` context manager is currently a no-op. MongoDB
    supports multi-document transactions in replica sets; you can extend this
    class to use sessions and transactions if your deployment requires it.
    """

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._db = database

    @classmethod
    def from_client_uri(cls, uri: str, db_name: str) -> "MongoDBManager":
        client = AsyncIOMotorClient(uri)
        return cls(client[db_name])

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        # For simplicity, this implementation does not open an explicit
        # MongoDB multi-document transaction. Individual document writes
        # are atomic in MongoDB.
        yield

    # Helper utilities
    @staticmethod
    def _prepare_insert(model: TModel) -> Dict[str, Any]:
        data = model.serialize_for_db()
        model_id = getattr(model, "id", None)
        if not model_id:
            model_id = uuid4().hex
            setattr(model, "id", model_id)
            data["id"] = model_id
        data["_id"] = model_id
        return data

    @staticmethod
    def _prepare_update(model: TModel) -> Dict[str, Any]:
        data = model.serialize_for_db()
        model_id = getattr(model, "id", None)
        if not model_id:
            raise ValueError("Model must have id to be updated")
        data["_id"] = model_id
        return data

    @staticmethod
    def _decode(model_cls: Type[TModel], doc: Optional[Mapping[str, Any]]) -> Optional[TModel]:
        if doc is None:
            return None
        data = dict(doc)
        if "_id" in data and "id" not in data:
            data["id"] = str(data["_id"])
        return model_cls.model_validate(data)

    # User operations
    async def add_user(self, user: UserAccount) -> UserAccount:
        col = self._db[UserAccount.collection_name]
        data = self._prepare_insert(user)
        await col.insert_one(data)
        return user

    async def get_user(self, user_id: str) -> Optional[UserAccount]:
        col = self._db[UserAccount.collection_name]
        doc = await col.find_one({"_id": user_id})
        return self._decode(UserAccount, doc)

    async def update_user(self, user: UserAccount) -> UserAccount:
        col = self._db[UserAccount.collection_name]
        data = self._prepare_update(user)
        await col.replace_one({"_id": data["_id"]}, data, upsert=False)
        return user

    async def get_user_credits(self, user_id: str) -> int:
        """
        Derive current credits from the latest transaction for a user.
        """
        col = self._db[Transaction.collection_name]
        cursor = col.find({"user_id": user_id}).sort("timestamp", -1).limit(1)
        docs = await cursor.to_list(length=1)
        if not docs:
            return 0
        tx = self._decode(Transaction, docs[0])
        return tx.current_credits if tx else 0

    async def get_user_credits_info(self, user_id: str) -> UserCreditInfo:
        """
        Optimized: get balance and reserved in parallel (two queries run concurrently).
        For MongoDB, we could use aggregation pipelines, but parallel queries are simpler
        and still efficient.
        """
        import asyncio

        # Run both queries concurrently
        balance_task = asyncio.create_task(self.get_user_credits(user_id))
        reserved_task = asyncio.create_task(self.get_reserved_credits_for_user(user_id))

        balance, reserved = await asyncio.gather(balance_task, reserved_task)

        return UserCreditInfo(
            balance=balance,
            reserved=reserved,
            available=balance - reserved,
        )

    # Transaction / ledger operations
    async def add_transaction(self, tx: Transaction) -> Transaction:
        col = self._db[Transaction.collection_name]
        data = self._prepare_insert(tx)
        await col.insert_one(data)
        return tx

    async def get_transaction(self, transaction_id: str) -> Optional[Transaction]:
        col = self._db[Transaction.collection_name]
        doc = await col.find_one({"_id": transaction_id})
        return self._decode(Transaction, doc)

    async def get_transactions(self, user_id: str) -> Iterable[Transaction]:
        col = self._db[Transaction.collection_name]
        cursor = col.find({"user_id": user_id}).sort("timestamp", 1)
        docs = await cursor.to_list(length=None)
        return [self._decode(Transaction, d) for d in docs if d is not None]  # type: ignore[list-item]

    # Credit expiry / reservation
    async def add_credit_expiry_record(
        self, record: CreditExpiryRecord
    ) -> CreditExpiryRecord:
        col = self._db[CreditExpiryRecord.collection_name]
        data = self._prepare_insert(record)
        await col.insert_one(data)
        return record

    async def get_credit_expiry_history(
        self, user_id: str
    ) -> Iterable[CreditExpiryRecord]:
        col = self._db[CreditExpiryRecord.collection_name]
        cursor = col.find({"user_id": user_id}).sort("expires_at", 1)
        docs = await cursor.to_list(length=None)
        return [
            self._decode(CreditExpiryRecord, d)
            for d in docs
            if d is not None
        ]  # type: ignore[list-item]

    async def add_reserved_credits(
        self, reserved: ReservedCredits
    ) -> ReservedCredits:
        col = self._db[ReservedCredits.collection_name]
        data = self._prepare_insert(reserved)
        await col.replace_one({"_id": data["_id"]}, data, upsert=True)
        return reserved

    async def get_reserved_credits_for_subscription_plan(
        self, subscription_plan_id: str
    ) -> Iterable[ReservedCredits]:
        col = self._db[ReservedCredits.collection_name]
        cursor = col.find(
            {
                "subscription_plan_id": subscription_plan_id,
                "released": False,
            }
        )
        docs = await cursor.to_list(length=None)
        return [
            self._decode(ReservedCredits, d)
            for d in docs
            if d is not None
        ]  # type: ignore[list-item]

    async def get_reserved_credits_for_user(self, user_id: str) -> int:
        col = self._db[ReservedCredits.collection_name]
        cursor = col.find(
            {"user_id": user_id, "committed": False, "released": False}
        )
        docs = await cursor.to_list(length=None)
        return sum(d.get("credits", 0) for d in docs)

    # Subscription operations
    async def add_subscription_plan(
        self, plan: SubscriptionPlan
    ) -> SubscriptionPlan:
        col = self._db[SubscriptionPlan.collection_name]
        data = self._prepare_insert(plan)
        await col.insert_one(data)
        return plan

    async def update_subscription_plan(
        self, plan: SubscriptionPlan
    ) -> SubscriptionPlan:
        col = self._db[SubscriptionPlan.collection_name]
        data = self._prepare_update(plan)
        await col.replace_one({"_id": data["_id"]}, data, upsert=False)
        return plan

    async def delete_subscription_plan(self, plan_id: str) -> None:
        col = self._db[SubscriptionPlan.collection_name]
        await col.delete_one({"_id": plan_id})

    async def get_subscription_plan(self, plan_id: str) -> Optional[SubscriptionPlan]:
        col = self._db[SubscriptionPlan.collection_name]
        doc = await col.find_one({"_id": plan_id})
        return self._decode(SubscriptionPlan, doc)

    async def get_all_subscription_plans(self) -> Iterable[SubscriptionPlan]:
        col = self._db[SubscriptionPlan.collection_name]
        cursor = col.find({})
        docs = await cursor.to_list(length=None)
        return [
            self._decode(SubscriptionPlan, d)
            for d in docs
            if d is not None
        ]  # type: ignore[list-item]

    async def add_user_subscription(
        self, user_subscription: UserSubscription
    ) -> UserSubscription:
        col = self._db[UserSubscription.collection_name]
        data = self._prepare_insert(user_subscription)
        await col.replace_one(
            {"user_id": user_subscription.user_id}, data, upsert=True
        )
        return user_subscription

    async def get_user_subscription_plan(
        self, user_id: str
    ) -> Optional[UserSubscription]:
        col = self._db[UserSubscription.collection_name]
        doc = await col.find_one({"user_id": user_id})
        return self._decode(UserSubscription, doc)

    async def update_user_subscription_plan(
        self, user_subscription: UserSubscription
    ) -> UserSubscription:
        col = self._db[UserSubscription.collection_name]
        data = self._prepare_update(user_subscription)
        await col.replace_one({"_id": data["_id"]}, data, upsert=False)
        return user_subscription

    async def delete_user_subscription_plan(self, user_id: str) -> None:
        col = self._db[UserSubscription.collection_name]
        await col.delete_one({"user_id": user_id})

    # Notifications
    async def add_notification_event(
        self, notification: NotificationEvent
    ) -> NotificationEvent:
        col = self._db[NotificationEvent.collection_name]
        data = self._prepare_insert(notification)
        await col.insert_one(data)
        return notification

    # Ledger
    async def add_ledger_entry(self, entry: LedgerEntry) -> LedgerEntry:
        col = self._db[LedgerEntry.collection_name]
        data = self._prepare_insert(entry)
        await col.insert_one(data)
        return entry

