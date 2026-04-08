"""
Payment Service — Central orchestrator for payment providers.

Manages:
- Provider registration and selection
- Payment link creation (calls through to provider)
- Webhook processing (verifies → processes → adds credits → updates ledger)
- Payment history queries
- Credit calculation with bonus tiers
- Idempotency (prevents double-crediting)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from ..cache.base import AsyncCacheBackend
from ..db.base import BaseDBManager
from ..logging.ledger_logger import LedgerLogger
from ..models.payment import (
    PaymentLinkResponse,
    PaymentRecord,
    PaymentResult,
    PaymentStatus,
    ProviderType,
)
from ..providers.base import PaymentProvider
from .credit_service import CreditService

logger = logging.getLogger(__name__)

# ─── Default Bonus Tiers ─────────────────────────────────────────────────────
# (amount_inr → bonus_multiplier)
DEFAULT_BONUS_TIERS = {
    1000: 1.1,   # 10% bonus for ₹1000+
    5000: 1.2,   # 20% bonus for ₹5000+
}


class PaymentService:
    """
    Central payment orchestration service.

    Ties together:
    - Payment providers (Razorpay, Stripe, etc.)
    - Credit service (adds credits after successful payment)
    - Database (stores payment records)
    - Ledger (logs all payment events)

    Usage:
        payment_service = PaymentService(db, ledger, credit_service)
        payment_service.register_provider("razorpay", razorpay_provider)

        # Create payment
        link = await payment_service.create_payment_link(
            user_id="user-123",
            amount_inr=500,
            provider="razorpay",
        )

        # Handle webhook
        result = await payment_service.handle_webhook(
            provider_name="razorpay",
            payload=webhook_data,
            signature=signature,
        )
    """

    def __init__(
        self,
        db: BaseDBManager,
        ledger: LedgerLogger,
        credit_service: CreditService,
        cache: Optional[AsyncCacheBackend] = None,
        credits_per_inr: float = 1.0,
        bonus_tiers: Optional[Dict[float, float]] = None,
    ):
        self._db = db
        self._ledger = ledger
        self._credit_service = credit_service
        self._cache = cache
        self._credits_per_inr = credits_per_inr
        self._bonus_tiers = bonus_tiers or DEFAULT_BONUS_TIERS

        self._providers: Dict[str, PaymentProvider] = {}

    # ─── Provider Registration ───────────────────────────────────────────────

    def register_provider(self, name: str, provider: PaymentProvider) -> None:
        """
        Register a payment provider.

        Args:
            name: Provider identifier (e.g., "razorpay", "stripe")
            provider: PaymentProvider implementation
        """
        self._providers[name] = provider
        logger.info(f"Payment provider registered: {name}")

    def get_provider(self, name: str) -> PaymentProvider:
        """Get a registered provider by name."""
        if name not in self._providers:
            available = ", ".join(self._providers.keys())
            raise ValueError(f"Unknown payment provider: {name}. Available: {available}")
        return self._providers[name]

    def list_providers(self) -> list[str]:
        """List registered provider names."""
        return list(self._providers.keys())

    # ─── Credit Calculation ──────────────────────────────────────────────────

    def calculate_credits(self, amount_inr: float) -> float:
        """
        Calculate credits to add based on amount with bonus tiers.

        Args:
            amount_inr: Amount in INR

        Returns:
            Number of credits to add
        """
        base_credits = amount_inr * self._credits_per_inr

        # Apply highest applicable bonus tier
        multiplier = 1.0
        for threshold, bonus in sorted(self._bonus_tiers.items()):
            if amount_inr >= threshold:
                multiplier = bonus

        return base_credits * multiplier

    # ─── Payment Link Creation ───────────────────────────────────────────────

    async def create_payment_link(
        self,
        user_id: str,
        amount_inr: float,
        provider_name: str = "razorpay",
        description: str = "Credit top-up",
        customer_email: Optional[str] = None,
        customer_phone: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PaymentLinkResponse:
        """
        Create a hosted payment link.

        Flow:
        1. Get the provider
        2. Calculate credits to add
        3. Create payment link via provider
        4. Save payment record to DB

        Args:
            user_id: User who will receive credits
            amount_inr: Amount in INR
            provider_name: Which provider to use (default: "razorpay")
            description: Payment description
            customer_email: Customer email
            customer_phone: Customer phone
            metadata: Additional metadata

        Returns:
            PaymentLinkResponse with payment URL
        """
        provider = self.get_provider(provider_name)
        credits_to_add = self.calculate_credits(amount_inr)

        # Create payment link via provider (amount in paise)
        amount_paise = int(amount_inr * 100)

        payment_metadata = {
            "credits_to_add": str(credits_to_add),
            **(metadata or {}),
        }

        link = await provider.create_payment_link(
            user_id=user_id,
            amount=amount_paise,
            currency="INR",
            description=description,
            customer_email=customer_email,
            customer_phone=customer_phone,
            metadata=payment_metadata,
        )

        # Save payment record to DB
        record = PaymentRecord(
            id=link.payment_id,
            user_id=user_id,
            provider=ProviderType(provider_name),
            provider_payment_link_id=link.payment_id,
            amount=amount_paise,
            currency="INR",
            amount_inr=amount_inr,
            credits_to_add=credits_to_add,
            status=PaymentStatus.PENDING,
            description=description,
            customer_email=customer_email,
            customer_phone=customer_phone,
            metadata={"payment_url": link.payment_url},
        )

        await self._db.add_payment_record(record)

        await self._ledger.log_transaction(
            user_id=user_id,
            message="Payment link created",
            details={
                "payment_id": link.payment_id,
                "provider": provider_name,
                "amount_inr": amount_inr,
                "credits_to_add": credits_to_add,
            },
        )

        logger.info(
            f"Payment link created: {link.payment_id} | "
            f"User: {user_id} | "
            f"Amount: ₹{amount_inr} → {credits_to_add} credits"
        )

        return link

    # ─── Webhook Processing ──────────────────────────────────────────────────

    async def handle_webhook(
        self,
        provider_name: str,
        payload: Dict[str, Any],
        signature: str = "",
    ) -> PaymentResult:
        """
        Process a payment webhook event.

        Flow:
        1. Get the provider
        2. Verify webhook signature
        3. Process the event via provider
        4. Check idempotency (already processed?)
        5. Add credits to user account
        6. Update payment record in DB
        7. Log to ledger

        Args:
            provider_name: Which provider sent the webhook
            payload: Webhook payload
            signature: Webhook signature string

        Returns:
            PaymentResult with processing outcome
        """
        provider = self.get_provider(provider_name)

        # 1. Verify signature
        try:
            provider.verify_webhook_signature(payload, signature)
        except ValueError as e:
            logger.warning(f"Webhook signature verification failed: {e}")
            return PaymentResult(success=False, status="error", error="invalid_signature")

        # 2. Process event via provider
        event_result = await provider.handle_webhook_event(payload)

        if not event_result.success and event_result.status != "ignored":
            # Payment failed — update record
            if event_result.payment_id:
                await self._update_payment_status(
                    payment_id=event_result.payment_id,
                    status=PaymentStatus.FAILED,
                    error=event_result.error,
                )
            return event_result

        if event_result.status == "ignored":
            return event_result

        # 3. Idempotency check — already processed?
        if event_result.payment_id:
            existing = await self._db.get_payment_record(event_result.payment_id)
            if existing and existing.status == PaymentStatus.CAPTURED.value:
                logger.info(f"Payment already processed (idempotent): {event_result.payment_id}")
                return PaymentResult(
                    success=True,
                    payment_id=event_result.payment_id,
                    user_id=existing.user_id,
                    amount=existing.amount_inr,
                    credits_added=existing.credits_added,
                    status="captured",
                    idempotent=True,
                )

        # 4. Calculate credits
        credits_to_add = self.calculate_credits(event_result.amount)
        user_id = event_result.user_id

        if not user_id:
            # Try to get from existing record
            if event_result.payment_id:
                existing = await self._db.get_payment_record(event_result.payment_id)
                if existing:
                    user_id = existing.user_id

        if not user_id:
            return PaymentResult(success=False, status="error", error="missing_user_id")

        # 5. Add credits to user account
        try:
            tx = await self._credit_service.add_credits(
                user_id=user_id,
                amount=credits_to_add,
                description=f"Payment: {event_result.payment_id} — ₹{event_result.amount}",
                correlation_id=event_result.payment_id,
            )
            logger.info(
                f"Credits added: user={user_id}, credits={credits_to_add}, tx_id={tx.id}"
            )
        except Exception as e:
            logger.error(f"Failed to add credits for {event_result.payment_id}: {e}")
            return PaymentResult(
                success=False,
                status="error",
                error="credit_addition_failed",
                payment_id=event_result.payment_id,
            )

        # 6. Update payment record
        if event_result.payment_id:
            await self._update_payment_status(
                payment_id=event_result.payment_id,
                status=PaymentStatus.CAPTURED,
                credits_added=credits_to_add,
                payment_method=getattr(event_result, "payment_method", None),
            )

        # 7. Log to ledger
        await self._ledger.log_transaction(
            user_id=user_id,
            message="Payment captured — credits added",
            details={
                "payment_id": event_result.payment_id,
                "amount_inr": event_result.amount,
                "credits_added": credits_to_add,
                "provider": provider_name,
            },
            correlation_id=event_result.payment_id,
        )

        return PaymentResult(
            success=True,
            payment_id=event_result.payment_id,
            user_id=user_id,
            amount=event_result.amount,
            credits_added=credits_to_add,
            status="captured",
        )

    # ─── Payment History ─────────────────────────────────────────────────────

    async def get_payment_history(
        self, user_id: str, limit: int = 20, skip: int = 0
    ) -> tuple[Iterable[PaymentRecord], int]:
        """Get payment history for a user."""
        records = await self._db.get_payment_records_by_user(user_id, limit=limit, skip=skip)
        total = await self._db.count_payment_records(user_id)
        return records, total

    async def get_payment_by_id(self, payment_id: str, user_id: str) -> Optional[PaymentRecord]:
        """Get a specific payment record (user-scoped)."""
        return await self._db.get_payment_record(payment_id, user_id=user_id)

    # ─── Internal Helpers ────────────────────────────────────────────────────

    async def _update_payment_status(
        self,
        payment_id: str,
        status: PaymentStatus,
        credits_added: float = 0,
        payment_method: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update a payment record's status."""
        record = await self._db.get_payment_record(payment_id)
        if not record:
            logger.warning(f"Payment record not found: {payment_id}")
            return

        record.status = status
        record.credits_added = credits_added

        if status == PaymentStatus.CAPTURED:
            record.completed_at = datetime.utcnow()
            if payment_method:
                record.payment_method = payment_method

        if status == PaymentStatus.FAILED:
            record.failed_at = datetime.utcnow()
            record.error_message = error

        await self._db.add_payment_record(record)
