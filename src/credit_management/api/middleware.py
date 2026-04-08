"""
FastAPI/Starlette middleware for automatic credit reserve → deduct based on response.

Flow:
  1. Before request: reserve an approximate number of credits (from header or default).
  2. Request is executed.
  3. After response: read actual usage from response body (e.g. total_token),
     deduct that amount, then release the reservation (unreserve).
  So the net deduction is the actual usage; the reservation only holds credits temporarily.

LLM Usage Metadata:
  If the request handler records LLM usage via addLlmUsage(), the middleware
  passes that metadata to the credit deduction transaction for detailed tracking.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from credit_management.context.creditContext import LLMUsage, getLlmUsages

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
        default_estimated_tokens: float = 100,
        skip_paths: Optional[Sequence[str]] = None,
    ) -> None:
        super().__init__(app)
        self.credit_service = credit_service
        self.path_prefix = path_prefix.rstrip("/")
        self.user_id_header = user_id_header
        self.estimated_tokens_header = estimated_tokens_header
        self.default_estimated_tokens = default_estimated_tokens
        self.skip_paths = tuple(skip_paths or ())

    def _should_apply(self, path: str) -> bool:
        if not path.startswith(self.path_prefix + "/") and path != self.path_prefix:
            return False
        for skip in self.skip_paths:
            if path == skip or path.startswith(skip.rstrip("/") + "/"):
                return False
        return True

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
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
            estimated = max(1, float(estimated_str))
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
                        "detail": f"""{ str(e)}""",
                        "code": "INSUFFICIENT_CREDITS",
                    },
                )
            raise

        request.state.credit_reservation = reservation

        try:
            response = await call_next(request)
        except Exception:
            await self.credit_service.unreserve_credits(reservation, correlation_id=request.headers.get("X-Request-Id"))
            raise

        deducted = 0
        try:

            # Collect LLM usage metadata from context (set by LiteLLM SDK)
            llmUsages: list[LLMUsage] = getLlmUsages()
            deducted = sum(u.cost for u in llmUsages if u.cost > 0)
            if deducted > 0:
                await self.credit_service.unreserve_credits(
                    reservation, correlation_id=request.headers.get("X-Request-Id")
                )
                # Use deduct_credits_after_service to allow negative balance
                # (actual usage may exceed reserved amount)
                await self.credit_service.deduct_credits_after_service(
                    user_id=user_id,
                    amount=deducted,
                    description=f"api-middleware",
                    correlation_id=request.headers.get("X-Request-Id"),
                    metadata={
                        "llm_usage": [
                            {
                                "model": u.model,
                                "provider": u.provider,
                                "cost": u.cost,
                                **u.metadata,
                            }
                            for u in llmUsages
                            if u.cost > 0
                        ]
                    },
                )
            elif deducted < 0:
                logger.error(
                    "Credit middleware: received negative usages %s",
                    str(llmUsages),
                    extra={"path": request.url.path, "user_id": user_id},
                )
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning(
                "Credit middleware: could not read usage from response: %s",
                e,
                extra={"path": request.url.path, "user_id": user_id},
            )
        except Exception:
            logger.warning(
                "Credit middleware: could not read usage from response: %s",
                e,
                extra={"path": request.url.path, "user_id": user_id},
            )

        finally:
            if deducted == 0:
                await self.credit_service.unreserve_credits(
                    reservation, correlation_id=request.headers.get("X-Request-Id")
                )
            response.headers["X-Credits-Deducted"] = str(deducted)
        return response
