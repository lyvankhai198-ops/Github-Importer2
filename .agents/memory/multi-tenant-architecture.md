---
name: Multi-tenant architecture (AI Center bot)
description: How tenant isolation is implemented — AdminUser-as-tenant, session-level auto-filter, fail-safe defaults. Read before touching models.py, tenancy.py, or any router/worker that queries tenant-scoped data.
---

## Core decision: AdminUser IS the tenant
There is no separate `Tenant` table. `AdminUser.id` is used as `tenant_id` everywhere. The first-ever admin account is `is_owner=True`; only the owner can create/manage other tenant accounts (via `/tenants`, owner-only).

**Why:** avoids a parallel identity system when admin accounts already 1:1 map to "a shop".

## Auto-filtering via SQLAlchemy session events, not per-file changes
`tenancy.py` registers two global SQLAlchemy events at import time (must be imported at module load, not deferred — see below):
- `do_orm_execute` — injects `with_loader_criteria(TenantScopedMixin, lambda cls: cls.tenant_id == tenant_id)` into every SELECT automatically.
- `before_flush` — auto-stamps `tenant_id` on new rows.

A contextvar (`tenancy._current_tenant_id`) holds "current tenant" per request/task. This lets ~24 existing routers/services keep using plain `db.query(Model)` with zero changes, as long as the tenant contextvar is set before they run.

**Why:** retrofitting every router/service individually for a schema this size was infeasible in one pass; a session-level filter is transparent to existing code.

**How to apply:** any NEW model that holds per-shop data must inherit `TenantScopedMixin` (models.py). `tenant_id` is declared via `@declared_attr` **on the mixin itself**, not as a per-class explicit Column — `with_loader_criteria(TenantScopedMixin, ...)` needs `TenantScopedMixin.tenant_id` to resolve directly during its lambda-analysis step; a plain Column added only on the subclass causes `AttributeError: type object 'TenantScopedMixin' has no attribute 'tenant_id'` at query time.

## Fail-safe default: unscoped → owner, never unfiltered
If the contextvar isn't set (background worker forgot, webhook, etc.), `get_current_tenant()` falls back to the **owner's** tenant id (cached `get_owner_tenant_id()`), never `None`/no-filter. Prevents accidental cross-tenant leakage from a forgotten scope.

**Gotcha:** `get_owner_tenant_id()` itself queries `AdminUser` — that query must use `.execution_options(skip_tenant_filter=True)`, or it recurses infinitely (the fallback path re-triggers the same `do_orm_execute` event before the cache is populated).

## Where tenant context gets set
- **HTTP requests:** a single `@app.middleware("http")` function in main.py reads `request.session.get("admin_id")` and sets the contextvar — chosen over `Depends(require_admin)` because not all routers use the shared dependency (several use a local `check_auth(request)` helper instead). Must be added to the middleware stack *before* `SessionMiddleware` (added later = outer layer = runs first) so `request.session` is already parsed. Also enforces rental auto-expiry here (`AdminUser.expires_at` past → flip `is_active=False`, clear session) rather than a scheduled job.
- **Background workers / bot polling / lifespan startup:** no request to derive a tenant from. `asyncio.create_task()` captures the current `contextvars.Context` at creation time and the task runs inside a copy of it for its whole lifetime — so wrapping the *creation* of all background tasks in `with tenancy.tenant_scope(owner_tenant_id):` scopes them permanently, without needing to touch the loop bodies themselves.

## SQLite legacy UNIQUE constraints under multi-tenancy
Any column that used to be globally unique (e.g. `Setting.key`, `PaymentMethod.method_code`, `Product.product_code`) must become `(tenant_id, column)` unique instead. SQLite bakes single-column `UNIQUE` into the table's own CREATE TABLE statement as a constraint-backed index — `DROP INDEX` on it always fails ("index associated with UNIQUE or PRIMARY KEY constraint cannot be dropped"). The only fix is the standard rebuild: rename table aside → recreate fresh from the current SQLAlchemy model (no longer `unique=True`) → copy rows via the column names both versions share → drop the old table → add the real composite unique index. Also drop the old table's plain non-constraint indexes (e.g. `ix_products_id`) right after the rename — SQLite keeps those attached under their original name across a rename, which collides with recreating them on the new table.

## Deferred (not yet built)
Per-tenant bot/worker multiplexing (one Telegram bot + one set of crypto/payment workers per rented tenant, payment webhooks routed by tenant) is NOT implemented. Only one bot process runs today, scoped to the owner tenant. Tenant accounts created via `/tenants` get isolated data (products/orders/settings/payment methods/ranks) but do not get their own bot instance yet.
