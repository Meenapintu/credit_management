# Open Source Credit Management 

[![PyPI version](https://badge.fury.io/py/credit-management.svg)](https://pypi.org/project/credit-management/)
[![Python](https://img.shields.io/pypi/pyversions/credit-management.svg)](https://pypi.org/project/credit-management/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Downloads](https://img.shields.io/pypi/dw/Credit-Management?color=00AA00)](https://pypi.org/project/credit-management/)
[![FastAPI](https://img.shields.io/badge/PRs-welcome-brightgreencolor=00AA00)](https://fastapi.tiangolo.com/)
[![FastAPI](https://img.shields.io/badge/Framework-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)

**Production-ready credit management system** for AI/LLM applications. Automatic credit deduction, Razorpay payment integration, subscription plans, promo codes, and multi-provider webhooks.

## ✨ Why Credit Management?

Building a credit system for your AI application shouldn't mean reinventing billing, payments, and usage tracking. This library gives you:

- **🪙 Credit Reservations & Deductions** — Reserve credits before API calls, deduct actual usage after. Prevents overcharging on failures.
- **💳 Payment Integration** — Razorpay payment links with atomic credit updates.
- **📊 Subscription Plans** — Daily, monthly, yearly plans with auto-renew and credit allocation.
- **🎫 Promo Codes** — Targeted promos with usage limits, expiry dates, and claim tracking.
- **🔒 Request-Scoped Context** — Automatic credit tracking via Python `contextvars`. Add LLM usage in your handlers, middleware handles the rest.
- **🗄️ Database Agnostic** — MongoDB or in-memory backend. Extensible to any database via `BaseDBManager` interface.
- **📋 Dual-Write Ledger** — Database + append-only file for audit trails and debugging.
- **🔔 Notifications** — Low credits, expiring credits, transaction errors — pluggable notification queue.

> 💡 **Framework-agnostic, database-agnostic.** Works with FastAPI, Flask, Django, or any async framework. Swap MongoDB for PostgreSQL, SQLite, or implement `BaseDBManager` for your database.

## 🚀 Quick Start

### Installation

```bash
pip install credit-management
```

### Basic Setup (3 Lines)

```python
from fastapi import FastAPI
from credit_management.api.frontend_router import router as frontend_router
from credit_management.api.middleware import CreditDeductionMiddleware, _credit_service

app = FastAPI()
app.include_router(frontend_router)

# Automatic credit reservation → deduction on every request
app.add_middleware(
    CreditDeductionMiddleware,
    credit_service=_credit_service,
    path_prefix="/api",
    user_id_header="X-User-Id",
    default_estimated_tokens=100,
    skip_paths=("/api/health",),
)
```

### Track LLM Usage in Your Handlers

```python
from credit_management.context.creditContext import addLlmUsage

# After your LLM call
addLlmUsage(
    model="gpt-4o",
    provider="openai",
    cost=0.05,
    metadata={"prompt_tokens": 100, "completion_tokens": 50},
)
```

```python
from credit_management.context.creditContext import addLlmUsage

# After your LLM call
addLlmUsage(
    model="gpt-4o",
    provider="openai",
    cost=0.05,
    metadata={"prompt_tokens": 100, "completion_tokens": 50},
)
```


The middleware automatically reserves credits before the request and deducts actual usage after. If the request fails, credits are unreserved — no overcharging.

### Accept Payments via Razorpay

```python
from credit_management.api.router import _payment_service, setup_razorpay_provider

# Initialize (auto-loaded from env vars)
setup_razorpay_provider(
    key_id="rzp_live_xxx",
    key_secret="xxx",
    webhook_secret="whsec_xxx",
    app_base_url="https://yourapp.com",
)

# Create payment link
response = await _payment_service.create_payment_link(
    user_id="user-123",
    amount_inr=500.0,
    provider_name="razorpay",
)
```


## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Your FastAPI App                         │
│  ┌──────────────────┐     ┌──────────────────────────────┐ │
│  │ CreditDeduction  │────▶│  ContextVar (LLM Usage)      │ │
│  │ Middleware       │     │  addLlmUsage(model, cost)    │ │
│  └────────┬─────────┘     └──────────────┬───────────────┘ │
│           │                              │                  │
│           ▼                              ▼                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              CreditService (Core)                   │   │
│  │  reserve → execute → deduct / unreserve             │   │
│  └─────────────────────────┬───────────────────────────┘   │
└────────────────────────────┼───────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐  ┌──────────┐  ┌──────────────┐
        │ MongoDB  │  │ Ledger   │  │ Payment      │
        │ (or Mem) │  │ (DB+File)│  │ Providers    │
        └──────────┘  └──────────┘  └──────────────┘
                                    │ Razorpay │ Stripe │
                                    └──────────┴────────┘
```

### Core Components

| Component | Purpose | Key Feature |
|-----------|---------|-------------|
| **CreditService** | Core credit operations | Cache-first reads, delta-based cache updates, transaction logging |
| **PaymentService** | Payment processing | Atomic updates, reference_id tracking, idempotent webhooks |
| **SubscriptionService** | Plan management | Daily/monthly/yearly plans, auto-renew, credit allocation |
| **PromoService** | Promo code system | Eligibility checks, usage limits, expiry tracking |
| **CreditDeductionMiddleware** | Automatic credit deduction | Reserve → execute → deduct/unreserve flow |
| **LedgerLogger** | Audit logging | Dual-write: database + append-only JSONL file |
| **NotificationService** | User notifications | Low credits, expiry warnings, transaction errors |

## 📖 API Endpoints

### User Endpoints (`/credits/*`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/credits/balance/{user_id}` | GET | Get available credit balance |
| `/credits/payments/create` | POST | Create Razorpay payment link |
| `/credits/payments/history` | GET | Payment history with pagination |
| `/credits/payments/{payment_id}` | GET | Get specific payment record |
| `/credits/promo/eligibility?promo_code=X` | GET | Check promo eligibility |
| `/credits/promo/claim` | POST | Claim promo code |

### Admin Endpoints (`/admin/credits/*`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/credits/add` | POST | Add credits to a user |
| `/admin/credits/deduct` | POST | Deduct credits from a user |
| `/admin/credits/plans` | POST | Create subscription plan |
| `/admin/credits/promos` | POST/GET | Manage promo codes |

### Webhook Endpoint

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhooks/{provider_name}` | POST | Unified webhook handler (Razorpay, Stripe, etc.) |

## 🔐 Security Features

- **Atomic Credit Updates** — MongoDB conditional updates prevent double-crediting on concurrent webhooks
- **HMAC-SHA256 Webhook Verification** — Timing-safe signature verification for all payment webhooks
- **State Machine Enforcement** — Payment status only moves forward (prevents status rollback attacks)
- **Immutable Field Validation** — Validates `user_id`, `amount` consistency across webhook events
- **Request-Scoped Isolation** — Python `contextvars` ensure credit tracking is per-request, thread-safe

## 🗄️ Database Backends

### MongoDB (Production)

```bash
export CREDIT_MONGO_URI="mongodb://localhost:27017"
export CREDIT_MONGO_DB="credit_management"
```

### In-Memory (Testing)

```python
from credit_management.db.memory import InMemoryDBManager
db = InMemoryDBManager()
```

### Custom Backend

Implement `BaseDBManager` interface for any database (SQL, NoSQL, in-memory). Use `schema_generator.py` for automatic SQL DDL or MongoDB validators.

```bash
python -m schema_generator --backend sql --dialect postgres
python -m schema_generator --backend nosql
```

## ⚙️ Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `CREDIT_MONGO_URI` | — | MongoDB connection string |
| `CREDIT_MONGO_DB` | `credit_management` | Database name |
| `RAZORPAY_KEY_ID` | — | Razorpay API key ID |
| `RAZORPAY_KEY_SECRET` | — | Razorpay API key secret |
| `RAZORPAY_WEBHOOK_SECRET` | — | Webhook signing secret |
| `APP_BASE_URL` | `http://localhost:8000` | Your application base URL |

## 🔧 Manual HTTP API Usage

Prefer curl or HTTP clients? The library exposes REST endpoints you can call directly:

### Credits

```bash
# Add credits (admin)
curl -X POST http://localhost:8000/admin/credits/add \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user-1", "amount": 100, "description": "Welcome bonus"}'

# Get balance
curl http://localhost:8000/credits/balance/user-1

# Check promo eligibility
curl "http://localhost:8000/credits/promo/eligibility?promo_code=LAUNCH100"

# Claim promo
curl -X POST http://localhost:8000/credits/promo/claim \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user-1", "promo_code": "LAUNCH100"}'

# Manual credit deduction (e.g., for non-API costs)
curl -X POST http://localhost:8000/admin/credits/deduct \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user-1", "amount": 10, "description": "Storage overage"}'

# Create subscription plan (admin)
curl -X POST http://localhost:8000/admin/credits/plans \
  -H "Content-Type: application/json" \
  -d '{"name": "Pro", "credit_limit": 10000, "price": 29.99, "billing_period": "MONTHLY"}'
```

### Payments

```bash
# Create payment link
curl -X POST http://localhost:8000/credits/payments/create \
  -H "Content-Type: application/json" \
  -H "X-User-Id: user-1" \
  -d '{"amount_inr": 500, "provider": "razorpay"}'

# Payment history
curl "http://localhost:9000/credits/payments/history?limit=10" \
  -H "X-User-Id: user-1"

# Handle Razorpay webhook
curl -X POST http://localhost:8000/webhooks/razorpay \
  -H "Content-Type: application/json" \
  -H "X-Razorpay-Signature: <signature>" \
  -d '{"event": "payment_link.paid", "payload": {...}}'
```

## 🔄 Manual Credit Deduction Use Cases

The middleware handles automatic credit deduction for API calls, but you may need manual deduction for:

### Non-API Costs

```python
from credit_management.api.router import _credit_service

# Storage overage
await _credit_service.deduct_credits_after_service(
    user_id="user-1",
    amount=10,
    description="Storage overage for 5GB extra usage",
)

# Manual adjustment
await _credit_service.deduct_credits(
    user_id="user-1",
    amount=50,
    description="Manual credit adjustment for billing cycle",
)
```

### Refunds

```python
# Refund credits to user
await _credit_service.add_credits(
    user_id="user-1",
    amount=100,
    description="Refund for failed API call #12345",
)
```

### Scheduled Credit Allocation

```python
from credit_management.services.expiration_service import ExpirationService

expiration = ExpirationService(db=_db, ledger=_ledger, credit_service=_credit_service)

# Check and expire credits
expired = await expiration.check_credit_expiration(user_id="user-1")
print(f"Expired {expired} credits for user-1")
```

## 🧪 Testing

Use the in-memory backend for zero-dependency tests:

```python
import asyncio
from credit_management.db.memory import InMemoryDBManager
from credit_management.services.credit_service import CreditService
from credit_management.logging.ledger_logger import LedgerLogger

async def test():
    db = InMemoryDBManager()
    ledger = LedgerLogger(db=db, file_path="/tmp/test_ledger.log")
    service = CreditService(db=db, ledger=ledger)
    
    await service.add_credits(user_id="test", amount=100)
    info = await service.get_user_credits_info("test")
    assert info.available == 100

asyncio.run(test())
```

## 📋 Full Example: AI API with Credit Deduction

```python
from fastapi import FastAPI, Depends
from credit_management.api.router import (
    frontend_router, webhook_router, backend_router,
    _credit_service, setup_razorpay_provider,
)
from credit_management.api.middleware import CreditDeductionMiddleware
from credit_management.context.creditContext import addLlmUsage

app = FastAPI()

# Include all routers
app.include_router(frontend_router)
app.include_router(backend_router, prefix="/admin")
app.include_router(webhook_router)

# Automatic credit middleware
app.add_middleware(
    CreditDeductionMiddleware,
    credit_service=_credit_service,
    path_prefix="/api",
    user_id_header="X-User-Id",
    default_estimated_tokens=100,
    skip_paths=("/api/health",),
)

# Initialize payment provider
setup_razorpay_provider()

@app.post("/api/generate")
async def generate(request: Request):
    user_id = request.headers.get("X-User-Id")
    
    # Your LLM call here
    response = await call_llm("gpt-4o", prompt)
    
    # Track usage — middleware deducts automatically
    addLlmUsage(
        model="gpt-4o",
        provider="openai",
        cost=0.05,
        metadata={"tokens": response.usage.total_tokens},
    )
    return response
```

## 🚀 Ready for Production

- [x] MongoDB or in-memory backend
- [x] Razorpay payment integration
- [x] Atomic credit updates (race-condition safe)
- [x] Webhook signature verification
- [x] Subscription plans with auto-renew
- [x] Promo codes with usage limits
- [x] Dual-write ledger (DB + file)
- [x] Notification system (low credits, expiry)
- [x] Cache with delta-based updates
- [x] Request-scoped context isolation
- [x] Database-agnostic interface
- [x] Schema generator (SQL + NoSQL)

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

## 🤝 Contributing

Contributions welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## 📦 Package Links

- [PyPI](https://pypi.org/project/credit-management/)
- [GitHub](https://github.com/Meenapintu/credit_management)
- [Changelog](CHANGELOG.md)
- [Documentation](https://github.com/Meenapintu/credit_management)
