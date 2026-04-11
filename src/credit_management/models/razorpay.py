"""Razorpay Webhook Models — Pydantic models for Razorpay webhook payloads."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WebhookPaymentEntity(BaseModel):
    id: str
    entity: str = "payment"
    amount: int
    currency: str = "INR"
    status: str
    order_id: Optional[str] = None
    method: str = "unknown"
    amount_refunded: int = 0
    captured: bool = False
    notes: Dict[str, Any] = Field(default_factory=dict)
    error_code: Optional[str] = None
    error_description: Optional[str] = None


class WebhookPaymentLinkEntity(BaseModel):
    id: str
    entity: str = "payment_link"
    amount: int
    amount_paid: int = 0
    reference_id: Optional[str] = ""
    status: str
    notes: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None


class WebhookOrderEntity(BaseModel):
    id: str
    entity: str = "order"
    amount: int
    status: str = "created"
    notes: Dict[str, Any] = Field(default_factory=dict)


class WebhookRefundEntity(BaseModel):
    id: str
    entity: str = "refund"
    amount: int
    currency: str = "INR"
    payment_id: str
    notes: Dict[str, Any] = Field(default_factory=dict)
    status: str = "processed"


class WebhookPayload(BaseModel):
    payment: Optional[Dict[str, WebhookPaymentEntity]] = None
    payment_link: Optional[Dict[str, WebhookPaymentLinkEntity]] = None
    order: Optional[Dict[str, WebhookOrderEntity]] = None
    refund: Optional[Dict[str, WebhookRefundEntity]] = None


class WebhookEvent(BaseModel):
    """Base webhook event from Razorpay."""

    entity: str = "event"
    account_id: str
    event: str
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

    def get_refund(self) -> Optional[WebhookRefundEntity]:
        if self.payload.refund and "entity" in self.payload.refund:
            return self.payload.refund["entity"]
        return None

    def get_user_id(self) -> Optional[str]:
        """Extract user_id from notes."""
        pl = self.get_payment_link()
        if pl and pl.notes.get("user_id"):
            return pl.notes["user_id"]
        p = self.get_payment()
        if p and p.notes.get("user_id"):
            return p.notes["user_id"]
        o = self.get_order()
        if o and o.notes.get("user_id"):
            return o.notes["user_id"]
        return None
