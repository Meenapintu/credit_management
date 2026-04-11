from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.payment import PaymentLinkResponse, PaymentRecord


class PaymentProvider(ABC):
    """
    Abstract base class for payment gateway providers.

    Every payment provider (Razorpay, Stripe, PayPal, etc.) must implement
    this interface. This allows the PaymentService to work with any provider
    without provider-specific logic.

    Provider Responsibilities:
    1. Create payment links/orders via their API
    2. Verify incoming webhook signatures for authenticity
    3. Parse webhook events and return a PaymentRecord with all extractable fields
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier (e.g., 'razorpay', 'stripe')."""
        ...

    # ─── Payment Link Creation ───────────────────────────────────────────────

    @abstractmethod
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
        """Create a hosted payment link with the provider."""
        ...

    # ─── Webhook Handling ────────────────────────────────────────────────────

    @abstractmethod
    def verify_webhook_signature(self, payload: Dict[str, Any], signature: str, secret: Optional[str] = None) -> bool:
        """Verify the webhook signature from the provider."""
        ...

    @abstractmethod
    async def handle_webhook_event(self, payload: Dict[str, Any]) -> Optional[PaymentRecord]:
        """
        Parse webhook payload and return a PaymentRecord with all extractable fields.

        The provider parses its own webhook format and extracts:
        - provider_payment_link_id, provider_payment_id, provider_order_id
        - user_id, amount, status, payment_method

        Returns None if the webhook event has no payment-related data.
        """
        ...
