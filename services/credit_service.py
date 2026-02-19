from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional

from ..cache.base import AsyncCacheBackend
from ..db.base import BaseDBManager
from ..logging.ledger_logger import LedgerLogger
from ..models.credits import CreditExpiryRecord, ReservedCredits
from ..models.transaction import Transaction, TransactionType
from ..models.user import UserCreditInfo


class CreditService:
    """
    High-level credit management service.

    Methods are intentionally narrow and focused on correctness and atomicity.
    """

    def __init__(
        self,
        db: BaseDBManager,
        ledger: LedgerLogger,
        cache: Optional[AsyncCacheBackend] = None,
        low_credit_threshold: int = 0,
    ) -> None:
        self._db = db
        self._ledger = ledger
        self._cache = cache
        self._low_credit_threshold = low_credit_threshold

    async def add_credits(
        self,
        user_id: str,
        amount: int,
        description: str | None = None,
        subscription_plan_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Transaction:
        if amount <= 0:
            raise ValueError("amount must be positive")

        async with self._db.transaction():
            current = await self._db.get_user_credits(user_id)
            new_balance = current + amount

            tx = Transaction(
                user_id=user_id,
                credits_added=amount,
                credits_deducted=0,
                current_credits=new_balance,
                transaction_type=TransactionType.ADD,
                description=description,
            )
            tx = await self._db.add_transaction(tx)

            # Record expiry chunk if a plan governs its lifetime
            if subscription_plan_id is not None:
                # Simple default: credits expire in 30 days; more precise logic
                # lives in the expiration service based on plan configuration.
                expiry = CreditExpiryRecord(
                    user_id=user_id,
                    subscription_plan_id=subscription_plan_id,
                    credits=amount,
                    remaining_credits=amount,
                    expires_at=datetime.utcnow() + timedelta(days=30),
                )
                await self._db.add_credit_expiry_record(expiry)

            # Ledger logging
            await self._ledger.log_transaction(
                user_id=user_id,
                message="Credits added",
                details={
                    "amount": amount,
                    "new_balance": new_balance,
                    "description": description or "",
                },
                correlation_id=correlation_id,
            )

            # Cache update
            if self._cache:
                await self._cache.set(self._user_credits_cache_key(user_id), new_balance)
                # Update credit info cache: balance increased, reserved unchanged
                await self._update_credit_info_cache(
                    user_id, balance_delta=amount, reserved_delta=0
                )

            return tx

    async def deduct_credits(
        self,
        user_id: str,
        amount: int,
        description: str | None = None,
        correlation_id: str | None = None,
    ) -> Transaction:
        """
        Deduct credits from user account. Raises ValueError if insufficient credits.
        Use this for pre-service checks where you need to ensure sufficient balance.
        """
        if amount <= 0:
            raise ValueError("amount must be positive")

        async with self._db.transaction():
            credit_info = await self._db.get_user_credits_info(user_id)
            if credit_info.available < amount:
                await self._ledger.log_error(
                    message="Insufficient credits for deduction",
                    details={
                        "requested": amount,
                        "balance": credit_info.balance,
                        "reserved": credit_info.reserved,
                        "available": credit_info.available,
                    },
                    user_id=user_id,
                    correlation_id=correlation_id,
                )
                raise ValueError("insufficient credits")

            return await self._deduct_credits_internal(
                user_id=user_id,
                amount=amount,
                current_balance=credit_info.balance,
                description=description,
                correlation_id=correlation_id,
            )

    async def deduct_credits_after_service(
        self,
        user_id: str,
        amount: int,
        description: str | None = None,
        correlation_id: str | None = None,
    ) -> Transaction:
        """
        Deduct credits after service execution. Allows balance to go negative.
        
        Use this when deducting actual usage after a service has run, where the
        actual cost may exceed the reserved amount. The balance can go negative
        to track overage/overdraft.
        
        Example: Middleware reserved 50 credits, but actual API usage was 60.
        This method allows deducting 60 even if only 50 was reserved.
        """
        if amount <= 0:
            raise ValueError("amount must be positive")

        async with self._db.transaction():
            credit_info = await self._db.get_user_credits_info(user_id)
            return await self._deduct_credits_internal(
                user_id=user_id,
                amount=amount,
                current_balance=credit_info.balance,
                description=description,
                correlation_id=correlation_id,
            )

    async def _deduct_credits_internal(
        self,
        user_id: str,
        amount: int,
        current_balance: int,
        description: str | None = None,
        correlation_id: str | None = None,
    ) -> Transaction:
        """
        Internal method that performs the actual deduction logic.
        Shared by deduct_credits and deduct_credits_after_service.
        """
        new_balance = current_balance - amount

        tx = Transaction(
            user_id=user_id,
            credits_added=0,
            credits_deducted=amount,
            current_credits=new_balance,
            transaction_type=TransactionType.DEDUCT,
            description=description,
        )
        tx = await self._db.add_transaction(tx)

        await self._ledger.log_transaction(
            user_id=user_id,
            message="Credits deducted",
            details={
                "amount": amount,
                "new_balance": new_balance,
                "description": description or "",
            },
            correlation_id=correlation_id,
        )

        if self._cache:
            await self._cache.set(self._user_credits_cache_key(user_id), new_balance)
            # Update credit info cache: balance decreased, reserved unchanged
            await self._update_credit_info_cache(
                user_id, balance_delta=-amount, reserved_delta=0
            )

        return tx

    async def expire_credits(
        self,
        user_id: str,
        as_of: Optional[datetime] = None,
        correlation_id: str | None = None,
    ) -> int:
        """
        Expire all credits whose expiry timestamp is before `as_of`.
        Returns the number of credits expired.
        """
        as_of = as_of or datetime.utcnow()
        expired_total = 0

        async with self._db.transaction():
            records = list(await self._db.get_credit_expiry_history(user_id))
            for record in records:
                if record.expired or record.expires_at > as_of:
                    continue
                expired_total += record.remaining_credits
                record.remaining_credits = 0
                record.expired = True
                # Persist updated record
                await self._db.add_credit_expiry_record(record)

            if expired_total > 0:
                current = await self._db.get_user_credits(user_id)
                new_balance = max(current - expired_total, 0)
                tx = Transaction(
                    user_id=user_id,
                    credits_added=0,
                    credits_deducted=expired_total,
                    current_credits=new_balance,
                    transaction_type=TransactionType.EXPIRE,
                    description="Credits expired",
                )
                await self._db.add_transaction(tx)

                await self._ledger.log_transaction(
                    user_id=user_id,
                    message="Credits expired",
                    details={"expired_total": expired_total, "new_balance": new_balance},
                    correlation_id=correlation_id,
                )

                if self._cache:
                    await self._cache.set(
                        self._user_credits_cache_key(user_id), new_balance
                    )
                    # Update credit info cache: balance decreased by expired_total, reserved unchanged
                    await self._update_credit_info_cache(
                        user_id, balance_delta=-expired_total, reserved_delta=0
                    )

        return expired_total



    async def get_user_credits_info(self, user_id: str) -> UserCreditInfo:
        """
        Get balance, reserved, and available credits in a single optimized call.
        
        Primary source: cache (updated on every credit modification).
        Fallback: DB (only if cache is missing or corrupted).
        
        This is more efficient than calling get_user_credits + get_reserved_credits separately.
        """
        cache_key = self._user_credits_info_cache_key(user_id)
        if self._cache:
            cached = await self._cache.get(cache_key)
            if isinstance(cached, dict):
                try:
                    return UserCreditInfo.model_validate(cached)
                except Exception:
                    # Cache corrupted, delete it and fetch from DB
                    await self._cache.delete(cache_key)

        # Cache miss or corrupted - fetch from DB and populate cache
        info = await self._db.get_user_credits_info(user_id)
        if self._cache:
            await self._cache.set(
                cache_key, info.model_dump(), ttl_seconds=300
            )
        return info

    async def get_credit_history(self, user_id: str) -> Iterable[Transaction]:
        return await self._db.get_transactions(user_id)

    async def get_expiring_credits_in_days(
        self, user_id: str, days: int
    ) -> Iterable[CreditExpiryRecord]:
        cutoff = datetime.utcnow() + timedelta(days=days)
        records = await self._db.get_credit_expiry_history(user_id)
        return [
            r for r in records if not r.expired and r.expires_at <= cutoff and r.remaining_credits > 0
        ]

    async def get_reserved_credits(self, user_id: str) -> int:
        # Aggregate all active reservations for the user
        # This is computed from ReservedCredits records.
        # A dedicated DB query/index would be better in production.
        total =await  self._db.get_reserved_credits_for_user(user_id)

        # Fallback: iterate over all plans; concrete DB backends can implement
        # a more efficient method if needed.
        # For now, reuse plan-scoped fetch and filter by user.
        # This keeps the interface small while remaining correct.
        return total

    async def reserve_credits(
        self,
        user_id: str,
        amount: int,
        reason: str | None = None,
        subscription_plan_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ReservedCredits:
        if amount <= 0:
            raise ValueError("amount must be positive")

        async with self._db.transaction():
            credit_info = await self._db.get_user_credits_info(user_id)
            if credit_info.available < amount:
                await self._ledger.log_error(
                    message="Insufficient credits for reservation",
                    details={
                        "requested": amount,
                        "balance": credit_info.balance,
                        "reserved": credit_info.reserved,
                        "available": credit_info.available,
                    },
                    user_id=user_id,
                    correlation_id=correlation_id,
                )
                raise ValueError("insufficient credits for reservation")

            reserved = ReservedCredits(
                user_id=user_id,
                subscription_plan_id=subscription_plan_id,
                credits=amount,
                reason=reason,
            )
            reserved = await self._db.add_reserved_credits(reserved)

            await self._ledger.log_transaction(
                user_id=user_id,
                message="Credits reserved",
                details={"amount": amount, "reason": reason or ""},
                correlation_id=correlation_id,
            )

            if self._cache:
                # Update credit info cache: balance unchanged, reserved increased
                await self._update_credit_info_cache(
                    user_id, balance_delta=0, reserved_delta=amount
                )

            return reserved

    async def unreserve_credits(
        self, reservation: ReservedCredits, correlation_id: str | None = None
    ) -> ReservedCredits:
        reservation.released = True
        await self._db.add_reserved_credits(reservation)

        await self._ledger.log_transaction(
            user_id=reservation.user_id,
            message="Reserved credits released",
            details={"reservation_id": reservation.id, "credits": reservation.credits},
            correlation_id=correlation_id,
        )

        if self._cache:
            # Update credit info cache: balance unchanged, reserved decreased
            await self._update_credit_info_cache(
                reservation.user_id, balance_delta=0, reserved_delta=-reservation.credits
            )

        return reservation

    async def commit_reserved_credits(
        self,
        reservation: ReservedCredits,
        description: str | None = None,
        correlation_id: str | None = None,
    ) -> Transaction:
        async with self._db.transaction():
            current = await self._db.get_user_credits(reservation.user_id)
            if current < reservation.credits:
                await self._ledger.log_error(
                    message="Insufficient credits to commit reservation",
                    details={
                        "reservation_id": reservation.id,
                        "reserved": reservation.credits,
                        "current": current,
                    },
                    user_id=reservation.user_id,
                    correlation_id=correlation_id,
                )
                raise ValueError("insufficient credits to commit reservation")

            new_balance = current - reservation.credits
            reservation.committed = True
            await self._db.add_reserved_credits(reservation)

            tx = Transaction(
                user_id=reservation.user_id,
                credits_added=0,
                credits_deducted=reservation.credits,
                current_credits=new_balance,
                transaction_type=TransactionType.COMMIT_RESERVED,
                description=description,
            )
            tx = await self._db.add_transaction(tx)

            await self._ledger.log_transaction(
                user_id=reservation.user_id,
                message="Reserved credits committed",
                details={
                    "reservation_id": reservation.id,
                    "credits": reservation.credits,
                    "new_balance": new_balance,
                },
                correlation_id=correlation_id,
            )

            if self._cache:
                await self._cache.set(
                    self._user_credits_cache_key(reservation.user_id), new_balance
                )
                # Update credit info cache: balance decreased, reserved decreased (reservation committed)
                await self._update_credit_info_cache(
                    reservation.user_id,
                    balance_delta=-reservation.credits,
                    reserved_delta=-reservation.credits,
                )

            return tx

    @staticmethod
    def _user_credits_cache_key(user_id: str) -> str:
        return f"credit:user:{user_id}:balance"

    @staticmethod
    def _user_credits_info_cache_key(user_id: str) -> str:
        return f"credit:user:{user_id}:info"

    async def _update_credit_info_cache(
        self, user_id: str, balance_delta: int, reserved_delta: int
    ) -> None:
        """
        Update the cached credit info by applying deltas.
        If cache is missing or corrupted, it will be refreshed on next get_user_credits_info call.
        """
        if not self._cache:
            return

        cache_key = self._user_credits_info_cache_key(user_id)
        cached = await self._cache.get(cache_key)

        if isinstance(cached, dict):
            try:
                current_info = UserCreditInfo.model_validate(cached)
                updated_info = UserCreditInfo(
                    balance=current_info.balance + balance_delta,
                    reserved=current_info.reserved + reserved_delta,
                    available=(current_info.balance + balance_delta)
                    - (current_info.reserved + reserved_delta),
                )
                await self._cache.set(
                    cache_key, updated_info.model_dump(), ttl_seconds=300
                )
                return
            except Exception:
                # Cache corrupted, delete it - will be refreshed on next call
                await self._cache.delete(cache_key)

    async def _invalidate_credit_info_cache(self, user_id: str) -> None:
        """
        Invalidate the cached credit info for a user.
        Kept as fallback, but _update_credit_info_cache is preferred.
        """
        if self._cache:
            await self._cache.delete(self._user_credits_info_cache_key(user_id))

