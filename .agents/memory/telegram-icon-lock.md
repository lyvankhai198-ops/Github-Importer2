---
name: telegram_icon auto-assign vs. manual lock
description: Product.telegram_icon auto-assigns from name keywords unless admin-locked; clearing the field unlocks it again — don't reuse the generic TRACKED_SYNC_FIELDS/apply_admin_edit machinery for this.
---

`Product.telegram_icon` has different lock semantics than the other admin-editable synced fields (`description`, `image_path`, `warranty`, `duration` in `services/product_sync.py TRACKED_SYNC_FIELDS`): those freeze permanently once touched, but the icon must support "clear to re-enable auto-assignment."

Implementation: `apply_admin_icon_edit(product, new_icon)` — non-blank value adds `"telegram_icon"` to the existing `manually_edited_fields` set (locking it); blank removes it from the set (unlocking). `auto_assign_icon_if_unlocked(product)` fills from `services/normalize.auto_assign_emoji(name)` (keyword map, e.g. "Grok"→🤖, generic terms like api/token/key last, 📦 fallback) only when not in that set. Both are called together at every product create/edit/API-sync point.

**Why:** Reusing the generic tracked-field lock (which never unlocks) would mean an admin who clears the icon field to "reset to auto" would instead freeze it as permanently blank — the opposite of intended behavior.

**How to apply:** Any future per-field "admin override with reset-to-auto" need should follow this same set-membership pattern (add on non-blank, discard on blank) rather than the permanent-freeze `TRACKED_SYNC_FIELDS` pattern.