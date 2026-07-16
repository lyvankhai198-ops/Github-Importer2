---
name: Slot-vs-account supplier item infra (incl. CanBoSo Market)
description: Shared item_type/pending_seller_fulfillment/synthetic-email infra that any supplier adapter can opt into; CanBoSo Market was rebuilt 2026-07-15 against the real confirmed Public Market API schema.
---

**2026-07-15: CanBoSo Market was removed then rebuilt same-day** — first deleted ahead of a planned redesign, then rebuilt as a fresh `integrations/canboso.py` (`CanBosoAdapter`, `ApiType.canboso_market`) once the user supplied real Swagger docs for `https://canboso.com/api/public/market`. Confirmed real contract (do not re-guess field names if touching this again):
- Auth: header `X-API-Key` only, no query-param key.
- `GET /products` (page, limit≤100, search, sort, slotProductType, seller, emoji) → `{"data": [MarketProduct...]}`.
- `MarketProduct` fields: `_id`, `productName`, `emoji`, `slotProductType`/`isSlotProduct`, `marketSalePrice`, `marketMinListingPrice`, `sellerDisplayName`, `stats: {total, sold, available}`.
- `POST /products/{id}/buy` body `{quantity, email}`. Account items return a **flat** `BuyItemResponse {user, password, verifyEmail, expiryText, otherInfo}` with no wrapper — the adapter must wrap it as `{"accounts": [...]}` before returning, since the shared delivery-item normalizer expects that key. Slot items return an order object (`status: paid`, `items: null`) for the seller to fulfill by hand.
- No documented balance or order-listing endpoint — adapter returns explicit "not supported" for those rather than fabricating data.

The generic fields below were never removed — AI Center Buyer also sets them — so any CanBoSo-like design should reuse this infra rather than re-inventing it.

- New suppliers with a "type" concept different from the existing account model get a new nullable column on `ApiProduct` (e.g. `external_item_type`), not a change to `Product` — the type belongs to the source item, not the local catalog entry. Other adapters leave it `None` and existing behavior is unchanged.
- When a purchase can't complete instantly (e.g. a "slot" item needs manual seller fulfillment), give it its own terminal-ish `OrderStatus` rather than reusing `pending_manual`/`paid_waiting_stock` — those mean something different and reusing them breaks admin filtering/notifications.
- `services/payment_service.py::process_paid_order` is the real purchase entry point (not `services/order_service.py::create_order`, which is a separate/legacy path). It's already idempotent via order.status gating + an in-memory processing-key set — new supplier branches don't need their own dedupe logic as long as they land on a non-retryable status after first success.
- Suppliers requiring a buyer email with no email-collection UX in the bot: pass a deterministic synthetic email derived from `telegram_user_id` (e.g. `tguser<id>@aicenter-orders.local`) into `adapter.buy_product(..., buyer_email=...)`. Adapters that don't need it just ignore the kwarg.
