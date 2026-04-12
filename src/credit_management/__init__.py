"""Credit Management — Production-ready credit management system for AI/LLM applications.

Features:
- Credit balance, reservation, and expiry management
- Automatic credit deduction via FastAPI middleware
- Payment provider architecture (Razorpay built-in, Stripe-ready)
- Atomic credit updates for race-condition safety
- Subscription plans with auto-renew
- Promo codes with usage limits and expiry
- Complete transaction ledger with dual-write (DB + file)
- Database-agnostic design (MongoDB, InMemory, extensible)
- Notification system (low credits, expiry, errors)

Quick Start:
    from fastapi import FastAPI
    from credit_management import CreditDeductionMiddleware, frontend_router, _credit_service

    app = FastAPI()
    app.include_router(frontend_router)
    app.add_middleware(CreditDeductionMiddleware, credit_service=_credit_service)


__version__ = "0.7.0"

# Core exports
from .providers.base import PaymentProvider
from .providers.razorpay import RazorpayProvider

__all__ = [
    "PaymentProvider",
    "RazorpayProvider",
    "__version__",
]
"""

__version__ = "0.7.0"
