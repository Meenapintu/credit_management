"""
Example: FastAPI app with credit deduction middleware.

- Middleware reserves approximate credits before the request.
- After the API runs, it reads total_token (or configurable key) from the JSON
  response, deducts that amount, and releases the reservation.
- Client sends X-User-Id and optionally X-Estimated-Tokens.
- Response includes X-Credits-Deducted when credits were deducted.

Run (from app directory, with credits and DB set up):
  uvicorn app.credit_management.examples.fastapi_middleware_example:app --reload
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

# Wire the credit management stack (same as router.py)
import os
import sys

# Ensure app is on path when running this example
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.credit_management.api.middleware import CreditDeductionMiddleware
from app.credit_management.api.router import _create_db_manager
from app.credit_management.cache.memory import InMemoryAsyncCache
from app.credit_management.logging.ledger_logger import LedgerLogger
from app.credit_management.services.credit_service import CreditService


def _get_credit_service():
    db = _create_db_manager()
    cache = InMemoryAsyncCache()
    ledger = LedgerLogger(db=db, file_path=Path("credit_ledger.jsonl"))
    return CreditService(db=db, ledger=ledger, cache=cache)


app = FastAPI(title="API with credit deduction middleware")

# Add credit deduction middleware: reserve before request, deduct from response total_token
credit_service = _get_credit_service()
app.add_middleware(
    CreditDeductionMiddleware,
    credit_service=credit_service,
    path_prefix="/api",  # only apply to /api/* routes
    user_id_header="X-User-Id",
    estimated_tokens_header="X-Estimated-Tokens",
    default_estimated_tokens=100,
    response_usage_key="total_token",  # or "usage.total_tokens" for OpenAI-style
    skip_paths=("/api/health",),
)


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    message: str
    total_token: int  # middleware reads this and deducts credits


@app.post("/api/chat", response_model=ChatResponse)
async def chat(body: ChatRequest):
    """Example endpoint: returns total_token so middleware can deduct."""
    # Simulate doing work and counting tokens (e.g. LLM call)
    total_token = min(len(body.message.split()) * 2, 50)
    return ChatResponse(message=f"Echo: {body.message}", total_token=total_token)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Optional: expose credit balance so clients can check before calling
@app.get("/api/balance/{user_id}")
async def balance(user_id: str):
    bal = await credit_service.get_user_credits(user_id)
    return {"user_id": user_id, "credits": bal}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.credit_management.examples.fastapi_middleware_example:app", host="0.0.0.0", port=8000, reload=True)
