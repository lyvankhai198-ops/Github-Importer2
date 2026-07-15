---
name: Slot-vs-account supplier item infra (formerly CanBoSo Market)
description: Shared item_type/pending_seller_fulfillment/synthetic-email infra that any supplier adapter can opt into; CanBoSo Market itself was removed 2026-07-15 for a redesign, but this infra stayed because AI Center Buyer also depends on it.
---

**2026-07-15: the CanBoSo Market adapter, its `ApiType.canboso_market` enum member, `integrations/canboso.py`, and its admin routes/tests were deleted at the user's request ahead of a redesign.** Nothing about a future CanBoSo replacement should assume the old adapter file exists — it needs a fresh `BaseAdapter` subclass per the new-supplier-adapter-pattern memory. The generic fields below were NOT removed — AI Center Buyer still sets them — so a new CanBoSo design can and should reuse this infra rather than re-inventing it.

- New suppliers with a "type" concept different from the existing account model get a new nullable column on `ApiProduct` (e.g. `external_item_type`), not a change to `Product` — the type belongs to the source item, not the local catalog entry. Other adapters leave it `None` and existing behavior is unchanged.
- When a purchase can't complete instantly (e.g. a "slot" item needs manual seller fulfillment), give it its own terminal-ish `OrderStatus` rather than reusing `pending_manual`/`paid_waiting_stock` — those mean something different and reusing them breaks admin filtering/notifications.
- `services/payment_service.py::process_paid_order` is the real purchase entry point (not `services/order_service.py::create_order`, which is a separate/legacy path). It's already idempotent via order.status gating + an in-memory processing-key set — new supplier branches don't need their own dedupe logic as long as they land on a non-retryable status after first success.
- Suppliers requiring a buyer email with no email-collection UX in the bot: pass a deterministic synthetic email derived from `telegram_user_id` (e.g. `tguser<id>@aicenter-orders.local`) into `adapter.buy_product(..., buyer_email=...)`. Adapters that don't need it just ignore the kwarg.
