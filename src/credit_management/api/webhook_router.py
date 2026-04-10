"""
Unified Webhook Router

Single entry point for all payment provider webhooks.
Routes to the correct provider based on the URL path.

Usage:
    # In main.py
    from credit_management.api.webhook_router import router as webhook_router
    app.include_router(webhook_router)

    # Razorpay configures webhook to point to:
    # POST /webhooks/razorpay
    #
    # Stripe configures webhook to point to:
    # POST /webhooks/stripe
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from ..api.router import _payment_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/{provider_name}")
async def payment_webhook(
    provider_name: str,
    request: Request,
    x_razorpay_signature: Optional[str] = Header(None),
    x_stripe_signature: Optional[str] = Header(None),
):
    """
    Unified webhook endpoint for all payment providers.

    The provider name in the URL path determines which provider handles the event.

    Examples:
        POST /webhooks/razorpay  → RazorpayProvider
        POST /webhooks/stripe    → StripeProvider (future)

    Each provider uses its own signature header:
        - Razorpay: X-Razorpay-Signature
        - Stripe: Stripe-Signature
    """
    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"Webhook router: failed to parse body")
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    # Collect all possible signature headers
    signature = x_razorpay_signature or x_stripe_signature or ""

    try:
        result = await _payment_service.handle_webhook(
            provider_name=provider_name,
            payload=body,
            signature=signature,
        )

        if not result.success:
            if result.error == "invalid_signature":
                return JSONResponse(status_code=401, content={"error": "Invalid signature"})
            return JSONResponse(status_code=400, content={"error": result.error})

        return JSONResponse(status_code=200, content={"status": "ok"})

    except ValueError as e:
        logger.warning(f"Webhook router: signature verification failed for {provider_name}")
        return JSONResponse(status_code=401, content={"error": "Invalid signature"})

    except Exception as e:
        logger.error(f"Webhook router: processing error for {provider_name}")
        return JSONResponse(status_code=500, content={"error": "Webhook processing failed"})
