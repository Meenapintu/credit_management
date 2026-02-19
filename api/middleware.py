"""
FastAPI/Starlette middleware for automatic credit reserve → deduct based on response.

Flow:
  1. Before request: reserve an approximate number of credits (from header or default).
  2. Request is executed.
  3. After response: read actual usage from response body (e.g. total_token),
     deduct that amount, then release the reservation (unreserve).
  So the net deduction is the actual usage; the reservation only holds credits temporarily.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional, Sequence

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..models.credits import ReservedCredits
from ..services.credit_service import CreditService


logger = logging.getLogger(__name__)


def _get_nested(data: dict, key_path: str) -> Optional[Any]:
    """Get a value using dot-notation key path, e.g. 'usage.total_tokens'."""
    keys = key_path.strip().split(".")
    current: Any = data
    for k in keys:
        if not isinstance(current, dict) or k not in current:
            return None
        current = current[k]
    return current


class CreditDeductionMiddleware(BaseHTTPMiddleware):
    """
    Middleware that reserves credits before the request and deducts the actual
    amount from the response body after the API runs.

    - Reserve is approximate (from header or default).
    - Deduction is the actual value read from the response (e.g. total_token).
    - If the response does not contain the usage key, only the reservation is
      released (no deduction). On request error, reservation is released without deduction.
    """

    def __init__(
        self,
        app: Any,
        credit_service: CreditService,
        *,
        path_prefix: str = "/api",
        user_id_header: str = "X-User-Id",
        estimated_tokens_header: str = "X-Estimated-Tokens",
        default_estimated_tokens: int = 100,
        response_usage_key: str = "total_token",
        skip_paths: Optional[Sequence[str]] = None,
    ) -> None:
        super().__init__(app)
        self.credit_service = credit_service
        self.path_prefix = path_prefix.rstrip("/")
        self.user_id_header = user_id_header
        self.estimated_tokens_header = estimated_tokens_header
        self.default_estimated_tokens = default_estimated_tokens
        self.response_usage_key = response_usage_key
        self.skip_paths = tuple(skip_paths or ())

    def _should_apply(self, path: str) -> bool:
        if not path.startswith(self.path_prefix + "/") and path != self.path_prefix:
            return False
        for skip in self.skip_paths:
            if path == skip or path.startswith(skip.rstrip("/") + "/"):
                return False
        return True

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Any]
    ) -> Response:
        if not self._should_apply(request.url.path):
            return await call_next(request)

        user_id = request.headers.get(self.user_id_header)
        if not user_id:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing user identification (e.g. X-User-Id header)."},
            )

        try:
            estimated_str = request.headers.get(
                self.estimated_tokens_header,
                str(self.default_estimated_tokens),
            )
            estimated = max(1, int(estimated_str))
        except ValueError:
            estimated = self.default_estimated_tokens

        reservation: Optional[ReservedCredits] = None
        try:
            reservation = await self.credit_service.reserve_credits(
                user_id=user_id,
                amount=estimated,
                reason="api-middleware",
                correlation_id=request.headers.get("X-Request-Id"),
            )
        except ValueError as e:
            if "insufficient" in str(e).lower():
                return JSONResponse(
                    status_code=402,
                    content={
                        "detail": "Insufficient credits for this request.",
                        "code": "INSUFFICIENT_CREDITS",
                    },
                )
            raise

        request.state.credit_reservation = reservation

        try:
            response = await call_next(request)
        except Exception:
            await self.credit_service.unreserve_credits(reservation)
            raise

        body_bytes: Optional[bytes] = None
        try:
            body_bytes = getattr(response, "body", None)
            if body_bytes is None and hasattr(response, "body_iterator"):
                body_bytes = b"".join([chunk async for chunk in response.body_iterator])
        except Exception:
            body_bytes = None

        deducted = 0
        try:
            if body_bytes:
                data = json.loads(body_bytes)
                raw = _get_nested(data, self.response_usage_key)
                if raw is not None:
                    actual = int(raw)
                    if actual > 0:
                        await self.credit_service.unreserve_credits(reservation)
                        await self.credit_service.deduct_credits(
                            user_id=user_id,
                            amount=actual,
                            description="api-middleware",
                            correlation_id=request.headers.get("X-Request-Id"),
                        )
                        deducted = actual
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning(
                "Credit middleware: could not read usage from response: %s",
                e,
                extra={"path": request.url.path, "user_id": user_id},
            )
        finally:
            if deducted == 0:
                await self.credit_service.unreserve_credits(reservation)

        if body_bytes is not None:
            headers = dict(response.headers)
            if deducted > 0:
                headers["X-Credits-Deducted"] = str(deducted)
            return Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=headers,
                media_type=getattr(response, "media_type", "application/json"),
            )
        return response
