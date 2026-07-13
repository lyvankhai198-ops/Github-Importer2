---
name: Manual-edit-safe field sync
description: How to protect admin-edited fields from being silently overwritten by an automatic upstream sync, without a separate "was edited" checkbox.
---

When a resource is populated both by an automatic external sync and by manual admin edits on a subset of the same fields, don't rely on "was this form submitted" as the signal for "this field is now manually owned" — every form submission resends every field's current value, so that would freeze everything permanently on the first save.

**Why:** In this project (AI Center bot), the product edit form always POSTs the full set of visible fields. Treating any POST as a manual edit would make every product immutable to sync the moment an admin opened the edit modal once, even for fields they never touched.

**How to apply:** On save, diff each tracked field's submitted value against the currently stored value. Only add the field name to a frozen-fields set (e.g. a comma-separated column) when the value actually changed. The sync job then skips any field whose name is in that set, and applies all others normally. This keeps untouched fields syncing forever while permanently protecting only what was actually edited.
