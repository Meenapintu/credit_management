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

    def verify_webhook_signature(
        self, payload: Dict[str, Any], signature: str, secret: Optional[str] = None
    ) -> bool:
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

        Supported events:
        - payment_link.paid: Payment link was fully paid
        - payment.captured: Payment was captured (direct API payments)
        - payment.failed: Payment failed
        """
        event_type = payload.get("event", "")
        entity_payload = payload.get("payload", {})

        logger.info(f"Razorpay webhook received: {event_type}")

        if event_type == "payment_link.paid":
            return self._process_payment_link_paid(entity_payload)

        elif event_type == "payment.captured":
            return self._process_payment_captured(entity_payload)

        elif event_type == "payment.failed":
            return self._process_payment_failed(entity_payload)

        else:
            logger.info(f"Razorpay webhook event ignored: {event_type}")
            return PaymentResult(success=False, status="ignored", error=f"Unknown event: {event_type}")

    def _process_payment_link_paid(self, entity_payload: Dict[str, Any]) -> PaymentResult:
        """Handle payment_link.paid webhook event."""
        payment_link_data = entity_payload.get("payment_link", {})
        payment_data = entity_payload.get("payment", {})

        reference_id = payment_link_data.get("reference_id")
        if not reference_id:
            return PaymentResult(success=False, status="error", error="missing_reference_id")

        razorpay_payment_id = payment_data.get("id")
        amount_paise = payment_data.get("amount", payment_link_data.get("amount", 0))
        amount_inr = amount_paise / 100
        payment_method = payment_data.get("method", "unknown")

        # Extract user_id from notes (stored during payment link creation)
        notes = payment_link_data.get("notes", {})
        user_id = notes.get("user_id")

        if not user_id:
            return PaymentResult(success=False, status="error", error="missing_user_id", payment_id=reference_id)

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
        """Handle payment.captured webhook event (fallback for direct payments)."""
        payment_data = entity_payload.get("payment", {})
        razorpay_payment_id = payment_data.get("id")

        if not razorpay_payment_id:
            return PaymentResult(success=False, status="error", error="missing_payment_id")

        amount_paise = payment_data.get("amount", 0)
        amount_inr = amount_paise / 100
        payment_method = payment_data.get("method", "unknown")

        notes = payment_data.get("notes", {})
        user_id = notes.get("user_id")

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

    def _process_payment_failed(self, entity_payload: Dict[str, Any]) -> PaymentResult:
        """Handle payment.failed webhook event."""
        payment_data = entity_payload.get("payment", {})
        razorpay_payment_id = payment_data.get("id")

        return PaymentResult(
            success=False,
            payment_id=razorpay_payment_id,
            user_id=None,
            amount=0,
            credits_added=0,
            status="failed",
            error=f"Payment failed: {razorpay_payment_id}",
        )
