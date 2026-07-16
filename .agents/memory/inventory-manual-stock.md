---
name: Local inventory ("kho tài khoản") delivery mode
description: How manual_stock delivery mode reserves/delivers InventoryItem rows safely under concurrency, and how it relates to manual_admin and api_auto.
---

Product `delivery_mode` has three live values: `api_auto` (existing API-sourced
delivery, unchanged), `manual_admin` (no local stock tracked — always shown to
bot users as "accepting orders"/unlimited, admin fulfills by hand), and
`manual_stock` (local `InventoryItem` rows are the source of truth for stock).
Legacy rows with `delivery_mode == "manual"` are treated identically to
`manual_admin` everywhere in app logic (no destructive migration was run to
rename them — a one-time backward-compat `UPDATE` normalizes new writes, but
old rows can still read as `"manual"`).

**Why:** the spec required adding local-inventory-backed products without
breaking any existing product's configured delivery behavior.

**How to apply:**
- Stock for `manual_stock` is always computed live via
  `COUNT(inventory_items WHERE product_id=? AND status='available')` — never
  cached or stored on `Product`. Any code path that needs "is this in stock"
  must call `get_product_stock_status` (branches 3 ways) rather than reading a
  stored counter.
- Reservation/delivery (`services/inventory_service.py::deliver_from_local_inventory`)
  uses a raw SQLite connection with `BEGIN IMMEDIATE` to atomically reserve the
  exact requested quantity of `available` rows before marking them `sold` —
  this is what prevents two concurrent paid orders from being allocated the
  same account. Verified via a concurrent `asyncio.gather` test: two orders
  requesting more combined stock than available never got the same item.
- `process_paid_order`'s idempotency guard (`_processing_paid`) is released
  before delegating to `deliver_from_local_inventory`, which manages its own
  instance of the same guard — don't hold both, they'd self-block.
- Any place gating behavior on `delivery_mode == DeliveryMode.api_auto` (stock
  checks in `bot/handlers.py`, `bot/keyboards.py` list rendering) must also
  cover `manual_stock` the same way; `manual_admin`/legacy `manual` should
  never be blocked on stock.
