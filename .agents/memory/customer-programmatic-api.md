---
name: Customer programmatic API key design
description: How the "🔗 API" customer key feature (key hashing, idempotency, rate limiting, request logging) is built — reuse this pattern for future API-facing features.
---

- **Key hashing**: keyed HMAC-SHA256 (not bcrypt) for API keys that need fast exact-match DB lookup on every request. Raw key shown once at generation/regen time only; only hash + display prefix persist.
- **client_order_id idempotency on SQLite**: when a table already exists and needs a new unique constraint, add nullable columns via `ALTER TABLE` then enforce uniqueness with a **partial unique index** (`CREATE UNIQUE INDEX ... WHERE col IS NOT NULL`) in the migrations runner — SQLite can't add a table-level `UniqueConstraint` to an existing table any other way.
- **Rate limiting without a new dependency**: counted directly from a request-log table (rows in last 60s / since UTC midnight) rather than adding `slowapi` — fine at this scale and keeps the dependency footprint down.
- **Uniform request logging via ASGI middleware**: a `require_api_client`-style auth dependency sets `request.state.api_client_id` as soon as the key resolves (before locked/revoked/rate-limit checks) so a single middleware can log every outcome (success, 401, 429) uniformly, without per-endpoint logging code.
- **API-originated money-moving orders**: create the order first (pending), then debit the wallet with `extra_updates` flipping `payment_status` to paid in the same atomic transaction guarded by `WHERE payment_status = 'pending'` — same pattern as every other wallet-funded flow (see wallet-ledger-design.md). On insufficient balance, mark the order failed via a plain non-money update and keep the client_order_id claimed so retries return the same failure instead of re-debiting.
