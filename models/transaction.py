from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Dict, Optional

from pydantic import Field

from .base import DBSerializableModel


class TransactionType(str, Enum):
    ADD = "add"
    DEDUCT = "deduct"
    EXPIRE = "expire"
    RESERVE = "reserve"
    COMMIT_RESERVED = "commit_reserved"
    RELEASE_RESERVED = "release_reserved"


class Transaction(DBSerializableModel):
    """
    Logical transaction record used for both API and DB.
    """

    collection_name: ClassVar[str] = "credit_transactions"

    id: Optional[str] = Field(default=None)
    user_id: str
    credits_added: int = 0
    credits_deducted: int = 0
    current_credits: int
    transaction_type: TransactionType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    description: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

