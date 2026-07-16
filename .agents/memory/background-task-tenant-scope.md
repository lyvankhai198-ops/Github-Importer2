---
name: Background task tenant scope for process_paid_order
description: Background asyncio tasks have no HTTP request context so the tenant filter defaults to owner scope — tenant orders are silently invisible without skip_tenant_filter.
---

## Rule

Any background asyncio task (crypto monitors, payment processor, etc.) that looks up an `Order` (or other TenantScopedMixin row) by a known id **must** use `skip_tenant_filter=True` for that lookup, then call `set_current_tenant(order.tenant_id)` immediately after so all subsequent queries in the same call are scoped to the correct tenant.

## Why

`process_paid_order` opens its own `SessionLocal()` with no HTTP request context. `get_current_tenant()` falls back to `get_owner_tenant_id()`. The tenant filter injects `WHERE tenant_id == owner_id`, which silently excludes a non-owner tenant's order. `order` comes back `None` → early return → customer never delivered, ví chợ debited, admin must manually fulfill.

## How to apply

```python
_tenant_token = None   # initialise BEFORE any early return

order = (
    db.query(Order)
    .execution_options(skip_tenant_filter=True)
    .filter(Order.id == order_id)
    .first()
)
if not order:
    return

if order.tenant_id:
    from tenancy import set_current_tenant
    _tenant_token = set_current_tenant(order.tenant_id)
# ... rest of function uses correct tenant scope ...

# in finally block:
if _tenant_token is not None:
    from tenancy import reset_current_tenant
    reset_current_tenant(_tenant_token)
```

The `bot_manager` proxy also reads the ambient tenant scope, so the correct tenant's bot is used for delivery notifications automatically once `set_current_tenant` is called.

Apply the same pattern to any other background task that looks up tenant-scoped rows by id without an HTTP request (crypto monitors, wallet workers, etc.) if they ever need to process cross-tenant rows.
