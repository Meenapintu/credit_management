"""
Backend Router — Endpoints called by backend services, not frontend.

Prefix: /admin/credits
Called by: Webhooks, middleware, scheduled jobs, admin panels
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from ..models.api_models import (
    AddCreditsRequest,
    CreditBalanceResponse,
    DeductCreditsRequest,
    SubscriptionPlanRequest,
    SubscriptionPlanResponse,
)
from ..models.promo import CreatePromoRequest, PromoResponse
from ..models.subscription import SubscriptionPlan

router = APIRouter(prefix="/admin/credits", tags=["credits-backend"])


@router.post("/add", response_model=CreditBalanceResponse)
async def add_credits(payload: AddCreditsRequest) -> CreditBalanceResponse:
    """
    Add credits to a user.

    Called by: Payment webhook (after verified payment), admin panel, scheduled jobs.
    NOT called by frontend directly.
    """
    from .router import _credit_service

    await _credit_service.add_credits(
        user_id=payload.user_id,
        amount=payload.amount,
        description=payload.description,
    )
    balance = await _credit_service.get_user_credits_info(payload.user_id)
    return CreditBalanceResponse(user_id=payload.user_id, credits=balance.available)


@router.post("/deduct", response_model=CreditBalanceResponse)
async def deduct_credits(payload: DeductCreditsRequest) -> CreditBalanceResponse:
    """
    Deduct credits from a user.

    Called by: CreditDeductionMiddleware (after API usage), backend services.
    NOT called by frontend directly.
    """
    from .router import _credit_service

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


@router.post("/plans", response_model=SubscriptionPlanResponse)
async def create_plan(payload: SubscriptionPlanRequest) -> SubscriptionPlanResponse:
    """
    Create a subscription plan.

    Called by: Admin panel, setup scripts.
    NOT called by frontend directly.
    """
    from .router import _subscription_service

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


# ─── Promo Management (Admin) ───────────────────────────────────────────────


@router.post("/promos", response_model=PromoResponse)
async def create_promo(payload: CreatePromoRequest):
    """Create a new promo code."""
    from .router import _promo_service

    try:
        promo = await _promo_service.create_promo(payload)
        total_claims = await _promo_service._db.count_promo_claims(promo.id or "")
        return PromoResponse(
            id=promo.id or "",
            code=promo.code,
            credits=promo.credits,
            description=promo.description,
            target_type=promo.target_type.value,
            target_user_ids=promo.target_user_ids,
            max_uses=promo.max_uses,
            max_uses_per_user=promo.max_uses_per_user,
            valid_from=promo.valid_from.isoformat(),
            valid_until=promo.valid_until.isoformat() if promo.valid_until else None,
            is_active=promo.is_active,
            total_claims=total_claims,
            created_at=promo.created_at.isoformat(),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.get("/promos", response_model=list[PromoResponse])
async def list_promos(active_only: bool = True):
    """List all promos."""
    from .router import _promo_service

    return await _promo_service.list_promos(active_only=active_only)


@router.post("/promos/{promo_id}/toggle")
async def toggle_promo(promo_id: str):
    """Toggle a promo's active status."""
    from .router import _promo_service

    try:
        promo = await _promo_service.toggle_promo(promo_id)
        return {"id": promo.id, "code": promo.code, "is_active": promo.is_active}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
