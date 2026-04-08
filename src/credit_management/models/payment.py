from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Dict, Optional

from pydantic import Field

from .base import DBSerializableModel


class ProviderType(str, Enum):
    RAZORPAY = "razorpay"
    STRIPE = "stripe"
    # Future providers
    # PAYPAL = "paypal"
    # CASHFREE = "cashfree"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class PaymentRecord(DBSerializableModel):
    """
    Unified payment record stored in the database.

    Provider-agnostic: stores the provider type and raw provider response
    in metadata so the same table/collection works for all payment gateways.
    """

    collection_name: ClassVar[str] = "payment_records"

    id: Optional[str] = Field(default=None)
    user_id: str
    provider: ProviderType
    provider_payment_id: Optional[str] = None  # e.g. Razorpay pay_xxx, Stripe pi_xxx
    provider_payment_link_id: Optional[str] = None  # e.g. Razorpay plink_xxx
    provider_order_id: Optional[str] = None

    amount: float  # In smallest currency unit (paise for INR, cents for USD)
    currency: str = "INR"
    amount_inr: float = Field(0, description="Human-readable amount in INR")

    credits_to_add: float = 0  # Calculated credits based on conversion rate
    credits_added: float = 0  # Actual credits added (after successful payment)

    status: PaymentStatus = PaymentStatus.PENDING
    payment_method: Optional[str] = None  # upi, card, netbanking, etc.

    description: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None

    metadata: Dict[str, Any] = Field(default_factory=dict)  # Raw provider response, notes, etc.
    error_message: Optional[str] = None


class PaymentLinkResponse(DBSerializableModel):
    """Response returned when a payment link is created."""

    payment_id: str
    provider: ProviderType
    payment_url: str
    amount: float
    currency: str = "INR"
    credits_to_add: float
    status: str = "pending"


class PaymentResult(DBSerializableModel):
    """Result of processing a payment webhook."""

    success: bool
    payment_id: Optional[str] = None
    user_id: Optional[str] = None
    amount: float = 0
    credits_added: float = 0
    status: str = ""
    error: Optional[str] = None
    idempotent: bool = False
