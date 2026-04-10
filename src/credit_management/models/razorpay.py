"""
Razorpay Pydantic Models

Properly typed models for Razorpay API request/response and webhook payloads.
Uses polymorphism for different webhook event types.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


# ─── Payment Link Creation Request ──────────────────────────────────────────


class PaymentLinkCustomerRequest(BaseModel):
    name: Optional[str] = None
    contact: Optional[str] = None
    email: Optional[str] = None


class PaymentLinkNotifyRequest(BaseModel):
    sms: bool = True
    email: bool = True
    whatsapp: bool = False


class PaymentLinkCreateRequest(BaseModel):
    amount: int  # In smallest currency unit (paise for INR)
    currency: str = "INR"
    accept_partial: bool = False
    first_min_partial_amount: int = 0
    reference_id: str  # Our internal payment ID - Razorpay echoes this back
    description: Optional[str] = None
    customer: Optional[PaymentLinkCustomerRequest] = None
    notify: Optional[PaymentLinkNotifyRequest] = None
    reminder_enable: bool = False
    notes: Dict[str, str] = Field(default_factory=dict)
    callback_url: Optional[str] = None
    callback_method: str = "get"
    expire_by: Optional[int] = None
    upi_link: bool = False
    customer_id: Optional[str] = None


# ─── Payment Link Creation Response ─────────────────────────────────────────


class PaymentLinkCustomerResponse(BaseModel):
    name: Optional[str] = None
    contact: Optional[str] = None
    email: Optional[str] = None


class PaymentLinkResponse(BaseModel):
    accepted: bool
    id: str  # "plink_xxx"
    entity: str = "payment_link"
    amount: int
    amount_paid: int = 0
    callback_method: Optional[str] = None
    callback_url: Optional[str] = None
    cancelled_at: Optional[int] = None
    created_at: int
    currency: str
    customer: Optional[PaymentLinkCustomerResponse] = None
    description: Optional[str] = None
    expire_by: Optional[int] = None
    expired_at: Optional[int] = None
    first_min_partial_amount: Optional[int] = None
    notes: Dict[str, Any] = Field(default_factory=dict)
    notify: Optional[PaymentLinkNotifyRequest] = None
    payments: List[Any] = Field(default_factory=list)
    reference_id: Optional[str] = None  # May be empty in response
    short_url: str
    status: str
    upi_link: bool
    user_id: str


# ─── Webhook Base Models ────────────────────────────────────────────────────


class WebhookPaymentEntity(BaseModel):
    id: str  # "pay_xxx"
    entity: str = "payment"
    amount: int
    currency: str = "INR"
    status: str
    order_id: Optional[str] = None
    invoice_id: Optional[str] = None
    international: bool = False
    method: str = "unknown"
    amount_refunded: int = 0
    refund_status: Optional[str] = None
    captured: bool = False
    description: Optional[str] = None
    card_id: Optional[str] = None
    bank: Optional[str] = None
    wallet: Optional[str] = None
    vpa: Optional[str] = None
    email: Optional[str] = None
    contact: Optional[str] = None
    notes: Dict[str, Any] = Field(default_factory=dict)
    fee: Optional[int] = None
    tax: Optional[int] = None
    error_code: Optional[str] = None
    error_description: Optional[str] = None
    error_source: Optional[str] = None
    error_step: Optional[str] = None
    error_reason: Optional[str] = None
    acquirer_data: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[int] = None
    upi: Optional[Dict[str, Any]] = None
    reward: Optional[Dict[str, Any]] = None
    base_amount: Optional[int] = None


class WebhookPaymentLinkEntity(BaseModel):
    id: str  # "plink_xxx"
    entity: str = "payment_link"
    amount: int
    amount_paid: int = 0
    callback_method: Optional[str] = None
    callback_url: Optional[str] = None
    cancelled_at: int = 0
    created_at: Optional[int] = None
    currency: str = "INR"
    customer: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None
    expire_by: int = 0
    expired_at: int = 0
    first_min_partial_amount: int = 0
    notes: Dict[str, Any] = Field(default_factory=dict)
    notify: Dict[str, Any] = Field(default_factory=dict)
    order_id: Optional[str] = None
    reference_id: Optional[str] = ""  # Often empty!
    reminder_enable: bool = True
    reminders: Dict[str, Any] = Field(default_factory=dict)
    short_url: str = ""
    status: str
    updated_at: Optional[int] = None
    upi_link: bool = False
    user_id: str = ""
    whatsapp_link: bool = False
    accept_partial: bool = False


class WebhookOrderEntity(BaseModel):
    id: str  # "order_xxx"
    entity: str = "order"
    amount: int
    amount_due: int = 0
    amount_paid: int = 0
    attempts: int = 0
    checkout: Optional[Any] = None
    created_at: Optional[int] = None
    currency: str = "INR"
    description: Optional[str] = None
    notes: Dict[str, Any] = Field(default_factory=dict)
    offer_id: Optional[str] = None
    receipt: Optional[str] = None
    status: str = "created"


class WebhookPayload(BaseModel):
    payment: Optional[Dict[str, WebhookPaymentEntity]] = None
    payment_link: Optional[Dict[str, WebhookPaymentLinkEntity]] = None
    order: Optional[Dict[str, WebhookOrderEntity]] = None


class WebhookEvent(BaseModel):
    """Base webhook event from Razorpay."""

    entity: str = "event"
    account_id: str
    event: str  # "payment.authorized", "payment.captured", "payment_link.paid", etc.
    contains: List[str] = Field(default_factory=list)
    payload: WebhookPayload
    created_at: int

    def get_payment(self) -> Optional[WebhookPaymentEntity]:
        if self.payload.payment and "entity" in self.payload.payment:
            return self.payload.payment["entity"]
        return None

    def get_payment_link(self) -> Optional[WebhookPaymentLinkEntity]:
        if self.payload.payment_link and "entity" in self.payload.payment_link:
            return self.payload.payment_link["entity"]
        return None

    def get_order(self) -> Optional[WebhookOrderEntity]:
        if self.payload.order and "entity" in self.payload.order:
            return self.payload.order["entity"]
        return None

    def get_user_id(self) -> Optional[str]:
        """Extract user_id from notes (stored during payment link creation)."""
        # Try payment_link notes first
        pl = self.get_payment_link()
        if pl and pl.notes.get("user_id"):
            return pl.notes["user_id"]
        # Try payment notes
        p = self.get_payment()
        if p and p.notes.get("user_id"):
            return p.notes["user_id"]
        # Try order notes
        o = self.get_order()
        if o and o.notes.get("user_id"):
            return o.notes["user_id"]
        return None
