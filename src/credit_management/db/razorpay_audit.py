"""
Razorpay Audit Log Repository

Simple append-only storage for all Razorpay payment events.
Stores raw JSON responses - no Pydantic parsing, no indexes.
Used for: audit trail, debugging, customer support queries.

Collection: razorpay_audit_logs

Query examples:
  # All events for a payment link
  db.razorpay_audit_logs.find({"payment_link_id": "plink_xxx"})

  # All events for a user
  db.razorpay_audit_logs.find({"user_id": "user123"})

  # Stuck webhooks (inbound, not processed)
  db.razorpay_audit_logs.find({"direction": "inbound", "processed": false})
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


class RazorpayAuditLogRepo:
    """Simple append-only audit log repository."""

    COLLECTION = "razorpay_audit_logs"

    def __init__(self, db):
        """
        Args:
            db: MongoDB database instance (AsyncIOMotorDatabase)
        """
        self._db = db
        self._col = None

    @property
    def col(self):
        if self._col is None:
            self._col = self._db[self.COLLECTION]
        return self._col

    async def log_outbound(
        self,
        payment_link_id: str,
        user_id: str,
        event_type: str,
        request_payload: Dict[str, Any],
        response_payload: Dict[str, Any],
        http_status: int = 200,
    ) -> str:
        """Log an outbound API call (create payment link).

        Args:
            payment_link_id: Razorpay's plink_xxx
            user_id: Our user ID
            event_type: "payment_link.created"
            request_payload: What we sent to Razorpay
            response_payload: Full Razorpay response JSON
            http_status: HTTP status code

        Returns:
            Audit log entry ID
        """
        entry = {
            "payment_link_id": payment_link_id,
            "user_id": user_id,
            "event_type": event_type,
            "direction": "outbound",
            "raw_payload": {
                "request": request_payload,
                "response": response_payload,
            },
            "http_status": http_status,
            "processed": True,
            "processed_at": datetime.utcnow().isoformat() + "Z",
            "error": None,
        }
        result = await self.col.insert_one(entry)
        return str(result.inserted_id)

    async def log_inbound(
        self,
        payment_link_id: Optional[str],
        user_id: Optional[str],
        event_type: str,
        raw_payload: Dict[str, Any],
        http_status: int = 200,
        processed: bool = True,
        error: Optional[str] = None,
    ) -> str:
        """Log an inbound webhook event.

        Args:
            payment_link_id: Razorpay's plink_xxx (if available)
            user_id: Our user ID (if extracted from notes)
            event_type: "payment.authorized", "payment.captured", "payment_link.paid", etc.
            raw_payload: Full webhook payload as received from Razorpay
            http_status: HTTP status code we'll return
            processed: Whether we successfully processed this webhook
            error: Error message if processing failed

        Returns:
            Audit log entry ID
        """
        entry = {
            "payment_link_id": payment_link_id,
            "user_id": user_id,
            "event_type": event_type,
            "direction": "inbound",
            "raw_payload": raw_payload,
            "http_status": http_status,
            "processed": processed,
            "processed_at": datetime.utcnow().isoformat() + "Z",
            "error": error,
        }
        result = await self.col.insert_one(entry)
        return str(result.inserted_id)

    async def get_by_payment_link(self, payment_link_id: str) -> List[Dict[str, Any]]:
        """Get all audit log entries for a payment link, ordered by time."""
        cursor = self.col.find({"payment_link_id": payment_link_id}).sort("processed_at", 1)
        return await cursor.to_list(length=None)

    async def get_by_user(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent audit log entries for a user."""
        cursor = (
            self.col.find({"user_id": user_id})
            .sort("processed_at", -1)
            .limit(limit)
        )
        return await cursor.to_list(length=None)

    async def get_unprocessed(self) -> List[Dict[str, Any]]:
        """Get inbound events that failed processing (for debugging/retry)."""
        cursor = (
            self.col.find({"direction": "inbound", "processed": False})
            .sort("processed_at", -1)
        )
        return await cursor.to_list(length=None)
