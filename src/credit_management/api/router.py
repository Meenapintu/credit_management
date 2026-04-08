from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from ..cache.memory import InMemoryAsyncCache
from ..db.memory import InMemoryDBManager
from ..db.base import BaseDBManager

try:  # MongoDB is optional; only import if installed
    from ..db.mongo import MongoDBManager  # type: ignore[import]
except Exception:  # pragma: no cover - optional dependency
    MongoDBManager = None  # type: ignore[assignment]
from ..logging.ledger_logger import LedgerLogger
from ..notifications.queue import InMemoryNotificationQueue
from ..services.credit_service import CreditService
from ..services.notification_service import NotificationService
from ..services.subscription_service import SubscriptionService
from ..services.expiration_service import ExpirationService
from ..services.payment_service import PaymentService
from ..models.subscription import SubscriptionPlan
from ..models.api_models import (
    AddCreditsRequest,
    CreditBalanceResponse,
    DeductCreditsRequest,
    SubscriptionPlanRequest,
    SubscriptionPlanResponse,
)
from ..models.payment import PaymentStatus, ProviderType


# ─── Pydantic models for payment API ─────────────────────────────────────────


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


router = APIRouter(prefix="/credits", tags=["credits"])


def _create_db_manager() -> BaseDBManager:

    mongo_uri = os.getenv("CREDIT_MONGO_URI")
    mongo_db = os.getenv("CREDIT_MONGO_DB", "credit_management")
    if mongo_uri and MongoDBManager is not None:
        return MongoDBManager.from_client_uri(mongo_uri, mongo_db)  # type: ignore[call-arg]
    return InMemoryDBManager()


_db = _create_db_manager()
_cache = InMemoryAsyncCache()
_ledger = LedgerLogger(db=_db, file_path=Path("logs/credit_ledger.log"))  # type: ignore[arg-type]
_queue = InMemoryNotificationQueue()
_credit_service = CreditService(db=_db, ledger=_ledger, cache=_cache)
_subscription_service = SubscriptionService(db=_db, ledger=_ledger, cache=_cache)
_notification_service = NotificationService(
    db=_db,
    queue=_queue,
    credit_service=_credit_service,
    low_credit_threshold=10,
)
_expiration_service = ExpirationService(db=_db, ledger=_ledger, credit_service=_credit_service)

# Payment service — no providers registered yet; caller must register them
_payment_service = PaymentService(db=_db, ledger=_ledger, credit_service=_credit_service, cache=_cache)


@router.post("/add", response_model=CreditBalanceResponse)
async def add_credits(payload: AddCreditsRequest) -> CreditBalanceResponse:
    await _credit_service.add_credits(
        user_id=payload.user_id,
        amount=payload.amount,
        description=payload.description,
    )
    balance = await _credit_service.get_user_credits_info(payload.user_id)
    return CreditBalanceResponse(user_id=payload.user_id, credits=balance.available)


@router.post("/deduct", response_model=CreditBalanceResponse)
async def deduct_credits(payload: DeductCreditsRequest) -> CreditBalanceResponse:
    try:
        await _credit_service.deduct_credits(
            user_id=payload.user_id,
            amount=payload.amount,
            description=payload.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    balance = await _credit_service.get_user_credits_info(payload.user_id)
    return CreditBalanceResponse(user_id=payload.user_id, credits=balance.available)


@router.get("/balance/{user_id}", response_model=CreditBalanceResponse)
async def get_balance(user_id: str) -> CreditBalanceResponse:
    balance = await _credit_service.get_user_credits_info(user_id)
    return CreditBalanceResponse(user_id=user_id, credits=balance.available)


@router.post("/plans", response_model=SubscriptionPlanResponse)
async def create_plan(payload: SubscriptionPlanRequest) -> SubscriptionPlanResponse:
    plan = SubscriptionPlan(**payload.dict())
    plan = await _subscription_service.add_subscription_plan(plan)
    return SubscriptionPlanResponse(
        id=plan.id or "",
        name=plan.name,
        credit_limit=plan.credit_limit,
        price=plan.price,
        billing_period=plan.billing_period.value,
        validity_days=plan.validity_days,
    )


# ─── Payment Endpoints ────────────────────────────────────────────────────────

from fastapi import Header as FastAPIHeader  # type: ignore[assignment]


def _payment_record_to_response(record) -> PaymentRecordResponse:
    """Convert PaymentRecord to API response."""
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


@router.post("/payments/create", response_model=CreatePaymentResponse)
async def create_payment(
    payload: CreatePaymentRequest,
    user_id: str = FastAPIHeader(None, alias="X-User-Id"),
):
    """Create a hosted payment link."""
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
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header required")

    records, total = await _payment_service.get_payment_history(user_id, limit=limit, skip=skip)
    return PaymentHistoryResponse(
        payments=[_payment_record_to_response(r) for r in records],
        total=total,
    )


@router.get("/payments/{payment_id}")
async def get_payment(
    payment_id: str,
    user_id: str = FastAPIHeader(None, alias="X-User-Id"),
):
    """Get a specific payment record."""
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header required")

    record = await _payment_service.get_payment_by_id(payment_id, user_id=user_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    return _payment_record_to_response(record)
