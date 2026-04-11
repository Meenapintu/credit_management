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

from ..models.payment import PaymentLinkResponse, PaymentResult, ProviderType
from ..models.razorpay import WebhookEvent
from .base import PaymentProvider

logger = logging.getLogger(__name__)


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
        payment_id = f"payl_{user_id}_{int(datetime.utcnow().timestamp())}"
        link_data = {
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

    def verify_webhook_signature(self, payload: Dict[str, Any], signature: str, secret: Optional[str] = None) -> bool:
        if self._is_test_mode:
            return True
        webhook_secret = secret or self.webhook_secret
        if not webhook_secret:
            logger.warning("RAZORPAY_WEBHOOK_SECRET not set — skipping signature verification")
            return True
        body_bytes = json.dumps(payload, separators=(",", ":")).encode()
        expected = hmac.new(webhook_secret.encode(), body_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("Invalid Razorpay webhook signature")
        return True

    async def handle_webhook_event(self, payload: Dict[str, Any]) -> PaymentResult:
        event_type = payload.get("event", "")
        if self._is_test_mode:
            logger.info(
                f"Razorpay webhook [{event_type}] — FULL PAYLOAD:\n{json.dumps(payload, indent=2, default=str)}"
            )

        try:
            webhook = WebhookEvent(**payload)
        except Exception as e:
            logger.error(f"Failed to parse webhook payload: {e}")
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

        try:
            if event_type == "payment_link.paid":
                result = self._process_payment_link_paid(webhook)
            elif event_type == "payment.captured":
                result = self._process_payment_captured(webhook)
            elif event_type == "payment.authorized":
                result = self._process_payment_authorized(webhook)
            elif event_type == "payment.failed":
                result = self._process_payment_failed(webhook)
            elif event_type == "payment_link.partially_paid":
                result = self._process_payment_link_partially_paid(webhook)
            elif event_type in ("payment_link.expired", "payment_link.cancelled"):
                result = self._process_payment_link_lifecycle(webhook, event_type)
            elif event_type in ("refund.created", "refund.processed", "refund.failed"):
                result = self._process_refund_event(webhook, event_type)
            else:
                result = PaymentResult(success=False, status="ignored", error=f"unknown_event")
        except Exception as e:
            logger.error(f"Razorpay webhook handler_failed: {event_type}")
            result = PaymentResult(success=False, status="error", error=str(e))

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

        logger.info(f"Razorpay {event_type} → {'ok' if result.success else result.error or 'ignored'}")
        return result

    def _process_payment_link_paid(self, webhook: WebhookEvent) -> PaymentResult:
        pl = webhook.get_payment_link()
        if not pl:
            return PaymentResult(success=False, status="error", error="missing_payment_link_entity")
        p = webhook.get_payment()
        payment_id = pl.reference_id if pl.reference_id else pl.id
        amount_inr = (p.amount if p else pl.amount) / 100
        return PaymentResult(
            success=True,
            payment_id=payment_id,
            user_id=webhook.get_user_id(),
            amount=amount_inr,
            credits_added=0,
            status="paid",
        )

    def _process_payment_captured(self, webhook: WebhookEvent) -> PaymentResult:
        p = webhook.get_payment()
        if not p:
            return PaymentResult(success=False, status="error", error="missing_payment_entity")
        return PaymentResult(
            success=True,
            payment_id=p.id,
            user_id=webhook.get_user_id(),
            amount=p.amount / 100 if p.amount else 0,
            credits_added=0,
            status="captured",
        )

    def _process_payment_authorized(self, webhook: WebhookEvent) -> PaymentResult:
        p = webhook.get_payment()
        if not p:
            return PaymentResult(success=False, status="error", error="missing_payment_entity")
        return PaymentResult(
            success=True,
            payment_id=p.id,
            user_id=webhook.get_user_id(),
            amount=p.amount / 100 if p.amount else 0,
            credits_added=0,
            status="authorized",
        )

    def _process_payment_failed(self, webhook: WebhookEvent) -> PaymentResult:
        p = webhook.get_payment()
        if not p:
            return PaymentResult(success=False, status="error", error="missing_payment_entity")
        amount_inr = p.amount / 100 if p.amount else 0
        return PaymentResult(
            success=False,
            payment_id=p.id,
            user_id=webhook.get_user_id(),
            amount=amount_inr,
            credits_added=0,
            status="failed",
            error=f"Payment failed: {p.error_code} - {p.error_description}",
        )

    def _process_payment_link_partially_paid(self, webhook: WebhookEvent) -> PaymentResult:
        p = webhook.get_payment()
        if not p:
            return PaymentResult(success=False, status="error", error="missing_payment_entity")
        return PaymentResult(
            success=True,
            payment_id=p.id,
            user_id=webhook.get_user_id(),
            amount=p.amount / 100 if p.amount else 0,
            credits_added=0,
            status="partially_paid",
        )

    def _process_payment_link_lifecycle(self, webhook: WebhookEvent, event_type: str) -> PaymentResult:
        pl = webhook.get_payment_link()
        status = "expired" if "expired" in event_type else "cancelled"
        return PaymentResult(
            success=True,
            payment_id=pl.id if pl else None,
            user_id=webhook.get_user_id(),
            amount=0,
            credits_added=0,
            status=status,
        )

    def _process_refund_event(self, webhook: WebhookEvent, event_type: str) -> PaymentResult:
        r = webhook.get_refund()
        if not r:
            return PaymentResult(success=True, status=event_type.split(".")[-1])
        return PaymentResult(
            success=True,
            payment_id=r.id,
            user_id=webhook.get_user_id(),
            amount=r.amount / 100 if r.amount else 0,
            credits_added=0,
            status=event_type.split(".")[-1],
        )
