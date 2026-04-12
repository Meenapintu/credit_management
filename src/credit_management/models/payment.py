"""Payment Models — Razorpay integration."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Dict, Optional

from pydantic import Field

from .base import DBSerializableModel


class ProviderType(str, Enum):
    RAZORPAY = "razorpay"
    STRIPE = "stripe"


class PaymentStatus(str, Enum):
    """Payment states mapped 1:1 from Razorpay's native event names."""

    PENDING = "pending"
    AUTHORIZED = "authorized"  # payment.authorized
    CAPTURED = "captured"  # payment.captured
    PAID = "paid"  # payment_link.paid
    PARTIALLY_PAID = "partially_paid"  # payment_link.partially_paid
    EXPIRED = "expired"  # payment_link.expired
    CANCELLED = "cancelled"  # payment_link.cancelled
    FAILED = "failed"  # payment.failed
    REFUND_CREATED = "refund_created"  # refund.created
    REFUND_PROCESSED = "refund_processed"  # refund.processed
    REFUND_FAILED = "refund_failed"  # refund.failed
    DISPUTE_CLOSED = "dispute_closed"  # payment.dispute.closed
    ORDER_PAID = "order_paid"  # order.paid
    INVOICE_PAID = "invoice_paid"  # invoice.paid
    INVOICE_PARTIALLY_PAID = "invoice_partially_paid"  # invoice.partially_paid
    INVOICE_EXPIRED = "invoice_expired"  # invoice.expired


class PaymentRecord(DBSerializableModel):
    """Unified payment record stored in the database."""

    collection_name: ClassVar[str] = "payment_records"

    id: Optional[str] = Field(default=None)
    user_id: str
    provider: ProviderType
    provider_payment_id: Optional[str] = None
    provider_payment_link_id: Optional[str] = None
    provider_order_id: Optional[str] = None

    amount: float  # In smallest currency unit (paise for INR)
    currency: str = "INR"
    amount_inr: float = Field(0, description="Human-readable amount in INR")

    credits_to_add: float = 0
    credits_added: float = 0

    status: PaymentStatus = PaymentStatus.PENDING
    payment_method: Optional[str] = None

    description: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None

    metadata: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None


class PaymentLinkResponse(DBSerializableModel):
    """Response returned when a payment link is created."""

    payment_id: str
    provider_payment_link_id: str
    provider: ProviderType
    payment_url: str
    amount: float
    currency: str = "INR"
    credits_to_add: float
    status: str = "pending"


class PaymentResult(DBSerializableModel):
    """Result of processing a payment webhook event."""

    success: bool
    payment_id: Optional[str] = None
    user_id: Optional[str] = None
    amount: float = 0
    credits_added: float = 0
    status: str = ""
    idempotent: bool = False
    error: Optional[str] = None
