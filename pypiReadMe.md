<h1 align="center">
 Credit Management — Plug-and-Play Credits & Subscriptions
 </h1>

<p align="center">
  <img alt="Static Badge" src="https://img.shields.io/badge/PRs-welcome-brightgreen?style=for-the-badge">
<img alt="Static Badge" src="https://img.shields.io/badge/python-3670A0?style=for-the-badge">
</p>


**Production-ready, database-agnostic credit and subscription management for any Python service or API.**

Manage user credits, subscriptions, expirations, reservations, and notifications with a single, pluggable module. No lock-in: use **in-memory** for development, **MongoDB** for scale, or plug in your own SQL/NoSQL backend.

---

## Installation

Install the package from PyPI:

```bash
pip install Credit-Management
```

Depending on your use case, you might need to install extra dependencies:
- If you are using the FastAPI router, install `fastapi`.
- If you are using the MongoDB backend, install `motor`.

```bash
pip install fastapi motor
```

---

## Quick Start

You can use the services directly in any Python application (e.g., FastAPI, Django, Flask, Celery).

Here's a simple example of how to add credits to a user and check their balance:

```python
import asyncio
from pathlib import Path
from credit_management.db.memory import InMemoryDBManager
from credit_management.logging.ledger_logger import LedgerLogger
from credit_management.services.credit_service import CreditService

async def main():
    # 1. Initialize the components
    db = InMemoryDBManager()
    ledger = LedgerLogger(db=db, file_path=Path("logs/credit_ledger.jsonl"))
    credit_svc = CreditService(db=db, ledger=ledger)

    # 2. Use the service
    await credit_svc.add_credits("user-1", 100, description="Sign-up bonus")
    balance = await credit_svc.get_user_credits_info("user-1")
    print(f"User-1 balance: {balance}")

    await credit_svc.deduct_credits("user-1", 30, description="Used for service X")
    balance = await credit_svc.get_user_credits_info("user-1")
    print(f"User-1 balance after deduction: {balance}")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## FastAPI Integration

This package comes with a pre-built FastAPI router for quick integration.

### 1. Mount the API

```python
from fastapi import FastAPI
from credit_management.api.router import router as credit_router

app = FastAPI()

# This will expose endpoints like /credits/add, /credits/deduct, etc.
app.include_router(credit_router, prefix="/credits")
```

### 2. Use the HTTP API

Once mounted, you can interact with the credit management system via HTTP requests:

```bash
# Add credits to a user
curl -X POST http://localhost:8000/credits/add 
  -H "Content-Type: application/json" 
  -d '{"user_id": "user-1", "amount": 100, "description": "Welcome bonus"}'

# Get user balance
curl http://localhost:8000/credits/balance/user-1

# Deduct credits from a user
curl -X POST http://localhost:8000/credits/deduct 
  -H "Content-Type: application/json" 
  -d '{"user_id": "user-1", "amount": 30}'
```

### Automatic Credit Deduction Middleware

The package also includes a FastAPI middleware for automatic credit deduction based on API usage.
This is useful for billing based on API calls or resource consumption.

For a full example, see the `examples/fastapi_middleware_example.py` in the project repository.

---

## Configuration

The credit management system can be configured to use different backends for data storage.

### In-Memory (Default)

By default, the system uses an in-memory database, which is perfect for development, testing, or simple use cases. No configuration is needed.

### MongoDB

For production environments, you can use MongoDB as the backend. To enable it, set the following environment variables:

| Environment Variable  | Purpose                                                       |
| --------------------- | ------------------------------------------------------------- |
| `CREDIT_MONGO_URI`    | MongoDB connection string (e.g., `mongodb://localhost:27017`) |
| `CREDIT_MONGO_DB`     | The name of the database to use (default: `credit_management`) |

If `CREDIT_MONGO_URI` is set, the system will automatically use the MongoDB backend. Make sure you have the `motor` library installed.

---

## Features

- **Credit Operations**: Add, deduct, expire, and reserve credits.
- **Subscriptions**: Create and manage subscription plans with different billing periods.
- **Expiration**: Automatically handle credit expiration.
- **Notifications**: A notification system for low credit and expiring credits.
- **Ledger**: A complete audit trail of all credit transactions.
- **Pluggable Backends**: Switch between in-memory and MongoDB backends.
- **Framework Agnostic**: Use it with any Python framework.

---

## Contributing

Contributions are welcome! Please feel free to open an issue or submit a pull request on the [GitHub repository](https://github.com/Meenapintu/credit_management).
