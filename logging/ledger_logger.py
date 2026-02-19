from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ..db.base import BaseDBManager
from ..models.ledger import LedgerEntry, LedgerEventType


class LedgerLogger:
    """
    Structured ledger logger that writes to a file and the database.

    File logging is append-only, line-delimited JSON for easier ingestion
    by log aggregators. DB logging uses the `LedgerEntry` model and the
    configured `BaseDBManager`.
    """

    def __init__(self, db: BaseDBManager, file_path: Path) -> None:
        self._db = db
        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

    async def log_transaction(
        self,
        user_id: str,
        message: str,
        details: dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> None:
        await self._log(
            LedgerEventType.TRANSACTION,
            user_id=user_id,
            message=message,
            details=details,
            correlation_id=correlation_id,
        )

    async def log_error(
        self,
        message: str,
        details: dict[str, Any],
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        await self._log(
            LedgerEventType.ERROR,
            user_id=user_id,
            message=message,
            details=details,
            correlation_id=correlation_id,
        )

    async def _log(
        self,
        event_type: LedgerEventType,
        user_id: Optional[str],
        message: str,
        details: dict[str, Any],
        correlation_id: Optional[str],
    ) -> None:
        entry = LedgerEntry(
            event_type=event_type,
            user_id=user_id,
            message=message,
            details=details,
            correlation_id=correlation_id,
        )

        # Persist to DB via the configured manager.
        await self._db.add_ledger_entry(entry)
        # NOTE: We intentionally do not fail the main flow if file logging fails.
        try:
            line = json.dumps(entry.serialize_for_db(), default=str)
            with self._file_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            # Best-effort; surface via monitoring in a real deployment.
            pass

