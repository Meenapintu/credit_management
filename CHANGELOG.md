# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.7.0] - 2026-04-12

### Added
- **Razorpay Payment Provider** — Full integration with Razorpay payment links for credit top-up
- **`PaymentProvider` abstract base class** — Extensible provider architecture (Stripe-ready)
- **`PaymentService`** — Centralized payment processing with atomic credit updates
- **`update_payment_record_atomic()`** — MongoDB conditional update prevents double-crediting on concurrent webhooks
- **Reference ID tracking** — Uses host provider reference_id as unique identifier across all webhook events
- **Webhook signature verification** — HMAC-SHA256 verification for Razorpay (Stripe-ready)
- **Payment history endpoints** — View payment records and status via `/credits/payments/*`
- **Promo code system** — Promos with usage limits, expiry dates, and claim tracking
- **`_CREDIT_EVENTS` whitelist** — Only whitelisted events can add credits, preventing duplicate processing

### Changed
- Renamed `add_credits_atomic` to `update_payment_record_atomic` for clarity
- Added provider IDs (`provider_payment_id`, `provider_order_id`) to `PaymentRecord` model
- Webhook processing now skips events with no matching record (prevents duplicates)
- State machine enforcement via `_is_forward()` ensures payment status only moves forward

### Fixed
- Race condition where multiple webhook events for the same payment could add credits multiple times
- Payment lookup now tries reference_id first for consistent matching across different event types

---

## [0.6.0] - 2026-04-08

### Added
- **Credit management with reservation pattern** — Reserve credits before API calls, deduct actual usage after
- **`CreditDeductionMiddleware`** — Automatic credit reservation/deduction middleware for FastAPI
- **Context variable tracking** — Request-scoped LLM usage tracking via Python `contextvars`
- **Dual-write ledger** — Database + append-only JSONL file for audit trails
- **In-memory backend** — Perfect for testing without a database
- **MongoDB backend** — Production-ready async MongoDB implementation via motor
- **Cache system** — 5-minute TTL cache with delta-based updates
- **Notification system** — Low credits, expiry warnings, transaction errors
- **Subscription plans** — Daily, monthly, yearly plans with auto-renew
- **Schema generator** — CLI tool to generate SQL DDL or MongoDB validators

---

## [0.5.0] - 2026-03-25

### Added
- Initial credit management system
- `CreditService` with add, deduct, reserve, expire methods
- `BaseDBManager` abstract interface for database-agnostic design
- `MongoDBManager` implementation using motor async driver
- `InMemoryDBManager` for testing
- `DBSerializableModel` base class for all data models
- Transaction ledger with correlation IDs
- Credit expiry records with expiration tracking
- Subscription plan management with billing periods
- User subscription assignment with auto-renew
- Notification event queue system
- Pydantic v2 models for all entities
- FastAPI router with frontend, backend, and webhook endpoints
- Schema generator for SQL and NoSQL backends
