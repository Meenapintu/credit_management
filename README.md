<h1 align="center">
 Credit Management — Plug-and-Play Credits & Subscriptions
 </h1>

<p align="center">
  <img alt="Static Badge" src="https://img.shields.io/badge/PRs-welcome-brightgreen?style=for-the-badge&color=00AA00">
<img alt="PyPI - Python Version" src="https://img.shields.io/pypi/pyversions/Credit-Management?style=for-the-badge&labelColor=00AA00">
<img alt="PyPI - Implementation" src="https://img.shields.io/pypi/implementation/Credit-Management?style=for-the-badge"><img alt="PyPI - Wheel" src="https://img.shields.io/pypi/wheel/Credit-Management?style=for-the-badge">
<img alt="PyPI - Downloads" src="https://img.shields.io/pypi/dd/Credit-Management?style=for-the-badge">
<img alt="PyPI - Version" src="https://img.shields.io/pypi/v/Credit-Management?style=for-the-badge">
<img alt="GitHub Actions Workflow Status" src="https://img.shields.io/github/actions/workflow/status/meenapintu/credit_management/pypi-package.yml?style=for-the-badge">
<img alt="PyPI - License" src="https://img.shields.io/pypi/l/Credit-Management?style=for-the-badge">

</p>


**Production-ready, database-agnostic credit and subscription management for any Python service or API.**

Manage user credits, subscriptions, expirations, reservations, and notifications with a single, pluggable module. No lock-in: use **in-memory** for development, **MongoDB** for scale, or plug in your own SQL/NoSQL backend.

---

## Why Use This?

| You need… | We give you… |
|-----------|----------------|
| **Credits that “just work”** | Add, deduct, reserve, expire — with a full audit trail and ledger. |
| **One codebase, any database** | Swap backends via config. Same API whether you use MongoDB, Postgres, or in-memory. |
| **Subscriptions & plans** | Plans with credit limits, billing periods (daily/monthly/yearly), and validity. |
| **Expiration & notifications** | Credits that expire by plan, low-credit alerts, and expiring-credits reminders via a message queue. |
| **Auditability & debugging** | Every change is a transaction; ledger entries go to DB + structured JSON log files. |
| **Async, cacheable, scalable** | Async-first design, optional caching for balances/plans, and queue-based notifications. |

**Use it when:** you're building SaaS, API products, usage-based billing, prepaid credits, or any app where “credits” or “subscription limits” are core — and you want a **reusable, testable, open-source** solution instead of rolling your own.

---

## Features

- **Credit operations** — Add, deduct, expire; reserve → commit or release; full history and “expiring in N days” queries.
- **Subscription plans** — Create/update/delete plans; assign/upgrade/remove user plans; daily/monthly/yearly billing and validity.
- **Expiration & allocation** — Check and run credit expiration; allocate subscription credits (e.g. from a scheduler).
- **Notifications** — Low-credits and expiring-credits events enqueued to a pluggable queue (email/SMS/push later).
- **Ledger & monitoring** — Structured ledger (transaction/error/system) written to DB and to a JSON log file for debugging.
- **Schema generator** — One-time CLI to generate SQL DDL or NoSQL schema from Pydantic models; add a field in the model → regenerate schema.
- **Pluggable backends** — `BaseDBManager` + implementations: **In-Memory** (tests/dev), **MongoDB** (Motor). Add Postgres/SQLite by implementing the same interface.
- **Pydantic everywhere** — Request/response and domain models are Pydantic; validation and serialization are consistent across API and DB.

---

## Quick Start

### 1. Install


## Installation

Install the package from PyPI:

```bash
pip install Credit-Management
```

Depending on your use case, you might need to install extra dependencies:
- If you are using the FastAPI router, install `fastapi`.
- If you are using the MongoDB backend, install `motor`.


From your app (or repo) root:

```bash
# If using this as part of a larger app, ensure dependencies are installed:
pip install fastapi pydantic motor  # motor only if using MongoDB
```

### 2. Mount the API (FastAPI)

```python
from fastapi import FastAPI
from credit_management.api.router import router as credit_router

app = FastAPI()
app.include_router(credit_router)  # prefix is /credits
```

### 3. Use the HTTP API

```bash
# Add credits
curl -X POST http://localhost:8000/credits/add \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user-1", "amount": 100, "description": "Welcome bonus"}'

# Get balance
curl http://localhost:8000/credits/balance/user-1

# Deduct credits
curl -X POST http://localhost:8000/credits/deduct \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user-1", "amount": 30}'

# Create a subscription plan
curl -X POST http://localhost:8000/credits/plans \
  -H "Content-Type: application/json" \
  -d '{"name": "Pro", "credit_limit": 500, "price": 9.99, "billing_period": "monthly", "validity_days": 30}'
```

---

## Automatic credit deduction middleware

Use **reserve-then-deduct** on selected routes: the middleware reserves an approximate number of credits before the request, runs your API, then reads the **actual usage** from the response (e.g. `total_token`) and deducts that amount, then releases the reservation. Net effect: only the actual usage is deducted; the reservation is a temporary hold.

### Flow

1. **Before request:** Reserve credits (from `X-Estimated-Tokens` header or a default).
2. **Request runs:** Your endpoint executes as usual.
3. **After response:** Middleware parses the JSON response for a configurable key (e.g. `total_token` or `usage.total_tokens`), deducts that amount, and unreserves the hold.

If the response has no usage key or the request fails, only the reservation is released (no deduction).

### Setup

```python
from fastapi import FastAPI
from credit_management.api.middleware import CreditDeductionMiddleware
from credit_management.api.router import _create_db_manager
from credit_management.services.credit_service import CreditService
from credit_management.logging.ledger_logger import LedgerLogger
from credit_management.cache.memory import InMemoryAsyncCache
from pathlib import Path

app = FastAPI()
db = _create_db_manager()
ledger = LedgerLogger(db=db, file_path=Path("credit_ledger.jsonl"))
credit_service = CreditService(db=db, ledger=ledger, cache=InMemoryAsyncCache())

app.add_middleware(
    CreditDeductionMiddleware,
    credit_service=credit_service,
    path_prefix="/api",                    # only /api/* routes
    user_id_header="X-User-Id",
    estimated_tokens_header="X-Estimated-Tokens",
    default_estimated_tokens=100,
    response_usage_key="total_token",     # or "usage.total_tokens" for OpenAI-style
    skip_paths=("/api/health",),
)
```

### Request / response

- **Client sends:** `X-User-Id` (required), optional `X-Estimated-Tokens` (reserve amount).
- **Your endpoint** returns JSON that includes the actual usage, e.g. `{"message": "...", "total_token": 42}`.
- **Response header:** `X-Credits-Deducted` is set to the deducted amount when applicable.
- **Errors:** Missing `X-User-Id` → 401; insufficient credits for reserve → 402.

A full runnable example is in `examples/fastapi_middleware_example.py`.

---

## Integration

### Option A: Use the included FastAPI router

Mount the router as above. The app will:

- Use **MongoDB** if `CREDIT_MONGO_URI` (and optionally `CREDIT_MONGO_DB`) are set.
- Otherwise use **in-memory** storage (no DB required).

### Option B: Use the services directly (any framework)

Instantiate a DB manager, ledger, optional cache/queue, then the services:

```python
from pathlib import Path
from credit_management.db.memory import InMemoryDBManager
# or: from credit_management.db.mongo import MongoDBManager
from credit_management.logging.ledger_logger import LedgerLogger
from credit_management.services.credit_service import CreditService
from credit_management.services.subscription_service import SubscriptionService

# Pick your backend
db = InMemoryDBManager()
# db = MongoDBManager.from_client_uri("mongodb://localhost:27017", "credit_management")

ledger = LedgerLogger(db=db, file_path=Path("logs/credit_ledger.jsonl"))
credit_svc = CreditService(db=db, ledger=ledger)
sub_svc = SubscriptionService(db=db, ledger=ledger)

# Use in your app (e.g. Celery, Django, Flask, another FastAPI app)
await credit_svc.add_credits("user-1", 100, description="Sign-up bonus")
balance = await credit_svc.get_user_credits_info("user-1")
```

You can pass an optional **cache** (`AsyncCacheBackend`) and, for notifications, a **queue** (`AsyncNotificationQueue`) to the relevant services for better performance and decoupled alerts.

### Option C: Swap the database via environment

| Environment variable   | Purpose |
|------------------------|--------|
| `CREDIT_MONGO_URI`     | MongoDB connection string (e.g. `mongodb://localhost:27017`). If set and `motor` is installed, the default API uses MongoDB. |
| `CREDIT_MONGO_DB`      | Database name (default: `credit_management`). |

Leave `CREDIT_MONGO_URI` unset to use in-memory storage.

---

## How to Test

### Run unit tests (pytest + asyncio)

From the **app** directory (so `credit_management` resolves):

```bash
cd /path/to/your/app
pip install pytest pytest-asyncio
pytest app/credit_management/tests/ -v
```

Tests use the in-memory DB and cache; no MongoDB or external services required.

### Example test (add & deduct)

```python
import pytest
from credit_management.db.memory import InMemoryDBManager
from credit_management.logging.ledger_logger import LedgerLogger
from credit_management.services.credit_service import CreditService

@pytest.mark.asyncio
async def test_add_and_deduct_credits(tmp_path):
    db = InMemoryDBManager()
    ledger = LedgerLogger(db=db, file_path=tmp_path / "ledger.log")
    service = CreditService(db=db, ledger=ledger)
    await service.add_credits("user-1", 100)
    assert await service.get_user_credits_info("user-1").available == 100
    await service.deduct_credits("user-1", 40)
    assert await service.get_user_credits_info("user-1").available == 60
```

---

## Schema generation (one-time)

Generate SQL or NoSQL schema from the Pydantic models (e.g. for migrations or collection validators):

```bash
# From repo root, with app on PYTHONPATH
python -m credit_management.schema_generator --backend sql --dialect postgres
python -m credit_management.schema_generator --backend nosql
```

Add a new field to a model → run the generator again to update DDL/validators.

---
More Example: <
[src/examples/](src/examples/)  ||
[PypiReadMe.md](PypiReadMe.md) >


---

## Project layout

```
credit_management/
├── README.md                 # This file
├── __init__.py
├── schema_generator.py       # CLI: generate SQL/NoSQL schema from models
├── api/
│   └── router.py             # FastAPI router (optional)
├── cache/
│   ├── base.py               # AsyncCacheBackend
│   └── memory.py             # In-memory cache
├── db/
│   ├── base.py               # BaseDBManager interface
│   ├── memory.py             # In-memory implementation
│   └── mongo.py              # MongoDB (Motor) implementation
├── logging/
│   └── ledger_logger.py      # Ledger file + DB
├── models/                   # Pydantic models (POJOs + db_schema)
│   ├── base.py               # DBSerializableModel
│   ├── transaction.py
│   ├── user.py
│   ├── subscription.py
│   ├── credits.py
│   ├── notification.py
│   └── ledger.py
├── notifications/
│   └── queue.py              # AsyncNotificationQueue + in-memory impl
├── services/
│   ├── credit_service.py
│   ├── subscription_service.py
│   ├── expiration_service.py
│   └── notification_service.py
└── tests/
    └── test_credit_service.py
```

---

## Design highlights

- **Database-agnostic** — Implement `BaseDBManager` for your store (SQL/NoSQL); services and API stay unchanged.
- **Transaction-oriented** — Every credit change is a stored transaction; balance is derived or cached for speed.
- **Ledger** — Operations and errors are logged to the DB and to a structured JSON log for debugging and monitoring.
- **Extensible schema** — Pydantic models define both API/domain and logical schema; the generator produces SQL/NoSQL artifacts once.

---

## Updates & roadmap

- **Current:** In-memory and MongoDB backends, FastAPI router, credit/subscription/expiration/notification services, ledger, schema generator, pytest example.
- **Possible next:** PostgreSQL/MySQL backend, Redis cache/queue adapters, more API endpoints (history, reservations, plan list), OpenAPI tags and examples.

---

## License & contribution

This project is open source. Use it as a library or as a reference to build your own credit system. If you extend it (new backends, endpoints, or features), consider contributing back or sharing your use case.

---

**Summary:** Add the router or services to your stack, set `CREDIT_MONGO_URI` if you want MongoDB, and you get a full credit and subscription system with ledger, expiration, and notifications — ready to integrate and test.
