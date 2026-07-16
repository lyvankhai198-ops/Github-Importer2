---
name: Language-gate needs its own flag
description: Why "has the user chosen a language yet" can't be derived from a defaulted column
---

A column with a non-null default at the DB level (e.g. `language_code` defaulting to `"vi"`) can never be used to detect "the user hasn't chosen yet" — every row has a truthy value from the moment it's created, so a `if not user.language_code` gate silently never fires. Add a separate explicit boolean (e.g. `language_selected`) that starts `False` and is flipped to `True` only when the user actually makes the choice.

**Why:** found a forced-language-picker feature that looked implemented (checked `language_code`) but could never actually trigger, because the column's DB default meant the "unset" state was unreachable.

**How to apply:** when migrating in a new "first-run gate" flag like this, backfill existing rows to the "already satisfied" value (e.g. `language_selected = 1` for all pre-existing users) so the gate only applies to genuinely new rows going forward, not retroactively to everyone.
