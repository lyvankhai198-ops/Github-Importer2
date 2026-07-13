---
name: Two distinct restock-notify audiences
description: paid_waiting_stock orders vs. a browsing "notify me" opt-in list are different user sets that need different restock notification paths.
---

When a product runs out of stock, two different groups of users may care about a restock, and they must not be conflated into one mechanism:

1. Buyers who already paid but the order couldn't be fulfilled (`Order.status == paid_waiting_stock`) — these are targeted automatically via the existing `notify_users_when_restocked` global toggle (see `services/inventory_service.notify_restock_if_enabled`), because they have a real pending order to resume.
2. Shoppers who were just browsing, saw "out of stock", and explicitly opted in to be pinged (no order exists yet) — this needs its own opt-in list (`RestockSubscription` model, subscribed via a bot button on the out-of-stock screen) and its own explicit, admin-triggered notify action (the "Notify users" checkbox on add-stock), independent of the global toggle.

**Why:** Conflating them either spams paying customers with a generic broadcast-style message instead of resuming their order, or never reaches browsers who never placed an order at all (since `paid_waiting_stock` queries return nothing for them).

**How to apply:** When adding a restock/back-in-stock notification, first identify which of these two audiences the trigger is for, and reuse the matching existing path rather than inventing a third parallel one.
