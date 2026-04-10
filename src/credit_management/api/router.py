"""
Credit Management — Core Setup

Creates shared service instances (DB, cache, ledger, credit, payment).
Endpoints have been split into:
  - frontend_router.py   (GET /credits/balance, payments/*)
  - backend_router.py    (POST /admin/credits/add, /deduct, /plans)
  - webhook_router.py    (POST /webhooks/{provider})

This file exports singletons for use across all routers.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter

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
from ..services.promo_service import PromoService
from ..db.razorpay_audit import RazorpayAuditLogRepo
from ..providers.razorpay import RazorpayProvider


router = APIRouter(prefix="/credits", tags=["credits"])


def _create_db_manager() -> BaseDBManager:
    mongo_uri = os.getenv("CREDIT_MONGO_URI")
    mongo_db = os.getenv("CREDIT_MONGO_DB", "credit_management")
    if mongo_uri and MongoDBManager is not None:
        return MongoDBManager.from_client_uri(mongo_uri, mongo_db)
    return InMemoryDBManager()


def create_credit_service() -> tuple[CreditService, BaseDBManager, LedgerLogger, InMemoryAsyncCache]:
    _db = _create_db_manager()
    _cache = InMemoryAsyncCache()
    _ledger = LedgerLogger(db=_db, file_path=Path("logs/credit_ledger.log"))
    _credit_service = CreditService(db=_db, ledger=_ledger, cache=_cache)
    return _credit_service, _db, _ledger, _cache


_queue = InMemoryNotificationQueue()
_credit_service, _db, _ledger, _cache = create_credit_service()
_subscription_service = SubscriptionService(db=_db, ledger=_ledger, cache=_cache)
_notification_service = NotificationService(
    db=_db,
    queue=_queue,
    credit_service=_credit_service,
    low_credit_threshold=10,
)
_expiration_service = ExpirationService(db=_db, ledger=_ledger, credit_service=_credit_service)

# Payment service — caller must register providers via _payment_service.register_provider()
_payment_service = PaymentService(db=_db, ledger=_ledger, credit_service=_credit_service, cache=_cache)

# Razorpay audit log repository (append-only, raw JSON storage)
_razorpay_audit_repo: Optional[RazorpayAuditLogRepo] = None
if hasattr(_db, "_db"):
    # MongoDB — has access to the raw AsyncIOMotorDatabase
    _razorpay_audit_repo = RazorpayAuditLogRepo(_db._db)


def get_razorpay_audit_repo() -> Optional[RazorpayAuditLogRepo]:
    """Get the Razorpay audit log repository (may be None if not MongoDB)."""
    return _razorpay_audit_repo


def setup_razorpay_provider() -> None:
    """Initialize Razorpay provider with audit logging and register it."""
    key_id = os.getenv("RAZERPAY_TEST_KEY") or os.getenv("RAZORPAY_KEY_ID")
    key_secret = os.getenv("RAZERPAY_TEST_SECRET") or os.getenv("RAZORPAY_KEY_SECRET")

    if not key_id or not key_secret:
        return

    provider = RazorpayProvider(
        key_id=key_id,
        key_secret=key_secret,
        webhook_secret=os.getenv("RAZORPAY_WEBHOOK_SECRET"),
        app_base_url=os.getenv("APP_BASE_URL", "http://localhost:9000"),
        audit_repo=_razorpay_audit_repo,
    )
    _payment_service.register_provider("razorpay", provider)


# Promo service
_promo_service = PromoService(db=_db, ledger=_ledger, credit_service=_credit_service)

# Initialize Razorpay provider with audit logging
setup_razorpay_provider()
