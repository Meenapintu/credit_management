"""
Razorpay Payment Provider

Implements the PaymentProvider interface for Razorpay gateway.
Supports payment links, webhook verification, and event processing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

import razorpay

from ..models.payment import PaymentLinkResponse, PaymentResult, ProviderType
from .base import PaymentProvider

logger = logging.getLogger(__name__)


def _safe_get(d: Dict[str, Any], path: str) -> Optional[Any]:
    """Safely navigate nested dict using dot notation.

    E.g. _safe_get(payload, "payment_link.entity.reference_id")
    """
    keys = path.split(".")
    current: Any = d
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


class RazorpayProvider(PaymentProvider):
    """
    Razorpay payment gateway provider.

    Supports:
    - Payment link creation (hosted checkout)
    - Webhook signature verification (HMAC-SHA256)
    - Payment event processing (paid, failed, captured)

    Usage:
        provider = RazorpayProvider(
            key_id="rzp_test_xxx",
            key_secret="xxx",
            webhook_secret="whsec_xxx",  # Optional
            callback_url="https://yourapp.com/payments/success",
        )

        # Create payment link
        link = await provider.create_payment_link(
            user_id="user-123",
            amount=50000,  # 500 INR in paise
            description="Credit top-up",
        )

        # Handle webhook
        result = await provider.handle_webhook_event(payload)
    """

    def __init__(
        self,
        key_id: str,
        key_secret: str,
        webhook_secret: Optional[str] = None,
        callback_url: Optional[str] = None,
        app_base_url: Optional[str] = None,
    ):
        """
        Initialize Razorpay provider.

        Args:
            key_id: Razorpay API key ID
            key_secret: Razorpay API key secret
            webhook_secret: Webhook signing secret (for verification)
            callback_url: URL to redirect after payment completion
            app_base_url: Base URL of the application (used for callback if callback_url not set)
        """
        self.key_id = key_id
        self.key_secret = key_secret
        self.webhook_secret = webhook_secret
        self.callback_url = callback_url or f"{app_base_url or 'http://localhost:8000'}/api/payments/success"

        self._client = razorpay.Client(auth=(key_id, key_secret))
        self._is_test_mode = key_id.startswith("rzp_test_")

    @property
    def provider_name(self) -> str:
        return ProviderType.RAZORPAY.value

    # ─── Payment Link Creation ───────────────────────────────────────────────

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
        """
        Create a Razorpay hosted payment link.

        The user is redirected to the returned URL to complete payment.
        """
        link_data: Dict[str, Any] = {
            "amount": int(amount),  # Already in paise
            "currency": currency,
            "accept_partial": False,
            "first_min_partial_amount": 0,
            "description": description,
            "notify": {
                "email": True,
                "sms": True,
            },
            "callback_url": self.callback_url,
            "notes": {
                "user_id": user_id,
                "purpose": "credit_topup",
            },
        }

        # Add metadata
        if metadata:
            link_data["notes"].update(metadata)

        # Add customer info
        if customer_email or customer_phone:
            link_data["customer"] = {}
            if customer_email:
                link_data["customer"]["email"] = customer_email
            if customer_phone:
                link_data["customer"]["contact"] = customer_phone

        # Call Razorpay API
        link = self._client.payment_link.create(link_data)

        razorpay_link_id = link.get("id")
        short_url = link.get("short_url")
        reference_id = link.get("reference_id")

        logger.info(f"Razorpay payment link created: {razorpay_link_id} | User: {user_id} | Amount: {amount}")

        return PaymentLinkResponse(
            payment_id=reference_id or f"rzp_{user_id}_{int(datetime.utcnow().timestamp())}",
            provider=ProviderType.RAZORPAY,
            payment_url=short_url,
            amount=amount / 100 if amount > 100 else amount,  # Convert to INR if in paise
            currency=currency,
            credits_to_add=0,  # Calculated by PaymentService
            status="pending",
        )

    # ─── Webhook Signature Verification ──────────────────────────────────────

    def verify_webhook_signature(self, payload: Dict[str, Any], signature: str, secret: Optional[str] = None) -> bool:
        """
        Verify Razorpay webhook signature using HMAC-SHA256.

        Signature = HMAC-SHA256(webhook_secret, request_body_json)
        """
        webhook_secret = secret or self.webhook_secret

        if not webhook_secret:
            logger.warning("RAZORPAY_WEBHOOK_SECRET not set — skipping signature verification")
            return True

        body_bytes = json.dumps(payload, separators=(",", ":")).encode()
        expected = hmac.new(
            webhook_secret.encode(),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected, signature):
            raise ValueError("Invalid Razorpay webhook signature")

        return True

    # ─── Webhook Event Processing ────────────────────────────────────────────

    async def handle_webhook_event(self, payload: Dict[str, Any]) -> PaymentResult:
        """
        Process a Razorpay webhook event.

        Razorpay wraps each entity inside payload.{entity}.entity.{fields}.
        Supported events:
        - payment_link.paid: Payment link was fully paid
        - payment.captured: Payment was captured (direct API payments)
        - payment.authorized: Payment was authorized (not yet captured)
        - payment.failed: Payment failed
        - payment_link.expired, payment_link.cancelled: Lifecycle events
        """
        event_type = payload.get("event", "")
        entity_payload = payload.get("payload", {})

        # In test mode, log the full payload for easy debugging
        if self._is_test_mode:
            logger.info(
                f"Razorpay webhook [{event_type}] — FULL PAYLOAD:\n" f"{json.dumps(payload, indent=2, default=str)}"
            )

        logger.info(
            f"Razorpay webhook received: {event_type} | "
            f"payload_keys: {list(entity_payload.keys())} | "
            f"mode: {'TEST' if self._is_test_mode else 'LIVE'}"
        )

        if event_type == "payment_link.paid":
            return self._process_payment_link_paid(entity_payload)

        elif event_type == "payment.captured":
            return self._process_payment_captured(entity_payload)

        elif event_type == "payment.authorized":
            return self._process_payment_authorized(entity_payload)

        elif event_type == "payment.failed":
            return self._process_payment_failed(entity_payload)

        elif event_type in ("payment_link.expired", "payment_link.cancelled"):
            logger.info(f"Razorpay webhook event (lifecycle): {event_type}")
            return PaymentResult(success=True, status="ignored", error=f"Lifecycle event: {event_type}")

        else:
            logger.info(f"Razorpay webhook event ignored: {event_type}")
            return PaymentResult(success=False, status="ignored", error=f"Unknown event: {event_type}")

    def _process_payment_link_paid(self, entity_payload: Dict[str, Any]) -> PaymentResult:
        """Handle payment_link.paid webhook event.

        Razorpay payload structure:
          payload.payment_link.entity.reference_id   → our payment_id
          payload.payment_link.entity.notes           → {"user_id": "..."}
          payload.payment.entity.id                   → razorpay pay_xxx
          payload.payment.entity.amount               → amount in paise
          payload.payment.entity.method               → upi, card, etc.
        """
        # Extract from nested entity_payload.{entity}.entity.{fields}
        payment_link_entity = _safe_get(entity_payload, "payment_link.entity") or {}
        payment_entity = _safe_get(entity_payload, "payment.entity") or {}
        order_entity = _safe_get(entity_payload, "order.entity") or {}

        # Log ALL extracted fields for verification
        logger.info(
            f"Razorpay payment_link.paid — FIELD EXTRACTION:\n"
            f"  reference_id:     {payment_link_entity.get('reference_id')!r}\n"
            f"  payment_link.id:  {payment_link_entity.get('id')!r}\n"
            f"  payment.id:       {payment_entity.get('id')!r}\n"
            f"  payment.amount:   {payment_entity.get('amount')!r} (paise)\n"
            f"  payment_link.amount: {payment_link_entity.get('amount')!r} (paise)\n"
            f"  payment.method:   {payment_entity.get('method')!r}\n"
            f"  payment.status:   {payment_entity.get('status')!r}\n"
            f"  payment_link.notes: {payment_link_entity.get('notes')!r}\n"
            f"  payment.notes:    {payment_entity.get('notes')!r}\n"
            f"  order.id:         {order_entity.get('id')!r}"
        )

        # Our reference_id (set when creating payment link)
        reference_id = payment_link_entity.get("reference_id")
        payment_link_id = payment_link_entity.get("id")
        razorpay_payment_id = payment_entity.get("id")
        amount_paise = payment_entity.get("amount", payment_link_entity.get("amount", 0))
        amount_inr = amount_paise / 100 if amount_paise else 0
        payment_method = payment_entity.get("method", "unknown")
        payment_status = payment_entity.get("status", "unknown")

        # Extract user_id from notes (stored during payment link creation)
        notes = payment_link_entity.get("notes") or {}
        if isinstance(notes, list):
            notes = {}
        if not notes:
            # Fallback: check payment entity notes
            notes = payment_entity.get("notes") or {}
            if isinstance(notes, list):
                notes = {}
        user_id = notes.get("user_id")

        if not reference_id:
            logger.error(
                f"payment_link.paid missing reference_id: "
                f"plink={payment_link_id}, pay_id={razorpay_payment_id}, "
                f"full_payload_keys={list(entity_payload.keys())}, "
                f"payment_link_keys={list(_safe_get(entity_payload, 'payment_link', {}).keys())}"
            )
            return PaymentResult(success=False, status="error", error="missing_reference_id")

        if not user_id:
            logger.error(
                f"payment_link.paid missing user_id: reference_id={reference_id}, "
                f"plink={payment_link_id}, notes={notes}"
            )
            return PaymentResult(success=False, status="error", error="missing_user_id", payment_id=reference_id)

        logger.info(
            f"Razorpay payment_link.paid ✅: "
            f"reference_id={reference_id}, "
            f"plink_id={payment_link_id}, "
            f"pay_id={razorpay_payment_id}, "
            f"amount_inr={amount_inr}, "
            f"method={payment_method}, "
            f"status={payment_status}, "
            f"user_id={user_id}"
        )

        return PaymentResult(
            success=True,
            payment_id=reference_id,
            user_id=user_id,
            amount=amount_inr,
            credits_added=0,  # To be calculated by PaymentService
            status="captured",
            idempotent=False,
        )

    def _process_payment_captured(self, entity_payload: Dict[str, Any]) -> PaymentResult:
        """Handle payment.captured webhook event (fallback for direct payments).

        Payload structure for direct payment capture:
          payload.payment.entity.id
          payload.payment.entity.amount
          payload.payment.entity.method
          payload.payment.entity.notes
        """
        payment_entity = _safe_get(entity_payload, "payment.entity") or {}

        # Log ALL extracted fields for verification
        logger.info(
            f"Razorpay payment.captured — FIELD EXTRACTION:\n"
            f"  payment.id:       {payment_entity.get('id')!r}\n"
            f"  payment.amount:   {payment_entity.get('amount')!r} (paise)\n"
            f"  payment.method:   {payment_entity.get('method')!r}\n"
            f"  payment.status:   {payment_entity.get('status')!r}\n"
            f"  payment.notes:    {payment_entity.get('notes')!r}"
        )

        razorpay_payment_id = payment_entity.get("id")
        amount_paise = payment_entity.get("amount", 0)
        amount_inr = amount_paise / 100 if amount_paise else 0
        payment_method = payment_entity.get("method", "unknown")
        status = payment_entity.get("status", "unknown")

        notes = payment_entity.get("notes") or {}
        if isinstance(notes, list):
            notes = {}
        user_id = notes.get("user_id")

        logger.info(
            f"Razorpay payment.captured: "
            f"pay_id={razorpay_payment_id}, amount_inr={amount_inr}, "
            f"method={payment_method}, status={status}, user_id={user_id}"
        )

        if not razorpay_payment_id:
            return PaymentResult(success=False, status="error", error="missing_payment_id")

        if not user_id:
            return PaymentResult(success=False, status="error", error="missing_user_id", payment_id=razorpay_payment_id)

        return PaymentResult(
            success=True,
            payment_id=razorpay_payment_id,
            user_id=user_id,
            amount=amount_inr,
            credits_added=0,
            status="captured",
            idempotent=False,
        )

    def _process_payment_authorized(self, entity_payload: Dict[str, Any]) -> PaymentResult:
        """Handle payment.authorized webhook event.

        Payment authorized but not yet captured. We log it for tracking
        but don't add credits — those are added on payment_link.paid or
        payment.captured.
        """
        payment_entity = _safe_get(entity_payload, "payment.entity") or {}

        logger.info(
            f"Razorpay payment.authorized — FIELD EXTRACTION:\n"
            f"  payment.id:       {payment_entity.get('id')!r}\n"
            f"  payment.amount:   {payment_entity.get('amount')!r} (paise)\n"
            f"  payment.method:   {payment_entity.get('method')!r}\n"
            f"  payment.status:   {payment_entity.get('status')!r}\n"
            f"  payment.notes:    {payment_entity.get('notes')!r}"
        )

        razorpay_payment_id = payment_entity.get("id")
        amount_paise = payment_entity.get("amount", 0)
        amount_inr = amount_paise / 100 if amount_paise else 0
        payment_method = payment_entity.get("method", "unknown")

        notes = payment_entity.get("notes") or {}
        if isinstance(notes, list):
            notes = {}
        user_id = notes.get("user_id")

        logger.info(
            f"Razorpay payment.authorized (not yet captured): "
            f"pay_id={razorpay_payment_id}, amount_inr={amount_inr}, "
            f"method={payment_method}, user_id={user_id}"
        )

        return PaymentResult(
            success=True,
            payment_id=razorpay_payment_id,
            user_id=user_id,
            amount=amount_inr,
            credits_added=0,
            status="authorized",
            idempotent=False,
        )

    def _process_payment_failed(self, entity_payload: Dict[str, Any]) -> PaymentResult:
        """Handle payment.failed webhook event."""
        payment_entity = _safe_get(entity_payload, "payment.entity") or {}

        logger.info(
            f"Razorpay payment.failed — FIELD EXTRACTION:\n"
            f"  payment.id:              {payment_entity.get('id')!r}\n"
            f"  payment.amount:          {payment_entity.get('amount')!r} (paise)\n"
            f"  payment.error_code:      {payment_entity.get('error_code')!r}\n"
            f"  payment.error_description: {payment_entity.get('error_description')!r}\n"
            f"  payment.error_source:    {payment_entity.get('error_source')!r}\n"
            f"  payment.notes:           {payment_entity.get('notes')!r}"
        )

        razorpay_payment_id = payment_entity.get("id")
        amount_paise = payment_entity.get("amount", 0)
        amount_inr = amount_paise / 100 if amount_paise else 0
        error_code = payment_entity.get("error_code")
        error_desc = payment_entity.get("error_description")

        notes = payment_entity.get("notes") or {}
        if isinstance(notes, list):
            notes = {}
        user_id = notes.get("user_id")

        logger.warning(
            f"Razorpay payment.failed ❌: "
            f"pay_id={razorpay_payment_id}, amount_inr={amount_inr}, "
            f"error={error_code}: {error_desc}, user_id={user_id}"
        )

        return PaymentResult(
            success=False,
            payment_id=razorpay_payment_id,
            user_id=user_id,
            amount=amount_inr,
            credits_added=0,
            status="failed",
            error=f"Payment failed: {error_code} - {error_desc}",
        )
