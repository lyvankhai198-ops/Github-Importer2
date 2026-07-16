---
name: Shared-catalog product sync must bypass tenant filter
description: Why a non-owner tenant viewing shared-catalog ("Chợ dùng chung") products can see stale/false "hết hàng" even though the source still has stock.
---

`sync_active_supplier_products()` / `sync_api_products()` in `services/api_service.py` refresh `ApiProduct` stock from the live supplier API. Both used to query `ApiConnection` with the default tenant filter active — so when a **non-owner tenant** opens `/products/market`, the query only finds *their own* connections, silently skipping the owner's connection even though its products are shared with them. Result: the owner's `ApiProduct.last_sync_at` goes stale, `get_product_stock_status()`'s 10-minute freshness check trips, and the listed product shows "Hết hàng" for the tenant while the owner's own view (which does trigger the sync) shows correct stock.

**Why:** `TenantScopedMixin` auto-filters every query by the current request's tenant contextvar (see `tenancy.py`) — including here, where the whole point is to refresh data on behalf of *any* tenant looking at a shared listing, not just the owner.

**How to apply:** any background/on-demand sync or refresh routine that must stay correct regardless of which tenant's HTTP request triggered it needs `.execution_options(skip_tenant_filter=True)` on its `ApiConnection`/`ApiProduct` lookups — the same pattern already used in `services/shared_catalog.py`'s `resolve_api_product`/`resolve_product`. Don't assume "runs during a tenant's request" implies "only needs that tenant's own rows."
