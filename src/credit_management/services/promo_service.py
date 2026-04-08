"""
Promo Service — Manages promo codes, eligibility checks, and claims.

Handles:
- Promo creation and management (backend)
- Eligibility checking (frontend)
- Promo claiming (frontend) — atomically adds credits and records claim
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from ..db.base import BaseDBManager
from ..logging.ledger_logger import LedgerLogger
from ..models.promo import (
    ClaimPromoResponse,
    CreatePromoRequest,
    PromoEligibilityResponse,
    PromoRecord,
    PromoTargetType,
    PromoResponse,
    UserPromoClaim,
)
from .credit_service import CreditService

logger = logging.getLogger(__name__)


class PromoService:
    """
    Service for managing promo codes.

    Usage:
        promo_service = PromoService(db, ledger, credit_service)

        # Backend: create promo
        promo = await promo_service.create_promo(CreatePromoRequest(...))

        # Frontend: check eligibility
        result = await promo_service.check_eligibility(user_id, "WELCOME50")

        # Frontend: claim promo
        result = await promo_service.claim_promo(user_id, "WELCOME50")
    """

    def __init__(self, db: BaseDBManager, ledger: LedgerLogger, credit_service: CreditService):
        self._db = db
        self._ledger = ledger
        self._credit_service = credit_service

    # ─── Backend: Promo Management ──────────────────────────────────────────

    async def create_promo(self, request: CreatePromoRequest) -> PromoRecord:
        """Create a new promo code (admin operation)."""
        # Validate: code must be unique
        existing = await self._db.get_promo_by_code(request.code)
        if existing:
            raise ValueError(f"Promo code '{request.code}' already exists")

        # Validate: specific_users must be set if target_type is SPECIFIC_USERS
        if request.target_type == PromoTargetType.SPECIFIC_USERS and not request.target_user_ids:
            raise ValueError("target_user_ids must be provided when target_type is 'specific_users'")

        promo = PromoRecord(
            code=request.code.upper(),
            credits=request.credits,
            description=request.description,
            target_type=request.target_type,
            target_user_ids=request.target_user_ids,
            max_uses=request.max_uses,
            max_uses_per_user=request.max_uses_per_user,
            valid_until=request.valid_until,
            is_active=request.is_active,
        )

        promo = await self._db.add_promo(promo)

        await self._ledger.log_transaction(
            user_id="system",
            message="Promo created",
            details={"promo_code": promo.code, "credits": promo.credits, "target_type": promo.target_type.value},
        )

        logger.info(f"Promo created: {promo.code} → {promo.credits} credits")
        return promo

    async def list_promos(self, active_only: bool = True) -> list[PromoResponse]:
        """List all promos (optionally active only)."""
        promos = await self._db.list_promos(active_only=active_only)
        responses = []
        for promo in promos:
            total_claims = await self._db.count_promo_claims(promo.id or "")
            responses.append(
                PromoResponse(
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
            )
        return responses

    async def toggle_promo(self, promo_id: str) -> PromoRecord:
        """Toggle a promo's active status."""
        promo = await self._db.get_promo_by_id(promo_id)
        if not promo:
            raise ValueError(f"Promo not found: {promo_id}")

        promo.is_active = not promo.is_active
        return await self._db.update_promo(promo)

    # ─── Frontend: Eligibility Check ────────────────────────────────────────

    async def check_eligibility(self, user_id: str, promo_code: str) -> PromoEligibilityResponse:
        """
        Check if a user is eligible for a promo.

        Checks:
        1. Promo exists and is active
        2. Not expired
        3. Not started yet (valid_from)
        4. Max uses not reached (global)
        5. User already claimed (max_uses_per_user)
        6. User is targeted (if specific_users)

        Returns eligibility response with reason if not eligible.
        """
        promo = await self._db.get_promo_by_code(promo_code.upper())
        if not promo:
            return PromoEligibilityResponse(eligible=False, reason="Promo code not found")

        if not promo.is_active:
            return PromoEligibilityResponse(eligible=False, reason="Promo is inactive")

        now = datetime.utcnow()
        if promo.valid_until and now > promo.valid_until:
            return PromoEligibilityResponse(eligible=False, reason="Promo has expired")

        if now < promo.valid_from:
            return PromoEligibilityResponse(eligible=False, reason="Promo not yet active")

        # Check global max uses
        if promo.max_uses is not None:
            total_claims = await self._db.count_promo_claims(promo.id or "")
            if total_claims >= promo.max_uses:
                return PromoEligibilityResponse(eligible=False, reason="Promo has reached maximum uses")

        # Check user's claim count
        user_claims = await self._db.count_user_promo_claims(user_id, promo.id or "")
        if user_claims >= promo.max_uses_per_user:
            return PromoEligibilityResponse(eligible=False, reason="You've already claimed this promo")

        # Check user targeting
        if promo.target_type == PromoTargetType.SPECIFIC_USERS:
            if user_id not in promo.target_user_ids:
                return PromoEligibilityResponse(eligible=False, reason="This promo is not available for your account")

        return PromoEligibilityResponse(
            eligible=True,
            promo_code=promo.code,
            credits=promo.credits,
            description=promo.description,
        )

    # ─── Frontend: Claim Promo ─────────────────────────────────────────────

    async def claim_promo(self, user_id: str, promo_code: str) -> ClaimPromoResponse:
        """
        Claim a promo code for a user.

        Flow:
        1. Check eligibility
        2. If eligible, add credits
        3. Record claim
        4. Log to ledger

        This is atomic — if credits fail to add, the claim is not recorded.
        """
        # Step 1: Check eligibility
        eligibility = await self.check_eligibility(user_id, promo_code)
        if not eligibility.eligible:
            return ClaimPromoResponse(success=False, message=eligibility.reason)

        promo = await self._db.get_promo_by_code(promo_code.upper())
        if not promo or not promo.id:
            return ClaimPromoResponse(success=False, message="Promo not found")

        # Step 2: Add credits
        try:
            tx = await self._credit_service.add_credits(
                user_id=user_id,
                amount=promo.credits,
                description=f"Promo: {promo.code}",
                correlation_id=f"promo_{promo.code}_{user_id}",
            )
            logger.info(
                f"Promo credits added: user={user_id}, promo={promo.code}, credits={promo.credits}, tx_id={tx.id}"
            )
        except Exception as e:
            logger.error(f"Failed to add promo credits for {user_id}/{promo_code}: {e}")
            return ClaimPromoResponse(success=False, message="Failed to add credits. Please try again.")

        # Step 3: Record claim
        claim = UserPromoClaim(
            user_id=user_id,
            promo_id=promo.id,
            promo_code=promo.code,
            credits_awarded=promo.credits,
        )
        await self._db.add_promo_claim(claim)

        # Step 4: Log to ledger
        await self._ledger.log_transaction(
            user_id=user_id,
            message="Promo claimed",
            details={"promo_code": promo.code, "credits_awarded": promo.credits, "claim_id": claim.id},
            correlation_id=f"promo_{promo.code}_{user_id}",
        )

        return ClaimPromoResponse(
            success=True,
            credits_awarded=promo.credits,
            promo_code=promo.code,
            message=f"Successfully claimed {promo.credits} credits!",
        )
