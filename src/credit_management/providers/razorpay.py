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
from ..models.razorpay import WebhookEvent
from .base import PaymentProvider

logger = logging.getLogger(__name__)


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
        We set reference_id to our own payment_id so Razorpay echoes it
        back in the webhook (when not empty), enabling us to match
        webhook → payment record.
        """
        # Generate our own payment_id — Razorpay will echo it back as reference_id
        payment_id = f"payl_{user_id}_{int(datetime.utcnow().timestamp())}"

        link_data: Dict[str, Any] = {
            "amount": int(amount),
            "currency": currency,
            "accept_partial": False,
            "first_min_partial_amount": 0,
            "description": description,
            "reference_id": payment_id,  # Our ID — Razorpay returns this in webhooks
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

        if metadata:
            link_data["notes"].update(metadata)

        if customer_email or customer_phone:
            link_data["customer"] = {}
            if customer_email:
                link_data["customer"]["email"] = customer_email
            if customer_phone:
                link_data["customer"]["contact"] = customer_phone

        link = self._client.payment_link.create(link_data)

        razorpay_link_id = link.get("id")  # plink_xxx
        short_url = link.get("short_url")

        logger.info(
            f"Razorpay payment link created: {razorpay_link_id} | "
            f"reference_id={payment_id} | "
            f"User: {user_id} | Amount: {amount}"
        )

        return PaymentLinkResponse(
            payment_id=razorpay_link_id,  # Use Razorpay's link ID for webhook lookup
            provider=ProviderType.RAZORPAY,
            payment_url=short_url,
            amount=amount / 100 if amount > 100 else amount,
            currency=currency,
            credits_to_add=0,
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

        Parses the raw payload into a typed WebhookEvent model,
        then dispatches to the appropriate handler based on event type.
        """
        event_type = payload.get("event", "")

        # In test mode, log the full payload for easy debugging
        if self._is_test_mode:
            logger.info(
                f"Razorpay webhook [{event_type}] — FULL PAYLOAD:\n" f"{json.dumps(payload, indent=2, default=str)}"
            )

        # Parse into typed model
        try:
            webhook = WebhookEvent(**payload)
        except Exception as e:
            logger.error(f"Failed to parse webhook payload: {e}\nPayload: {json.dumps(payload, indent=2)}")
            return PaymentResult(success=False, status="error", error="invalid_webhook_payload")

        logger.info(
            f"Razorpay webhook received: {event_type} | "
            f"contains={webhook.contains} | "
            f"mode: {'TEST' if self._is_test_mode else 'LIVE'}"
        )

        if event_type == "payment_link.paid":
            return self._process_payment_link_paid(webhook)
        elif event_type == "payment.captured":
            return self._process_payment_captured(webhook)
        elif event_type == "payment.authorized":
            return self._process_payment_authorized(webhook)
        elif event_type == "payment.failed":
            return self._process_payment_failed(webhook)
        elif event_type in ("payment_link.expired", "payment_link.cancelled"):
            logger.info(f"Razorpay webhook event (lifecycle): {event_type}")
            return PaymentResult(success=True, status="ignored", error=f"Lifecycle event: {event_type}")
        else:
            logger.info(f"Razorpay webhook event ignored: {event_type}")
            return PaymentResult(success=False, status="ignored", error=f"Unknown event: {event_type}")

    def _process_payment_link_paid(self, webhook: WebhookEvent) -> PaymentResult:
        """Handle payment_link.paid webhook event.

        Key fields from the typed WebhookEvent model:
        - webhook.get_payment_link().id → "plink_xxx" (Razorpay payment link ID)
        - webhook.get_payment_link().reference_id → may be EMPTY!
        - webhook.get_payment_link().notes → {"user_id": "...", ...}
        - webhook.get_payment().id → razorpay pay_xxx
        - webhook.get_payment().amount → amount in paise
        - webhook.get_payment().method → upi, card, etc.
        - webhook.get_order().id → order_xxx

        IMPORTANT: Razorpay often sends empty reference_id in payment_link.paid.
        We use payment_link.id (plink_xxx) as the payment_id for lookup.
        """
        pl = webhook.get_payment_link()
        p = webhook.get_payment()
        o = webhook.get_order()

        if not pl:
            logger.error("payment_link.paid: missing payment_link entity in payload")
            return PaymentResult(success=False, status="error", error="missing_payment_link_entity")

        payment_link_id = pl.id
        reference_id = pl.reference_id or ""  # Often empty!
        razorpay_payment_id = p.id if p else None
        amount_paise = p.amount if p else pl.amount
        amount_inr = amount_paise / 100 if amount_paise else 0
        payment_method = p.method if p else "unknown"
        payment_status = p.status if p else "unknown"
        order_id = o.id if o else None

        # Extract user_id from notes
        user_id = webhook.get_user_id()

        # Log ALL extracted fields for verification
        logger.info(
            f"Razorpay payment_link.paid — FIELD EXTRACTION:\n"
            f"  reference_id:     {reference_id!r}\n"
            f"  payment_link.id:  {payment_link_id!r}\n"
            f"  payment.id:       {razorpay_payment_id!r}\n"
            f"  payment.amount:   {amount_paise!r} (paise)\n"
            f"  payment.method:   {payment_method!r}\n"
            f"  payment.status:   {payment_status!r}\n"
            f"  order.id:         {order_id!r}\n"
            f"  payment_link.notes: {pl.notes!r}\n"
            f"  payment.notes:    {p.notes if p else None!r}"
        )

        if not user_id:
            logger.error(
                f"payment_link.paid missing user_id: "
                f"plink_id={payment_link_id}, pay_id={razorpay_payment_id}, "
                f"notes_link={pl.notes!r}, "
                f"notes_payment={p.notes if p else None!r}"
            )
            return PaymentResult(success=False, status="error", error="missing_user_id")

        # Use payment_link.id (plink_xxx) as the payment_id for lookup.
        # Our payment records are stored with id=payment_link.id.
        # If reference_id is non-empty, use it instead (it's our original payment_id).
        payment_id = reference_id if reference_id else payment_link_id

        if not reference_id:
            logger.warning(f"payment_link.paid: reference_id empty, using plink_id as payment_id: {payment_id}")

        logger.info(
            f"Razorpay payment_link.paid ✅: "
            f"payment_id={payment_id}, "
            f"plink_id={payment_link_id}, "
            f"pay_id={razorpay_payment_id}, "
            f"order_id={order_id}, "
            f"amount_inr={amount_inr}, "
            f"method={payment_method}, "
            f"payment_status={payment_status}, "
            f"user_id={user_id}"
        )

        return PaymentResult(
            success=True,
            payment_id=payment_id,
            user_id=user_id,
            amount=amount_inr,
            credits_added=0,
            status="captured",
            idempotent=False,
        )

    def _process_payment_captured(self, webhook: WebhookEvent) -> PaymentResult:
        """Handle payment.captured webhook event.

        For payment links, credits are only added via payment_link.paid.
        For direct payments (no payment link), this event adds credits.
        We return success=True with status="captured" — PaymentService
        checks if this is a payment link and skips if so.
        """
        p = webhook.get_payment()
        if not p:
            logger.error("payment.captured: missing payment entity in payload")
            return PaymentResult(success=False, status="error", error="missing_payment_entity")

        # Log ALL extracted fields for verification
        logger.info(
            f"Razorpay payment.captured — FIELD EXTRACTION:\n"
            f"  payment.id:       {p.id!r}\n"
            f"  payment.amount:   {p.amount!r} (paise)\n"
            f"  payment.method:   {p.method!r}\n"
            f"  payment.status:   {p.status!r}\n"
            f"  payment.notes:    {p.notes!r}"
        )

        amount_inr = p.amount / 100 if p.amount else 0
        user_id = webhook.get_user_id()

        logger.info(
            f"Razorpay payment.captured: "
            f"pay_id={p.id}, amount_inr={amount_inr}, "
            f"method={p.method}, status={p.status}, user_id={user_id}"
        )

        return PaymentResult(
            success=True,
            payment_id=p.id,
            user_id=user_id,
            amount=amount_inr,
            credits_added=0,
            status="captured",
            idempotent=False,
        )

    def _process_payment_authorized(self, webhook: WebhookEvent) -> PaymentResult:
        """Handle payment.authorized webhook event.

        Payment authorized but not yet captured. We log it for tracking
        but do NOT add credits — credits are only added when payment is
        fully captured (payment_link.paid).

        Returns success=False with status="authorized" so PaymentService
        knows to skip credit addition.
        """
        p = webhook.get_payment()
        if not p:
            logger.error("payment.authorized: missing payment entity in payload")
            return PaymentResult(success=False, status="error", error="missing_payment_entity")

        logger.info(
            f"Razorpay payment.authorized — FIELD EXTRACTION:\n"
            f"  payment.id:       {p.id!r}\n"
            f"  payment.amount:   {p.amount!r} (paise)\n"
            f"  payment.method:   {p.method!r}\n"
            f"  payment.status:   {p.status!r}\n"
            f"  payment.notes:    {p.notes!r}"
        )

        amount_inr = p.amount / 100 if p.amount else 0
        user_id = webhook.get_user_id()

        logger.info(
            f"Razorpay payment.authorized (not yet captured): "
            f"pay_id={p.id}, amount_inr={amount_inr}, "
            f"method={p.method}, user_id={user_id}"
        )

        return PaymentResult(
            success=False,  # Prevents PaymentService from adding credits
            payment_id=p.id,
            user_id=user_id,
            amount=amount_inr,
            credits_added=0,
            status="authorized",
            idempotent=False,
        )

    def _process_payment_failed(self, webhook: WebhookEvent) -> PaymentResult:
        """Handle payment.failed webhook event."""
        p = webhook.get_payment()
        if not p:
            logger.error("payment.failed: missing payment entity in payload")
            return PaymentResult(success=False, status="error", error="missing_payment_entity")

        logger.info(
            f"Razorpay payment.failed — FIELD EXTRACTION:\n"
            f"  payment.id:                 {p.id!r}\n"
            f"  payment.amount:             {p.amount!r} (paise)\n"
            f"  payment.error_code:         {p.error_code!r}\n"
            f"  payment.error_description:  {p.error_description!r}\n"
            f"  payment.error_source:       {p.error_source!r}\n"
            f"  payment.notes:              {p.notes!r}"
        )

        amount_inr = p.amount / 100 if p.amount else 0
        user_id = webhook.get_user_id()

        logger.warning(
            f"Razorpay payment.failed ❌: "
            f"pay_id={p.id}, amount_inr={amount_inr}, "
            f"error={p.error_code}: {p.error_description}, user_id={user_id}"
        )

        return PaymentResult(
            success=False,
            payment_id=p.id,
            user_id=user_id,
            amount=amount_inr,
            credits_added=0,
            status="failed",
            error=f"Payment failed: {p.error_code} - {p.error_description}",
        )
