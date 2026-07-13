---
name: Customer wallet ledger & atomicity design
description: How wallet_vnd/wallet_usdt balance mutations stay race-safe and decimal-accurate; read before touching wallet_service.py or anything that credits/debits a user's wallet.
---

Wallet balances (`User.wallet_vnd` / `User.wallet_usdt`) are separate from the
legacy imported `User.balance` column — no auto-merge between them, both are
shown in the admin UI. Wallet mutations never touch `balance`.

All credit/debit goes through `services/wallet_service.py`'s `credit_wallet()`
/ `debit_wallet()`, which:
- Lock the user's row with a raw `engine.raw_connection()` + `BEGIN IMMEDIATE`
  (same pattern as `inventory_service._reserve_items_for_order`) before
  read-modify-write, so concurrent debits/credits on the same user can't race
  into an incorrect or negative balance.
- Do arithmetic in Python `Decimal`, quantized per currency (VND: 0 decimal
  places, USDT: 2) before writing back to the `Float` DB column — storage
  stays `Float` (matches the rest of the schema), but every step in between
  is Decimal-safe.
- Always write an immutable `WalletTransaction` ledger row alongside the
  balance change (`balance_before`/`balance_after` snapshot, no
  recomputation needed for history views).

**Why:** two concurrent wallet-affecting events (e.g. a purchase debit and an
admin adjustment, or two paid orders) hitting the same user must never
double-spend or double-credit, and repeated float rounding must never drift
a balance over many transactions.

**How to apply:** any new code path that changes a wallet balance (deposits,
purchases, refunds, admin adjustments, future features) must call
`credit_wallet`/`debit_wallet` — never write `user.wallet_vnd`/`wallet_usdt`
directly. Order fulfillment-failure paths (`payment_service._notify_paid_api_failed`,
`_notify_paid_waiting_stock`, `inventory_service._notify_inventory_waiting_stock`,
`_notify_inventory_delivery_failed`) call `wallet_service.refund_order_to_wallet`,
gated by `Order.refunded_to_wallet` to prevent double-refunding a wallet-paid order.
