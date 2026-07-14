---
name: Rank/tier spend aggregation
description: How to compute a user's "total spend" reliably across all payment paths in the AI Center bot, for any feature that needs it (membership ranks, spend-based promos, leaderboards, etc.)
---

`User.total_orders`/`User.total_spent` are only incremented in one place
(`services/order_service.py::create_order`, the legacy non-payment-gated
instant-order path). The SePay/crypto/Binance/wallet payment-gated flows —
which is most real traffic — never touch those counters. They all converge
on `services/payment_service.py::process_paid_order`, which is the shared,
idempotent completion point for every gated method.

**Why:** Trusting `User.total_spent` for anything spend-threshold-based
(ranks, promos, tax reporting) will silently undercount most users. It was
never wired up for the payment-gated paths — not a bug introduced by a
tracked feature, just a pre-existing gap in the counter.

**How to apply:** Compute total spend live from the `orders` table instead of
reading the counter: `SUM(total_price)` where `payment_status IN (paid,
overpaid)` OR (`payment_status IS NULL AND status IN (completed,
partial_delivery)`) — the second clause covers the legacy instant-create
path where payment_status is never set. This is implemented in
`services/rank_service.py::compute_total_spent` /
`compute_total_accounts_purchased`. For any new feature needing a
"successful order" signal, hook `process_paid_order`'s gate-passed point
(fires once per order, guarded by the existing `_processing_paid` set) for
gated flows, plus `create_order`'s completion branch for the instant path —
don't add a new hook per payment method.
