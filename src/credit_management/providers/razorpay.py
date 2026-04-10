"""
Razorpay Payment Provider

Implements the PaymentProvider interface for Razorpay gateway.
Supports payment links, webhook verification, and event processing.

Usage:
    provider = RazorpayProvider(
        key_id="rzp_test_xxx",
        key_secret="xxx",
        webhook_secret="whsec_xxx",  # Optional
        callback_url="https://yourapp.com/payments/success",
        audit_repo=audit_repo,  # Optional — for audit logging
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
        audit_repo: Any = None,
    ):
        """
        Initialize Razorpay provider.

        Args:
            key_id: Razorpay API key ID
            key_secret: Razorpay API key secret
            webhook_secret: Webhook signing secret (for verification)
            callback_url: URL to redirect after payment completion
            app_base_url: Base URL of the application (used for callback if callback_url not set)
            audit_repo: Optional RazorpayAuditLogRepo for audit logging
        """
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
        """Create a Razorpay hosted payment link.

        Full request/response JSON is stored in razorpay_audit_logs.
        Logs only the event type and result — no sensitive data.
        """
        payment_id = f"payl_{user_id}_{int(datetime.utcnow().timestamp())}"

        link_data: Dict[str, Any] = {
            "amount": int(amount),
            "currency": currency,
            "accept_partial": False,
            "first_min_partial_amount": 0,
            "description": description,
            "reference_id": payment_id,
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
                await self.audit_repo.log_outbound(
                    payment_link_id="unknown",
                    user_id=user_id,
                    event_type="payment_link.created",
                    request_payload=link_data,
                    response_payload={"error": str(e)},
                    http_status=500,
                )
            raise

        razorpay_link_id = link.get("id")
        short_url = link.get("short_url")

        # Audit stores full request/response JSON
        if self.audit_repo:
            try:
                await self.audit_repo.log_outbound(
                    payment_link_id=razorpay_link_id,
                    user_id=user_id,
                    event_type="payment_link.created",
                    request_payload=link_data,
                    response_payload=link,
                    http_status=http_status,
                )
            except Exception:
                pass

        logger.info("Razorpay payment_link.created")

        return PaymentLinkResponse(
            payment_id=razorpay_link_id,
            provider=ProviderType.RAZORPAY,
            payment_url=short_url,
            amount=amount / 100 if amount > 100 else amount,
            currency=currency,
            credits_to_add=0,
            status="pending",
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

        Skips verification in test mode (rzp_test_* keys) to allow local testing.
        """
        # Skip in test mode
        if self._is_test_mode:
            return True

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
        """Process a Razorpay webhook event.

        Full raw payload is stored in razorpay_audit_logs.
        Logs only event type + result — no sensitive data.
        """
        event_type = payload.get("event", "")

        # Parse into typed model
        try:
            webhook = WebhookEvent(**payload)
        except Exception as e:
            logger.error(f"Razorpay webhook parse_failed: {event_type}")
            if self.audit_repo:
                try:
                    await self.audit_repo.log_inbound(
                        payment_link_id=None,
                        user_id=None,
                        event_type=event_type,
                        raw_payload=payload,
                        http_status=400,
                        processed=False,
                        error=f"parse_failed: {e}",
                    )
                except Exception:
                    pass
            return PaymentResult(success=False, status="error", error="invalid_webhook_payload")

        # Dispatch to handler
        try:
            if event_type == "payment_link.paid":
                result = self._process_payment_link_paid(webhook)
            elif event_type == "payment.captured":
                result = self._process_payment_captured(webhook)
            elif event_type == "payment.authorized":
                result = self._process_payment_authorized(webhook)
            elif event_type == "payment.failed":
                result = self._process_payment_failed(webhook)
            elif event_type in ("payment_link.expired", "payment_link.cancelled"):
                result = PaymentResult(success=True, status="ignored", error=f"lifecycle_{event_type}")
            else:
                result = PaymentResult(success=False, status="ignored", error=f"unknown_event")
        except Exception as e:
            logger.error(f"Razorpay webhook handler_failed: {event_type}")
            result = PaymentResult(success=False, status="error", error=str(e))

        # Log to audit repo (stores full raw payload)
        if self.audit_repo:
            try:
                pl = webhook.get_payment_link()
                await self.audit_repo.log_inbound(
                    payment_link_id=pl.id if pl else None,
                    user_id=webhook.get_user_id(),
                    event_type=event_type,
                    raw_payload=payload,
                    http_status=200 if result.success else 400,
                    processed=result.success,
                    error=result.error if not result.success else None,
                )
            except Exception:
                pass

        logger.info(f"Razorpay {event_type} → " f"{'ok' if result.success else result.error or 'ignored'}")

        return result

    def _process_payment_link_paid(self, webhook: WebhookEvent) -> PaymentResult:
        """Handle payment_link.paid webhook event.

        Full payload stored in razorpay_audit_logs.
        Logs only event type + result.
        """
        pl = webhook.get_payment_link()
        p = webhook.get_payment()
        o = webhook.get_order()

        if not pl:
            return PaymentResult(success=False, status="error", error="missing_payment_link_entity")

        payment_link_id = pl.id
        reference_id = pl.reference_id or ""
        razorpay_payment_id = p.id if p else None
        amount_paise = p.amount if p else pl.amount
        amount_inr = amount_paise / 100 if amount_paise else 0
        payment_method = p.method if p else "unknown"
        order_id = o.id if o else None
        user_id = webhook.get_user_id()

        if not user_id:
            return PaymentResult(success=False, status="error", error="missing_user_id")

        payment_id = reference_id if reference_id else payment_link_id
        if not reference_id:
            logger.debug(f"Razorpay payment_link.paid: reference_id empty, using plink_id")

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
        """Handle payment.captured webhook event. Full payload in audit logs."""
        p = webhook.get_payment()
        if not p:
            return PaymentResult(success=False, status="error", error="missing_payment_entity")

        amount_inr = p.amount / 100 if p.amount else 0
        user_id = webhook.get_user_id()

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
        """Handle payment.authorized webhook event. Full payload in audit logs.

        Returns success=False so PaymentService skips credit addition.
        """
        p = webhook.get_payment()
        if not p:
            return PaymentResult(success=False, status="error", error="missing_payment_entity")

        amount_inr = p.amount / 100 if p.amount else 0
        user_id = webhook.get_user_id()

        return PaymentResult(
            success=False,
            payment_id=p.id,
            user_id=user_id,
            amount=amount_inr,
            credits_added=0,
            status="authorized",
            idempotent=False,
        )

    def _process_payment_failed(self, webhook: WebhookEvent) -> PaymentResult:
        """Handle payment.failed webhook event. Full payload in audit logs."""
        p = webhook.get_payment()
        if not p:
            return PaymentResult(success=False, status="error", error="missing_payment_entity")

        amount_inr = p.amount / 100 if p.amount else 0
        user_id = webhook.get_user_id()

        return PaymentResult(
            success=False,
            payment_id=p.id,
            user_id=user_id,
            amount=amount_inr,
            credits_added=0,
            status="failed",
            error=f"Payment failed: {p.error_code} - {p.error_description}",
        )
