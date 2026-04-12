"""Payment Service — Credit calculation and webhook processing."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..db.base import BaseDBManager
from ..logging.ledger_logger import LedgerLogger
from ..cache.base import AsyncCacheBackend
from ..models.payment import PaymentRecord, PaymentStatus, ProviderType
from .credit_service import CreditService

logger = logging.getLogger(__name__)

_EVENT_TO_STATUS: Dict[str, PaymentStatus] = {
    "payment.authorized": PaymentStatus.AUTHORIZED,
    "payment.captured": PaymentStatus.CAPTURED,
    "payment.failed": PaymentStatus.FAILED,
    "payment_link.paid": PaymentStatus.PAID,
    "payment_link.partially_paid": PaymentStatus.PARTIALLY_PAID,
    "payment_link.expired": PaymentStatus.EXPIRED,
    "payment_link.cancelled": PaymentStatus.CANCELLED,
    "refund.created": PaymentStatus.REFUND_CREATED,
    "refund.processed": PaymentStatus.REFUND_PROCESSED,
    "refund.failed": PaymentStatus.REFUND_FAILED,
    "refund.speed_changed": PaymentStatus.REFUND_PROCESSED,
    "payment.dispute.closed": PaymentStatus.DISPUTE_CLOSED,
    "order.paid": PaymentStatus.ORDER_PAID,
    "invoice.paid": PaymentStatus.INVOICE_PAID,
    "invoice.partially_paid": PaymentStatus.INVOICE_PARTIALLY_PAID,
    "invoice.expired": PaymentStatus.INVOICE_EXPIRED,
}

_ADD_CREDIT_EVENTS = {
    "payment_link.paid",
    "payment_link.partially_paid",
    "order.paid",
    "invoice.paid",
    "invoice.partially_paid",
}

_DEDUCT_CREDIT_EVENTS = {"refund.created", "refund.processed"}

STATE_ORDER = [
    PaymentStatus.PENDING.value,
    PaymentStatus.AUTHORIZED.value,
    PaymentStatus.CAPTURED.value,
    PaymentStatus.FAILED.value,
    PaymentStatus.PAID.value,
    PaymentStatus.PARTIALLY_PAID.value,
    PaymentStatus.EXPIRED.value,
    PaymentStatus.CANCELLED.value,
    PaymentStatus.ORDER_PAID.value,
    PaymentStatus.INVOICE_PAID.value,
    PaymentStatus.INVOICE_PARTIALLY_PAID.value,
    PaymentStatus.INVOICE_EXPIRED.value,
    PaymentStatus.REFUND_CREATED.value,
    PaymentStatus.REFUND_PROCESSED.value,
    PaymentStatus.REFUND_FAILED.value,
    PaymentStatus.DISPUTE_CLOSED.value,
]


def _state_index(status_value: str) -> int:
    try:
        return STATE_ORDER.index(status_value)
    except ValueError:
        return -1


def _is_forward(current: Optional[str], new: str) -> bool:
    if not current:
        return True
    return _state_index(new) > _state_index(current)


class PaymentService:
    """Manages payment links and webhook processing with credit tracking."""

    _REFUNDED_IDS_KEY = "refunded_refund_ids"

    def __init__(
        self,
        db: BaseDBManager,
        ledger: Any,
        credit_service: CreditService,
        cache: Optional[AsyncCacheBackend] = None,
    ):
        self._db = db
        self._ledger = ledger
        self._credit_service = credit_service
        self._cache = cache
        self._providers: Dict[str, Any] = {}

    def register_provider(self, name: str, provider: Any) -> None:
        self._providers[name] = provider
        logger.info(f"Payment provider registered: {name}")

    def get_provider(self, name: str) -> Any:
        if name not in self._providers:
            raise ValueError(f"Unknown payment provider: {name}. Available: {list(self._providers.keys())}")
        return self._providers[name]

    def list_providers(self) -> List[str]:
        return list(self._providers.keys())

    def calculate_credits(self, amount_inr: float) -> float:
        base = amount_inr
        for threshold, bonus in [(5000, 1.0), (1000, 1.0)]:
            if amount_inr >= threshold:
                return base * bonus
        return base

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
    ) -> Any:
        provider = self.get_provider(provider_name)
        credits_to_add = self.calculate_credits(amount_inr)
        payment_metadata = {"credits_to_add": str(credits_to_add), **(metadata or {})}

        link = await provider.create_payment_link(
            user_id=user_id,
            amount=int(amount_inr * 100),
            currency="INR",
            description=description,
            customer_email=customer_email,
            customer_phone=customer_phone,
            metadata=payment_metadata,
        )

        record = PaymentRecord(
            id=link.payment_id,
            user_id=user_id,
            provider=ProviderType(provider_name),
            provider_payment_link_id=link.payment_id,
            amount=int(amount_inr * 100),
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
        logger.info("PaymentService: payment_link.created")
        return link

    # ─── Webhook Processing ──────────────────────────────────────────────────

    async def handle_webhook(self, provider_name: str, payload: Dict[str, Any], signature: str = "") -> Any:
        """Process webhook: provider parses payload → payment service merges with DB."""
        provider = self.get_provider(provider_name)
        event_type = payload.get("event", "")
        target_status = _EVENT_TO_STATUS.get(event_type)

        # 1. Parse webhook into a PaymentRecord (provider does parsing)
        provider_record: Optional[PaymentRecord] = await provider.handle_webhook_event(payload)
        if not provider_record:
            logger.warning(f"PaymentService webhook: provider returned no record for {event_type}")
            return self._result(False, error="no_payment_record_from_provider")

        trace = (
            f"plink={provider_record.provider_payment_link_id or '-'}"
            f":pay={provider_record.provider_payment_id or '-'}"
            f":order={provider_record.provider_order_id or '-'}"
            f":event={event_type}"
        )

        # 2. Verify signature
        try:
            provider.verify_webhook_signature(payload, signature)
        except ValueError:
            logger.warning(f"PaymentService webhook: signature failed [{trace}]")
            return self._result(False, error="invalid_signature")

        # 3. Find existing record by plink_id, order_id, or payment_id
        existing = await self._find_record(
            provider_record.provider_payment_link_id,
            provider_record.provider_order_id,
            provider_record.provider_payment_id,
        )

        # 4. Validate immutable fields if existing record found
        if existing:
            mismatch = self._validate_immutable_fields(existing, provider_record, trace)
            if mismatch:
                logger.error(f"PaymentService webhook: immutable field mismatch [{trace}]: {mismatch}")

        # 5. State machine: skip if not moving forward
        current_status = existing.status if existing else None
        target_value = target_status.value if target_status else None
        if target_value and not _is_forward(current_status, target_value):
            logger.info(
                f"PaymentService webhook: not forward move current={current_status} new={target_value} [{trace}]"
            )
            return self._result(
                True,
                payment_id=existing.id if existing else provider_record.id,
                user_id=(existing.user_id if existing else provider_record.user_id),
                amount=(existing.amount_inr if existing else provider_record.amount_inr),
                credits_added=(existing.credits_added if existing else 0),
                status=current_status or "unknown",
                idempotent=True,
            )

        # 6. Process based on event type
        if event_type in _ADD_CREDIT_EVENTS:
            return await self._process_add_credits(existing, provider_record, trace, target_status)

        if event_type in _DEDUCT_CREDIT_EVENTS:
            return await self._process_refund(existing, event_type, trace)

        # 7. Other events — update status/IDs
        if existing:
            await self._merge_and_save(existing, provider_record, target_status)
        else:
            await self._db.add_payment_record(provider_record)

        logger.info(f"PaymentService webhook: {event_type} → {target_value} [{trace}]")
        return self._result(
            True,
            payment_id=existing.provider_payment_id if existing else provider_record.provider_payment_id,
            user_id=(existing.user_id if existing else provider_record.user_id),
            amount=(existing.amount_inr if existing else provider_record.amount_inr),
            credits_added=(existing.credits_added if existing else 0),
            status=provider_record.status,
        )

    async def _find_record(
        self, plink_id: Optional[str], order_id: Optional[str], payment_id: Optional[str]
    ) -> Optional[PaymentRecord]:
        """Find existing record by any available ID."""
        if plink_id:
            rec = await self._db.get_payment_record(plink_id)
            if rec:
                return rec
        if order_id:
            rec = await self._db.get_payment_by_order_id(order_id)
            if rec:
                return rec
        if payment_id:
            return await self._db.get_payment_by_provider_id(payment_id)
        return None

    @staticmethod
    def _validate_immutable_fields(
        existing: PaymentRecord, provider_record: PaymentRecord, trace: str
    ) -> Optional[str]:
        """Validate immutable fields match. Return mismatch description or None."""
        issues = []
        # user_id should match
        if existing.user_id and provider_record.user_id and existing.user_id != provider_record.user_id:
            issues.append(f"user_id: existing={existing.user_id}, provider={provider_record.user_id}")
        # amount should match (if provider has it)
        if provider_record.amount and existing.amount and existing.amount != provider_record.amount:
            issues.append(f"amount: existing={existing.amount}, provider={provider_record.amount}")
        if provider_record.amount_inr and existing.amount_inr and existing.amount_inr != provider_record.amount_inr:
            issues.append(f"amount_inr: existing={existing.amount_inr}, provider={provider_record.amount_inr}")
        return "; ".join(issues) if issues else None

    async def _process_add_credits(
        self,
        existing: Optional[PaymentRecord],
        provider_record: PaymentRecord,
        trace: str,
        target_status: Optional[PaymentStatus],
    ) -> Any:
        """Add credits using atomic update to prevent race conditions."""
        # Ensure record exists
        if not existing:
            await self._db.add_payment_record(provider_record)
            existing = provider_record

        if existing.credits_added > 0:
            return self._result(
                True,
                payment_id=existing.id,
                user_id=existing.user_id,
                amount=existing.amount_inr,
                credits_added=existing.credits_added,
                status=existing.status,
                idempotent=True,
            )

        # Calculate credits
        credits = (
            existing.credits_to_add if existing.credits_to_add > 0 else self.calculate_credits(existing.amount_inr)
        )
        status_val = target_status.value if target_status else PaymentStatus.PAID.value

        # Atomic update: sets credits, status, and provider IDs in one operation
        updated = await self._db.update_payment_record_atomic(
            existing.id,
            credits,
            status_val,
            provider_record.provider_payment_id,
            provider_record.provider_order_id,
        )
        if not updated:
            # Another process already added credits
            rec = await self._db.get_payment_record(existing.id)
            return self._result(
                True,
                payment_id=existing.id,
                user_id=existing.user_id,
                amount=existing.amount_inr,
                credits_added=(rec.credits_added if rec else credits),
                status=(rec.status if rec else status_val),
                idempotent=True,
            )

        # Record atomic — now add credits to user
        try:
            await self._credit_service.add_credits(
                user_id=existing.user_id,
                amount=credits,
                description=f"Payment: {existing.id}",
                correlation_id=existing.id,
            )
        except Exception:
            logger.error(f"PaymentService webhook: failed to add credits to user [{trace}]")
            return self._result(False, error="credit_addition_failed")

        logger.info(f"PaymentService webhook: credits_added={credits} [{trace}]")
        return self._result(
            True,
            payment_id=existing.id,
            user_id=existing.user_id,
            amount=existing.amount_inr,
            credits_added=credits,
            status=status_val,
        )

    async def _process_refund(self, existing: Optional[PaymentRecord], event_type: str, trace: str) -> Any:
        """Handle refund events — deduct credits if not already done."""
        if event_type == "refund.failed":
            logger.warning(f"PaymentService webhook: refund failed [{trace}]")
            return self._result(False, status="refund_failed", error="refund_failed")

        if not existing:
            logger.error(f"PaymentService webhook: refund but no payment record [{trace}]")
            return self._result(False, error="payment_not_found")

        # For now, just update status. Refund credit deduction logic can be added later.
        existing.status = event_type
        await self._db.add_payment_record(existing)

        logger.info(f"PaymentService webhook: {event_type} [{trace}]")
        return self._result(
            True,
            payment_id=existing.id,
            user_id=existing.user_id,
            amount=existing.amount_inr,
            credits_added=0,
            status=event_type,
        )

    async def _merge_and_save(
        self, existing: PaymentRecord, provider_record: PaymentRecord, target_status: Optional[PaymentStatus]
    ) -> None:
        """Merge provider data into existing record and save."""
        # Update mutable fields if null
        if provider_record.provider_payment_id and not existing.provider_payment_id:
            existing.provider_payment_id = provider_record.provider_payment_id
        if provider_record.provider_order_id and not existing.provider_order_id:
            existing.provider_order_id = provider_record.provider_order_id
        if target_status:
            existing.status = target_status.value
        await self._db.add_payment_record(existing)

    @staticmethod
    def _result(
        success: bool,
        payment_id: Optional[str] = None,
        user_id: Optional[str] = None,
        amount: float = 0,
        credits_added: float = 0,
        status: str = "",
        idempotent: bool = False,
        error: Optional[str] = None,
    ) -> Any:
        from ..models.payment import PaymentResult

        return PaymentResult(
            success=success,
            payment_id=payment_id,
            user_id=user_id,
            amount=amount,
            credits_added=credits_added,
            status=status,
            idempotent=idempotent,
            error=error or (None if success else "unknown"),
        )

    # ─── Payment History ─────────────────────────────────────────────────────

    async def get_payment_by_id(self, payment_id: str, user_id: str) -> Optional[PaymentRecord]:
        rec = await self._db.get_payment_record(payment_id)
        if rec and rec.user_id == user_id:
            return rec
        return None

    async def get_payment_history(self, user_id: str, limit: int = 20, skip: int = 0) -> Dict:
        records, total = await self._get_user_payments(user_id, limit, skip)
        return {"payments": [self._record_to_dict(r) for r in records], "total": total}

    async def _get_user_payments(self, user_id: str, limit: int, skip: int):
        return [], 0

    @staticmethod
    def _record_to_dict(rec: PaymentRecord) -> Dict:
        return {
            "id": rec.id or "",
            "user_id": rec.user_id,
            "provider": rec.provider.value if hasattr(rec.provider, "value") else str(rec.provider),
            "provider_payment_id": rec.provider_payment_id,
            "provider_payment_link_id": rec.provider_payment_link_id,
            "amount": rec.amount,
            "currency": rec.currency,
            "amount_inr": rec.amount_inr,
            "credits_to_add": rec.credits_to_add,
            "credits_added": rec.credits_added,
            "status": rec.status.value if hasattr(rec.status, "value") else str(rec.status),
            "payment_method": rec.payment_method,
            "description": rec.description,
            "created_at": rec.created_at.isoformat() if rec.created_at else None,
            "completed_at": rec.completed_at.isoformat() if rec.completed_at else None,
        }
