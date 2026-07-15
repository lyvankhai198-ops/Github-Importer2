---
name: Shared-catalog cross-tenant resolution pattern
description: How "Kho hàng chung" lets tenants list products from the owner's API connections without their own API key, and the general fix for tenant-filter breaking relationship traversal across tenants.
---

Feature: `ApiConnection.is_shared_with_tenants` (owner toggle) + `ProductSource.shared_from_admin`
(set when a tenant "treo chợ" from the shared catalog) let a tenant's `ProductSource.api_product_id`
point at an `ApiProduct` owned by the OWNER's tenant, instead of every tenant needing their own
`ApiConnection`/API key for the same supplier. No new bridge table — reuses the existing
Product/ProductSource/ApiProduct/ApiConnection schema. See `services/shared_catalog.py`.

**Why this needed a dedicated helper module:** the global `do_orm_execute` tenant filter
(`tenancy.py`) auto-injects a `tenant_id == current_tenant` clause into every SELECT against a
`TenantScopedMixin` row, including *lazy relationship loads* (`source.api_product`,
`api_product.connection`, `source.product`). Once a `ProductSource` can legitimately point across
tenants, those relationship accesses silently return `None` the moment the target row belongs to a
different tenant than whichever tenant context is currently active.

**The fix, generalized:** whenever a row already holds a trusted foreign key to another
`TenantScopedMixin` row (obtained via a normal, properly-filtered query), it's safe to resolve that
FK with `db.query(Target).execution_options(skip_tenant_filter=True).filter(Target.id == known_id)`
instead of the plain relationship attribute — the caller already had legitimate access to that
pointer, so bypassing the filter here doesn't leak anything new. Used for: `resolve_api_product`,
`resolve_api_connection`, `resolve_product` in `services/shared_catalog.py`, and reused across
`services/order_service.py`, `services/payment_service.py`, `services/product_service.py`,
`services/api_service.py`, `bot/handlers.py` wherever a `ProductSource` might be cross-tenant.

**Sync-tick propagation gotcha:** the owner's background API-sync scheduler runs inside
`tenant_scope(owner_tenant_id)` for the whole process lifetime (see `main.py` lifespan). Any query in
`services/api_service.py` that looks up `ProductSource` rows by `ApiConnection`/`ApiProduct` id must
add `.execution_options(skip_tenant_filter=True)` too, or it silently only finds the owner's own
rows and never propagates price/stock to other tenants' shared listings.

**Scope decision:** shared listings skip the full margin-preserving/approval-gated price pipeline
(`services/price_sync_service.handle_source_price_change`) — that pipeline evaluates the *product's
own tenant's* guard-rail settings, which is meaningless to run under the owner's tenant scope during
a sync tick. Shared products just get `Product.source_price` kept in sync directly; the tenant sets
their own `sale_price` by hand. A tenant's `sale_price` is NOT auto-adjusted when the owner's cost
changes for a shared product — flagged as a known gap, not silently "handled".

**Ví chợ wallet gating already covers this for free:** `services/market_stock_service.is_gated_by_market_wallet`
gates on "any `source_type=api` product owned by a non-owner tenant" — not specifically on
`shared_from_admin` — so shared-catalog attachments are correctly wallet-gated with zero extra code.
