"""
Promo System Models

Supports:
- One-time promos per user
- Promos for all users
- Targeted promos for specific users
- Expiry and usage limits
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import Field

from .base import DBSerializableModel


class PromoTargetType(str, Enum):
    ALL_USERS = "all_users"
    SPECIFIC_USERS = "specific_users"


class PromoRecord(DBSerializableModel):
    """
    Promo definition stored in the database.
    """

    collection_name: ClassVar[str] = "promos"

    id: Optional[str] = Field(default=None)
    code: str  # e.g., "WELCOME50", "LAUNCH2026"
    credits: float  # Credits to award
    description: Optional[str] = None

    # Targeting
    target_type: PromoTargetType = PromoTargetType.ALL_USERS
    target_user_ids: List[str] = Field(default_factory=list)  # Used when target_type = specific_users

    # Limits
    max_uses: Optional[int] = None  # None = unlimited
    max_uses_per_user: int = 1  # Default: one-time per user

    # Timing
    valid_from: datetime = Field(default_factory=datetime.utcnow)
    valid_until: Optional[datetime] = None  # None = never expires

    is_active: bool = True

    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None


class UserPromoClaim(DBSerializableModel):
    """
    Record of a user claiming a promo.
    """

    collection_name: ClassVar[str] = "user_promo_claims"

    id: Optional[str] = Field(default=None)
    user_id: str
    promo_id: str
    promo_code: str
    credits_awarded: float
    claimed_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PromoEligibilityResponse(DBSerializableModel):
    """Response when frontend checks promo eligibility."""

    eligible: bool
    promo_code: Optional[str] = None
    credits: float = 0
    description: Optional[str] = None
    reason: Optional[str] = None  # Why ineligible (if not eligible)


class CreatePromoRequest(DBSerializableModel):
    """Request to create a promo (admin)."""

    code: str
    credits: float
    description: Optional[str] = None
    target_type: PromoTargetType = PromoTargetType.ALL_USERS
    target_user_ids: List[str] = Field(default_factory=list)
    max_uses: Optional[int] = None
    max_uses_per_user: int = 1
    valid_until: Optional[datetime] = None
    is_active: bool = True


class PromoResponse(DBSerializableModel):
    """Response for a promo record."""

    id: str
    code: str
    credits: float
    description: Optional[str]
    target_type: str
    target_user_ids: List[str]
    max_uses: Optional[int]
    max_uses_per_user: int
    valid_from: str
    valid_until: Optional[str]
    is_active: bool
    total_claims: int = 0
    created_at: str


class ClaimPromoRequest(DBSerializableModel):
    """Request to claim a promo (frontend)."""

    promo_code: str


class ClaimPromoResponse(DBSerializableModel):
    """Response after claiming a promo."""

    success: bool
    credits_awarded: float = 0
    promo_code: Optional[str] = None
    message: Optional[str] = None
