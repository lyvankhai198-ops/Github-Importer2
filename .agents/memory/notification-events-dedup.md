---
name: Product notification dedup ledger
description: How the bot decides new-product vs restock announcements and prevents duplicate sends
---

`notification_events` table (event_key unique, claimed via insert + catch IntegrityError) is the single source of truth for "has this product ever been announced" and "was this exact stock total already announced". Never rely purely on stateless before/after comparison for cross-call dedup — a persisted claim survives process restarts and concurrent triggers.

**Rule:** a product's very first stock-positive event (regardless of trigger — admin creation with stock already present, manual inventory import, or an API sync bringing it from 0 to positive) is announced as "new product" (`event_key = new_product:{product_id}`). Every subsequent stock increase is "restock" (`event_key = restock:{product_id}:{current_stock}`). A product created with 0 stock sends nothing immediately — it waits silently until its first real stock event, which then naturally becomes the "new product" announcement.

**Why:** avoids the double-notification bug where a stock-gated product created at 0 stock got an immediate (broken) "new product" message with a dead buy button, followed by a second "restock" message moments later when stock was actually added. Also lets one unified entry point (`notify_product_stock_event` in `services/broadcast_service.py`) serve every stock-increase call site without each one needing to reason about new-vs-restock itself.

**How to apply:** any new code path that increases a product's stock should call `notify_product_stock_event(product_id, previous_stock, current_stock, source_id=None)` rather than composing its own broadcast — it already handles the new/restock decision, dedup claim, settings-toggle gating, and per-user language rendering (via `bot/i18n.py` keys `notify_*`). `notify_restock_broadcast(product_id, added_qty, new_total)` still exists as a backward-compatible thin wrapper for older call sites.
