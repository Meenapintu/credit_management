from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Optional

from pydantic import Field

from .base import DBSerializableModel


class LedgerEventType(str, Enum):
    TRANSACTION = "transaction"
    ERROR = "error"
    SYSTEM = "system"


class LedgerEntry(DBSerializableModel):
    """
    Structured ledger entry persisted to DB and optionally mirrored to file log.
    """

    collection_name: ClassVar[str] = "credit_ledger"

    id: Optional[str] = Field(default=None)
    event_type: LedgerEventType
    user_id: Optional[str] = None
    correlation_id: Optional[str] = Field(
        default=None,
        description="Correlation id for tracing a logical operation across components.",
    )
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)

