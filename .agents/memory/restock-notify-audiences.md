---
name: Restock/new-product notification audiences (three distinct paths)
description: paid_waiting_stock orders vs. an explicit waiting-list vs. "all active users" are three different audiences — do not conflate them into one mechanism.
---

Three separate notification concepts exist for stock/product events; adding a new "notify on X" feature almost always means picking (or adding) one of these, not merging them:

1. Buyers who already paid but the order couldn't be fulfilled (`Order.status == paid_waiting_stock`) — targeted automatically via the global toggle (`services/inventory_service.notify_restock_if_enabled`), because they have a real pending order to resume.
2. Shoppers who opted into a waiting list for one product (no order exists) — explicit, admin-triggered ("Notify users" checkbox on add-stock), via `services/restock_notify_service.notify_restock_waiting_list`.
3. Broadcast-style "🆕 new product" / "🔄 restocked" announcements to **all active users**, gated by their own settings toggles (`notify_new_products`, `notify_restock`) and batched (`broadcast_batch_size`/`broadcast_delay_ms`) — `services/broadcast_service.notify_new_product_broadcast` / `notify_restock_broadcast`. Fires on ANY stock increase (not just 0→positive) or a brand-new product, with a "🛒 Mua ngay" button reusing the existing `product:{id}` callback (which re-checks stock live) rather than any new order-creation logic.

**Why:** Conflating them either spams paying customers with a generic message instead of resuming their order, blasts every user for a routine top-up, or never reaches the audience a given trigger is actually meant for.

**How to apply:** When adding a restock/new-product notification, identify which of the three audiences the trigger is for and extend/call the matching existing path — don't build a fourth parallel one.
