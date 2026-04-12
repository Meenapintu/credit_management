"""
Frontend Router — Endpoints the frontend app calls directly.

Prefix: /credits
Called by: Frontend (browser/mobile)
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, status
from fastapi import Header as FastAPIHeader
from pydantic import BaseModel

from ..models.payment import PaymentStatus
from ..models.promo import ClaimPromoRequest, ClaimPromoResponse, PromoEligibilityResponse

router = APIRouter(prefix="/credits", tags=["credits-frontend"])


# ─── Pydantic Models ─────────────────────────────────────────────────────────


class CreditBalanceResponse(BaseModel):
    user_id: str
    credits: float


class CreatePaymentRequest(BaseModel):
    amount_inr: float
    description: str = "Credit top-up"
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    provider: str = "razorpay"


class CreatePaymentResponse(BaseModel):
    payment_id: str
    provider: str
    payment_url: str
    amount_inr: float
    credits_to_add: float
    status: str


class PaymentRecordResponse(BaseModel):
    id: str
    user_id: str
    provider: str
    provider_payment_id: Optional[str]
    provider_payment_link_id: Optional[str]
    amount: float
    currency: str
    amount_inr: float
    credits_to_add: float
    credits_added: float
    status: str
    payment_method: Optional[str]
    description: Optional[str]
    created_at: str
    completed_at: Optional[str]
    error_message: Optional[str]


class PaymentHistoryResponse(BaseModel):
    payments: List[PaymentRecordResponse] = []
    total: int


# ─── Endpoint Helpers ────────────────────────────────────────────────────────


def _payment_record_to_response(record) -> PaymentRecordResponse:
    return PaymentRecordResponse(
        id=record.id or "",
        user_id=record.user_id,
        provider=record.provider.value if hasattr(record.provider, "value") else str(record.provider),
        provider_payment_id=record.provider_payment_id,
        provider_payment_link_id=record.provider_payment_link_id,
        amount=record.amount,
        currency=record.currency,
        amount_inr=record.amount_inr,
        credits_to_add=record.credits_to_add,
        credits_added=record.credits_added,
        status=record.status.value if hasattr(record.status, "value") else str(record.status),
        payment_method=record.payment_method,
        description=record.description,
        created_at=record.created_at.isoformat(),
        completed_at=record.completed_at.isoformat() if record.completed_at else None,
        error_message=record.error_message,
    )


# ─── Balance Endpoint ────────────────────────────────────────────────────────


@router.get("/balance/{user_id}", response_model=CreditBalanceResponse)
async def get_balance(user_id: str):
    """Get user's current credit balance."""
    from .router import _credit_service

    balance = await _credit_service.get_user_credits_info(user_id)
    return CreditBalanceResponse(user_id=user_id, credits=balance.available)


# ─── Payment Endpoints ───────────────────────────────────────────────────────


@router.post("/payments/create", response_model=CreatePaymentResponse)
async def create_payment(
    payload: CreatePaymentRequest,
    user_id: str = FastAPIHeader(None, alias="X-User-Id"),
):
    """Create a hosted payment link. Frontend redirects user to payment_url."""
    from .router import _payment_service

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header required")

    try:
        link = await _payment_service.create_payment_link(
            user_id=user_id,
            amount_inr=payload.amount_inr,
            provider_name=payload.provider,
            description=payload.description,
            customer_email=payload.customer_email,
            customer_phone=payload.customer_phone,
        )
        return CreatePaymentResponse(
            payment_id=link.payment_id,
            provider=link.provider.value if hasattr(link.provider, "value") else str(link.provider),
            payment_url=link.payment_url,
            amount_inr=link.amount,
            credits_to_add=link.credits_to_add,
            status=link.status,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Payment creation failed: {str(e)}",
        ) from e


@router.get("/payments/history", response_model=PaymentHistoryResponse)
async def payment_history(
    user_id: str = FastAPIHeader(None, alias="X-User-Id"),
    limit: int = 20,
    skip: int = 0,
):
    """Get payment history for the authenticated user."""
    from .router import _payment_service

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header required")

    records, total = await _payment_service.get_payment_history(user_id, limit=limit, skip=skip)
    return PaymentHistoryResponse(
        payments=[_payment_record_to_response(r) for r in records],
        total=total,
    )


@router.get("/payments/{payment_id}", response_model=PaymentRecordResponse)
async def get_payment(
    payment_id: str,
    user_id: str = FastAPIHeader(None, alias="X-User-Id"),
):
    """Get a specific payment record. Frontend polls this for status."""
    from .router import _payment_service

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header required")

    record = await _payment_service.get_payment_by_id(payment_id, user_id=user_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    return _payment_record_to_response(record)


# ─── Promo Endpoints ────────────────────────────────────────────────────────


@router.get("/promo/eligibility", response_model=PromoEligibilityResponse)
async def check_promo_eligibility(
    promo_code: str,
    user_id: str = FastAPIHeader(None, alias="X-User-Id"),
):
    """Check if user is eligible for a promo code."""
    from .router import _promo_service

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header required")

    return await _promo_service.check_eligibility(user_id, promo_code)


@router.post("/promo/claim", response_model=ClaimPromoResponse)
async def claim_promo(
    payload: ClaimPromoRequest,
    user_id: str = FastAPIHeader(None, alias="X-User-Id"),
):
    """Claim a promo code. Adds credits if eligible."""
    from .router import _promo_service

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header required")

    return await _promo_service.claim_promo(user_id, payload.promo_code)
