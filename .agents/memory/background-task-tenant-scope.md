---
name: Background task tenant scope for process_paid_order
description: Background asyncio tasks have no HTTP request context so the tenant filter defaults to owner scope — tenant orders are silently invisible without skip_tenant_filter.
---

## Rule

**Any code that looks up a TenantScopedMixin row by a known id without an HTTP request context must use `skip_tenant_filter=True`, then call `set_current_tenant(order.tenant_id)` for subsequent queries.**

This applies to:
1. Background asyncio tasks (crypto monitors, `process_paid_order`, etc.)
2. Webhook endpoints that receive data for ALL tenants (SePay, payment webhooks)
3. FastAPI BackgroundTask helpers spawned from a webhook request

## Why: two separate root causes fixed

**Background tasks** (`process_paid_order`): opens its own `SessionLocal()` with no HTTP request context. `get_current_tenant()` falls back to `get_owner_tenant_id()`. Tenant filter injects `WHERE tenant_id == owner_id`, silently hiding non-owner tenant orders → `order` is `None` → early return → customer never delivered, ví chợ debited, admin must manually fulfill.

**Webhook handler** (`process_webhook_transaction` + background helpers): SePay delivers all bank-transfer webhooks to a **single shared endpoint** backed by the owner's bank account. The HTTP request has no admin session cookie → ambient tenant = owner → tenant orders invisible in the order-by-payment-code lookup → webhook returns "unmatched" → `process_paid_order` never called → payment confirmed but nothing delivered. Same problem hit `PaymentTransaction` dedup check, `WalletDeposit` reference-code matching, and all three background notification helpers (`_bg_notify_partial`, `_bg_notify_payment_received`, `_bg_notify_late_payment`).

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

## Ví chợ ownership redesign (applied together with the above)

**Old (wrong):** tenant pre-funds their own ví chợ; tenant's balance gated stock; tenant's wallet debited after sale.

**New (correct):** OWNER pre-funds their ví chợ (tracks their supplier API budget). `is_gated_by_market_wallet` detects `shared_from_admin=True` ProductSource (not tenant's is_owner flag). `get_virtual_stock` reads OWNER's `market_wallet_balance`. `_debit_market_wallet_for_sale` debits OWNER's wallet for shared-catalog sales (fee=0, cost only). Tenant has no ví chợ in this model — they collect payment from their customer; admin earns margin.
