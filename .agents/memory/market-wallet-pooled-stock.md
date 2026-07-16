---
name: Ví chợ virtual stock is pooled, not split
description: get_virtual_stock in market_stock_service.py checks a chợ-sourced product's unit count against the tenant's FULL market_wallet_balance, never balance divided by number of attached products.
---

Rule: `get_virtual_stock(db, product)` = `floor(admin.market_wallet_balance / product.source_price)`, computed against the whole current balance — never pre-divided by `n_attached` (number of active source_type=api products the tenant has listed).

**Why:** the original spec formula divided the balance evenly across every attached product (`budget_per_product = balance / n_attached`). That produced false "Hết hàng" as soon as a tenant listed 2+ chợ-sourced products: e.g. wallet=100.000đ with 2 products meant each only "saw" 50.000đ, even if a single product's real cost was covered by the full balance. The real debit only ever happens once, atomically, at order fulfillment time (`debit_for_sale` in market_wallet_service.py, guarded by `orders.market_wallet_debited`), so there's no risk of double-spending the same balance across two simultaneous sales — pre-partitioning it during display was unnecessary and wrong.

**How to apply:** if asked to reintroduce any kind of "reserve/split the wallet across listings" behavior, push back — it reintroduces this exact bug. Any new logic that needs to know "can this specific product still be sold" should check the live pooled balance directly, not a per-product allocation. `get_attached_market_product_count` was removed since nothing needs it anymore.

The per-unit cost used for the displayed count must also include the platform fee percent (`services/market_pricing.get_platform_fee_percent`), not just `source_price` — `debit_for_sale` debits cost+fee together as one atomic transaction, so ignoring the fee in the display formula overstates available units and the last "in stock" unit can fail at actual checkout with InsufficientBalanceError.
