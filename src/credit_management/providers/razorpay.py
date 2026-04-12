"""Razorpay Payment Provider"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

import razorpay

from ..models.payment import PaymentRecord, PaymentLinkResponse, PaymentResult, PaymentStatus, ProviderType
from .base import PaymentProvider

logger = logging.getLogger(__name__)


def _entity(obj):
    """Unwrap entity wrapper if present (Razorpay wraps data in {entity: {...}})."""
    if obj and isinstance(obj, dict):
        return obj.get("entity") or obj
    return obj or {}


def _get(obj, key):
    """Get value from object, drilling into entity wrapper if needed."""
    if not obj or not isinstance(obj, dict):
        return None
    return obj.get(key) or (_get(obj.get("entity"), key) if obj.get("entity") else None)


def _notes(obj):
    """Get notes dict from object, drilling into entity wrapper if needed."""
    if not obj or not isinstance(obj, dict):
        return {}
    n = obj.get("notes")
    if n and isinstance(n, dict):
        return n
    n = (_get(obj, "entity") or {}).get("notes")
    return n if isinstance(n, dict) else {}


class RazorpayProvider(PaymentProvider):
    """Razorpay payment gateway provider."""

    def __init__(
        self,
        key_id: str,
        key_secret: str,
        webhook_secret: Optional[str] = None,
        callback_url: Optional[str] = None,
        app_base_url: Optional[str] = None,
        audit_repo: Any = None,
    ):
        self.key_id = key_id
        self.key_secret = key_secret
        self.webhook_secret = webhook_secret
        self.callback_url = callback_url or f"{app_base_url or 'http://localhost:8000'}/api/payments/success"
        self.audit_repo = audit_repo
        self._client = razorpay.Client(auth=(key_id, key_secret))
        self._is_test_mode = key_id.startswith("rzp_test_")

    @property
    def provider_name(self) -> str:
        return ProviderType.RAZORPAY.value

    async def create_payment_link(
        self,
        user_id: str,
        amount: float,
        currency: str = "INR",
        description: str = "Payment",
        customer_email: Optional[str] = None,
        customer_phone: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PaymentLinkResponse:
        import hashlib

        ts = str(int(datetime.utcnow().timestamp()))
        user_hash = hashlib.md5(user_id.encode()).hexdigest()[:8]
        ref_id = f"payl_{user_hash}_{ts}"

        link_data = {
            "amount": int(amount),
            "currency": currency,
            "accept_partial": False,
            "first_min_partial_amount": 0,
            "description": description,
            "reference_id": ref_id,
            "notify": {"email": True, "sms": True},
            "callback_url": self.callback_url,
            "notes": {"user_id": user_id, "purpose": "credit_topup"},
        }
        if metadata:
            link_data["notes"].update(metadata)
        if customer_email or customer_phone:
            link_data["customer"] = {}
            if customer_email:
                link_data["customer"]["email"] = customer_email
            if customer_phone:
                link_data["customer"]["contact"] = customer_phone

        try:
            link = self._client.payment_link.create(link_data)
            http_status = 200
        except Exception as e:
            logger.error("Razorpay payment_link.create failed")
            if self.audit_repo:
                try:
                    await self.audit_repo.log_outbound(
                        payment_link_id="unknown",
                        user_id=user_id,
                        event_type="payment_link.created",
                        request_payload=link_data,
                        response_payload={"error": str(e)},
                        http_status=500,
                    )
                except Exception:
                    pass
            raise

        razorpay_link_id = link.get("id")
        short_url = link.get("short_url")

        logger.info("Razorpay payment_link.created")
        return PaymentLinkResponse(
            payment_id=link.get("reference_id", ref_id),  # Use our reference_id, not Razorpay's plink_id
            provider_payment_link_id=razorpay_link_id,
            provider=ProviderType.RAZORPAY,
            payment_url=short_url,
            amount=amount / 100 if amount > 100 else amount,
            currency=currency,
            credits_to_add=0,
            status="pending",
        )

    def verify_webhook_signature(self, payload: Dict[str, Any], signature: str, secret: Optional[str] = None) -> bool:
        webhook_secret = secret or self.webhook_secret
        if not webhook_secret:
            logger.warning("RAZORPAY_WEBHOOK_SECRET not set — skipping signature verification")
            return True
        body_bytes = json.dumps(payload, separators=(",", ":")).encode()
        expected = hmac.new(webhook_secret.encode(), body_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("Invalid Razorpay webhook signature")
        return True

    async def handle_webhook_event(self, payload: Dict[str, Any]) -> Optional[PaymentRecord]:
        """Parse webhook payload and return a PaymentRecord with all extractable fields."""
        event_type = payload.get("event", "")
        if self._is_test_mode:
            logger.info(
                f"Razorpay webhook [{event_type}] — FULL PAYLOAD:\n{json.dumps(payload, indent=2, default=str)}"
            )

        raw = payload.get("payload", {})
        payment_link = _entity(raw.get("payment_link"))
        payment = _entity(raw.get("payment"))
        order = _entity(raw.get("order"))
        refund = _entity(raw.get("refund"))

        if not payment_link and not payment and not order and not refund:
            logger.warning(f"Razorpay webhook [{event_type}] has no recognizable entities")
            return None

        # Extract all available fields
        plink_id = _get(payment_link, "id")
        payment_id = _get(payment, "id") or _get(refund, "payment_id")
        order_id = _get(order, "id") or _get(payment, "order_id") or _get(payment_link, "order_id")
        refund_id = _get(refund, "id")
        reference_id = _get(payment_link, "reference_id")
        user_id = _notes(payment_link).get("user_id") or _notes(payment).get("user_id")
        amount_paise = _get(payment, "amount") or _get(payment_link, "amount")
        amount = amount_paise if amount_paise else 0
        amount_inr = amount / 100
        method = _get(payment, "method")
        status_str = _get(payment_link, "status") or _get(payment, "status")
        if not reference_id:
            reference_id = (payment.get("notes", {})).get("reference_id", None) or (order.get("notes", {})).get(
                "reference_id", None
            )

        # Map Razorpay status to our PaymentStatus
        if status_str == "paid" or status_str == "captured":
            status = PaymentStatus.PAID
        elif status_str == "expired":
            status = PaymentStatus.EXPIRED
        elif status_str == "cancelled":
            status = PaymentStatus.CANCELLED
        elif status_str == "authorized":
            status = PaymentStatus.AUTHORIZED
        elif status_str == "failed":
            status = PaymentStatus.FAILED
        else:
            status = PaymentStatus.PENDING

        return PaymentRecord(
            id=reference_id,
            user_id=user_id or "",
            provider=ProviderType.RAZORPAY,
            provider_payment_link_id=plink_id,
            provider_payment_id=payment_id,
            provider_order_id=order_id,
            amount=amount,
            currency="INR",
            amount_inr=amount_inr,
            credits_to_add=0,  # Will be calculated from amount_inr when adding credits
            credits_added=0,
            status=status,
            payment_method=method,
            description=_get(payment_link, "description") or f"Razorpay {event_type}",
            metadata={
                "event_type": event_type,
                "refund_id": refund_id,
                "reference_id": reference_id,
            },
        )
