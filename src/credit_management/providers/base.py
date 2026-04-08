from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from ..models.payment import PaymentLinkResponse, PaymentResult


class PaymentProvider(ABC):
    """
    Abstract base class for payment gateway providers.

    Every payment provider (Razorpay, Stripe, PayPal, etc.) must implement
    this interface. This allows the PaymentService to work with any provider
    without provider-specific logic.

    Provider Responsibilities:
    1. Create payment links/orders via their API
    2. Verify incoming webhook signatures for authenticity
    3. Process webhook events and return standardized results
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
        """
        Create a hosted payment link with the provider.

        Args:
            user_id: Internal user identifier
            amount: Amount in smallest currency unit (paise for INR, cents for USD)
            currency: ISO 4217 currency code
            description: Payment description
            customer_email: Customer email for receipt/notifications
            customer_phone: Customer phone for SMS notifications
            metadata: Additional key-value pairs to attach to the payment

        Returns:
            PaymentLinkResponse with payment URL and metadata
        """
        ...

    # ─── Webhook Handling ────────────────────────────────────────────────────

    @abstractmethod
    def verify_webhook_signature(self, payload: Dict[str, Any], signature: str, secret: Optional[str] = None) -> bool:
        """
        Verify the webhook signature from the provider.

        Args:
            payload: The webhook request body (parsed JSON)
            signature: The signature from the webhook header
            secret: Optional webhook secret (falls back to provider default)

        Returns:
            True if signature is valid

        Raises:
            ValueError: If signature is invalid
        """
        ...

    @abstractmethod
    async def handle_webhook_event(self, payload: Dict[str, Any]) -> PaymentResult:
        """
        Process a webhook event from the provider.

        This method:
        1. Parses the webhook payload
        2. Extracts payment details (user_id, amount, status, method)
        3. Returns a standardized PaymentResult

        The caller (PaymentService) is responsible for:
        - Verifying the signature before calling this
        - Adding credits to the user account
        - Updating the payment record in the database

        Args:
            payload: The full webhook payload from the provider

        Returns:
            PaymentResult with payment details and status
        """
        ...

    # ─── Utility Methods ─────────────────────────────────────────────────────

    def convert_amount(self, amount_inr: float, credits_per_inr: float = 1.0, bonus_multiplier: float = 1.0) -> float:
        """
        Calculate credits to add based on amount.

        Providers can override this for custom pricing, but the default
        implementation uses a simple conversion rate with optional bonus.

        Args:
            amount_inr: Amount in INR
            credits_per_inr: Base conversion rate (default 1:1)
            bonus_multiplier: Bonus multiplier for promotional tiers

        Returns:
            Number of credits to add
        """
        return amount_inr * credits_per_inr * bonus_multiplier
