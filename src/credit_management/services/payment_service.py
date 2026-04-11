"""Payment Service — Credit calculation and webhook processing."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..db.base import BaseDBManager
from ..logging.ledger_logger import LedgerLogger
from ..cache.base import AsyncCacheBackend
from ..models.payment import PaymentRecord, PaymentStatus, ProviderType
from ..models.razorpay import WebhookEvent
from .credit_service import CreditService

logger = logging.getLogger(__name__)

# ─── Event → Status Mapping ───────────────────────────────────────────────────
# Single source of truth: maps provider's native event names to PaymentStatus.
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
    "payment.dispute.closed": PaymentStatus.DISPUTE_CLOSED,
    "order.paid": PaymentStatus.ORDER_PAID,
    "invoice.paid": PaymentStatus.INVOICE_PAID,
    "invoice.partially_paid": PaymentStatus.INVOICE_PARTIALLY_PAID,
    "invoice.expired": PaymentStatus.INVOICE_EXPIRED,
}

# Events that add credits to user.
_ADD_CREDIT_EVENTS = {
    "payment_link.paid",
    "payment_link.partially_paid",
    "order.paid",
    "invoice.paid",
    "invoice.partially_paid",
}

# Events that deduct credits from user.
_DEDUCT_CREDIT_EVENTS = {"refund.created", "refund.processed"}

# Ordered list defining forward state progression.
# Events later in this list are "ahead" of earlier ones.
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
    """Check if the new state is ahead of the current state."""
    if not current:
        return True
    return _state_index(new) > _state_index(current)


class PaymentService:
    """Manages payment links and webhook processing with credit tracking."""

    _REFUNDED_IDS_KEY = "refunded_refund_ids"

    def __init__(
        self, db: BaseDBManager, ledger: Any, credit_service: CreditService, cache: Optional[AsyncCacheBackend] = None
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
        for threshold, bonus in [(5000, 1.2), (1000, 1.1)]:
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
        provider = self.get_provider(provider_name)
        event_type = payload.get("event", "")
        target_status = _EVENT_TO_STATUS.get(event_type)

        try:
            webhook = WebhookEvent(**payload)
            pl = webhook.get_payment_link()
            plink_id = pl.id if pl else None
        except Exception:
            webhook = None
            plink_id = None

        trace = f"plink={plink_id}" if plink_id else f"event={event_type}"

        try:
            provider.verify_webhook_signature(payload, signature)
        except ValueError:
            logger.warning(f"PaymentService webhook: signature failed [{trace}]")
            return self._result(False, error="invalid_signature")

        provider_result = await provider.handle_webhook_event(payload)
        existing = await self._find_record(provider_result, webhook)

        # Skip if state is not moving forward (duplicate or backward)
        current_status = existing.status if existing else None
        target_value = target_status.value if target_status else None
        if target_value and not _is_forward(current_status, target_value):
            logger.info(
                f"PaymentService webhook: not forward move current={current_status} new={target_value} [{trace}]"
            )
            return self._result(
                True,
                payment_id=provider_result.payment_id,
                user_id=existing.user_id if existing else None,
                amount=existing.amount_inr if existing else 0,
                credits_added=existing.credits_added if existing else 0,
                status=current_status or "unknown",
                idempotent=True,
            )

        # Dispatch by credit action
        if event_type in _ADD_CREDIT_EVENTS:
            return await self._add_credits(existing, provider_result, webhook, trace, target_status)

        if event_type in _DEDUCT_CREDIT_EVENTS:
            return await self._handle_refund(existing, webhook, event_type, trace)

        # No credit action — just update status
        if target_status and existing:
            await self._update_payment_status(payment_id=existing.id, status=target_status)
        elif target_status and provider_result and provider_result.payment_id:
            await self._update_payment_status(payment_id=provider_result.payment_id, status=target_status)

        logger.info(f"PaymentService webhook: {event_type} → {target_value} [{trace}]")
        return provider_result

    async def _find_record(self, result: Any, webhook: Optional[WebhookEvent]) -> Optional[PaymentRecord]:
        if result and result.payment_id:
            rec = await self._db.get_payment_record(result.payment_id)
            if rec:
                return rec
        if webhook:
            pl = webhook.get_payment_link()
            if pl:
                rec = await self._db.get_payment_record(pl.id)
                if rec:
                    return rec
            r = webhook.get_refund()
            if r and r.payment_id:
                return await self._db.get_payment_record(r.payment_id)
        return None

    async def _add_credits(
        self,
        existing: Optional[PaymentRecord],
        result: Any,
        webhook: Optional[WebhookEvent],
        trace: str,
        target_status: Optional[PaymentStatus],
    ) -> Any:
        user_id = webhook.get_user_id() if webhook else (result.user_id if result else None)
        amount_inr = 0
        if webhook:
            p = webhook.get_payment()
            if p:
                amount_inr = p.amount / 100
            pl = webhook.get_payment_link()
            if pl:
                amount_inr = amount_inr or pl.amount / 100
        elif result:
            amount_inr = result.amount

        if not user_id:
            return self._result(False, error="missing_user_id")

        if not existing and webhook and amount_inr > 0:
            existing = await self._create_from_webhook(result, webhook)

        credits = self.calculate_credits(amount_inr)

        # Skip if credits already added
        if existing and existing.credits_added > 0:
            return self._result(
                True,
                payment_id=existing.id,
                user_id=existing.user_id,
                amount=existing.amount_inr,
                credits_added=existing.credits_added,
                status=existing.status,
                idempotent=True,
            )

        try:
            await self._credit_service.add_credits(
                user_id=user_id,
                amount=credits,
                description=f"Payment: {result.payment_id if result else 'webhook'}",
                correlation_id=result.payment_id if result else None,
            )
        except Exception:
            logger.error(f"PaymentService webhook: failed to add credits [{trace}]")
            return self._result(False, error="credit_addition_failed")

        record_id = existing.id if existing else (result.payment_id if result else None)
        if record_id:
            pm = getattr(result, "payment_method", None) if result else None
            await self._update_payment_status(
                payment_id=record_id,
                status=target_status or PaymentStatus.PAID,
                credits_added=credits,
                payment_method=pm,
            )

        final_status = target_status.value if target_status else "paid"
        logger.info(f"PaymentService webhook: credits_added={credits} [{trace}]")
        return self._result(
            True, payment_id=record_id, user_id=user_id, amount=amount_inr, credits_added=credits, status=final_status
        )

    async def _handle_refund(
        self, existing: Optional[PaymentRecord], webhook: Optional[WebhookEvent], event_type: str, trace: str
    ) -> Any:
        if not webhook:
            return self._result(False, error="missing_webhook_data")

        r = webhook.get_refund()
        if not r:
            return self._result(False, error="missing_refund_entity")

        if not existing:
            if r.payment_id:
                existing = await self._db.get_payment_record(r.payment_id)
            if not existing:
                logger.error(f"PaymentService webhook: refund but no payment record [{trace}]")
                return self._result(False, error="payment_not_found")

        # Check idempotency
        refunded_ids = existing.metadata.get(self._REFUNDED_IDS_KEY, [])
        if r.id in refunded_ids:
            return self._result(True, payment_id=r.id, status="refunded", idempotent=True)

        if event_type == "refund.failed":
            logger.warning(f"PaymentService webhook: refund failed [{trace}]")
            return self._result(False, status="refund_failed", error="refund_failed")

        # Deduct credits
        refund_inr = r.amount / 100
        credits_to_deduct = self.calculate_credits(refund_inr)
        if credits_to_deduct > 0:
            try:
                await self._credit_service.deduct_credits_after_service(
                    user_id=existing.user_id,
                    amount=credits_to_deduct,
                    description=f"Refund: {r.id}",
                    correlation_id=r.id,
                )
            except Exception:
                logger.error(f"PaymentService webhook: failed to deduct refund credits [{trace}]")
                return self._result(False, error="credit_deduction_failed")

        # Track refund ID
        refunded_ids.append(r.id)
        existing.metadata[self._REFUNDED_IDS_KEY] = refunded_ids
        await self._db.add_payment_record(existing)

        logger.info(f"PaymentService webhook: {event_type} → credits_deducted={credits_to_deduct} [{trace}]")
        return self._result(
            True,
            payment_id=r.id,
            user_id=existing.user_id,
            amount=refund_inr,
            credits_added=-credits_to_deduct,
            status="refunded",
        )

    async def _create_from_webhook(self, result: Any, webhook: WebhookEvent) -> Optional[PaymentRecord]:
        pl = webhook.get_payment_link()
        if not pl:
            return None
        p = webhook.get_payment()
        o = webhook.get_order()
        amount_paise = p.amount if p else pl.amount
        amount_inr = amount_paise / 100 if amount_paise else 0

        record = PaymentRecord(
            id=pl.id,
            user_id=webhook.get_user_id() or (result.user_id if result else ""),
            provider=ProviderType.RAZORPAY,
            provider_payment_link_id=pl.id,
            provider_payment_id=p.id if p else None,
            provider_order_id=o.id if o else None,
            amount=amount_paise,
            currency="INR",
            amount_inr=amount_inr,
            credits_to_add=self.calculate_credits(amount_inr),
            credits_added=0,
            status=PaymentStatus.PENDING,
            description=pl.description or "Payment from webhook",
            metadata={"razorpay_order_id": o.id if o else None, "created_from": "webhook_fallback"},
        )
        await self._db.add_payment_record(record)
        return record

    async def _update_payment_status(
        self,
        payment_id: str,
        status: PaymentStatus,
        credits_added: float = 0,
        payment_method: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        rec = await self._db.get_payment_record(payment_id)
        if not rec:
            logger.warning(f"Payment record not found: {payment_id}")
            return
        rec.status = status.value
        if credits_added:
            rec.credits_added = credits_added
        if payment_method:
            rec.payment_method = payment_method
        if error:
            rec.error_message = error
        await self._db.add_payment_record(rec)

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
