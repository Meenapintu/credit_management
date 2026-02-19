from __future__ import annotations

import os
from pathlib import Path
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
from ..models.subscription import SubscriptionPlan

from config import settings



router = APIRouter(prefix="/credits", tags=["credits"])


class AddCreditsRequest(BaseModel):
    user_id: str
    amount: int
    description: str | None = None


class DeductCreditsRequest(BaseModel):
    user_id: str
    amount: int
    description: str | None = None


class CreditBalanceResponse(BaseModel):
    user_id: str
    credits: int


class SubscriptionPlanRequest(BaseModel):
    name: str
    description: str | None = None
    credit_limit: int
    price: float
    billing_period: str
    validity_days: int


class SubscriptionPlanResponse(BaseModel):
    id: str
    name: str
    credit_limit: int
    price: float
    billing_period: str
    validity_days: int


def _create_db_manager() -> BaseDBManager:
    
    mongo_uri = settings.MONGO_URI if settings.MONGO_URI else os.getenv("CREDIT_MONGO_URI")
    mongo_db = os.getenv("CREDIT_MONGO_DB", "credit_management")
    if mongo_uri and MongoDBManager is not None:
        return MongoDBManager.from_client_uri(mongo_uri, mongo_db)  # type: ignore[call-arg]
    return InMemoryDBManager()


_db = _create_db_manager()
_cache = InMemoryAsyncCache()
_ledger = LedgerLogger(db=_db, file_path=Path("logs/credit_ledger.log") ) # type: ignore[arg-type]
_queue = InMemoryNotificationQueue()
_credit_service = CreditService(db=_db, ledger=_ledger, cache=_cache)
_subscription_service = SubscriptionService(db=_db, ledger=_ledger, cache=_cache)
_notification_service = NotificationService(
    db=_db,
    queue=_queue,
    credit_service=_credit_service,
    low_credit_threshold=10,
)
_expiration_service = ExpirationService(
    db=_db, ledger=_ledger, credit_service=_credit_service
)


@router.post("/add", response_model=CreditBalanceResponse)
async def add_credits(payload: AddCreditsRequest) -> CreditBalanceResponse:
    await _credit_service.add_credits(
        user_id=payload.user_id,
        amount=payload.amount,
        description=payload.description,
    )
    balance = await _credit_service.get_user_credits(payload.user_id)
    return CreditBalanceResponse(user_id=payload.user_id, credits=balance)


@router.post("/deduct", response_model=CreditBalanceResponse)
async def deduct_credits(payload: DeductCreditsRequest) -> CreditBalanceResponse:
    try:
        await _credit_service.deduct_credits(
            user_id=payload.user_id,
            amount=payload.amount,
            description=payload.description,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    balance = await _credit_service.get_user_credits(payload.user_id)
    return CreditBalanceResponse(user_id=payload.user_id, credits=balance)


@router.get("/balance/{user_id}", response_model=CreditBalanceResponse)
async def get_balance(user_id: str) -> CreditBalanceResponse:
    balance = await _credit_service.get_user_credits(user_id)
    return CreditBalanceResponse(user_id=user_id, credits=balance)


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

