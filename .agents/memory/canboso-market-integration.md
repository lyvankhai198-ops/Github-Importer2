---
name: CanBoSo Market supplier integration
description: How the account-vs-slot API item distinction and per-order buyer email were added to the generic supplier adapter pattern.
---

- New suppliers with a "type" concept different from the existing account model get a new nullable column on `ApiProduct` (e.g. `external_item_type`), not a change to `Product` — the type belongs to the source item, not the local catalog entry. Other adapters leave it `None` and existing behavior is unchanged.
- When a purchase can't complete instantly (e.g. a "slot" item needs manual seller fulfillment), give it its own terminal-ish `OrderStatus` rather than reusing `pending_manual`/`paid_waiting_stock` — those mean something different and reusing them breaks admin filtering/notifications.
- `services/payment_service.py::process_paid_order` is the real purchase entry point (not `services/order_service.py::create_order`, which is a separate/legacy path). It's already idempotent via order.status gating + an in-memory processing-key set — new supplier branches don't need their own dedupe logic as long as they land on a non-retryable status after first success.
- Suppliers requiring a buyer email with no email-collection UX in the bot: pass a deterministic synthetic email derived from `telegram_user_id` (e.g. `tguser<id>@aicenter-orders.local`) into `adapter.buy_product(..., buyer_email=...)`. Adapters that don't need it just ignore the kwarg.
