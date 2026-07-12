---
name: VND price formatting
description: How Vietnamese-style integer prices with dot thousands separators are produced across templates and bot messages, and why Python's default `,` format specifier is wrong here.
---

Python's `"{:,.0f}".format(x)` (and f-string `{x:,.0f}`) always uses a **comma** as
the thousands separator, regardless of any Vietnamese context — there is no
locale switch happening. Vietnamese convention uses a **dot** ("5.000đ"), so
every one of these call sites needs an explicit `.replace(",", ".")`.

**Why:** this bug shipped silently across the whole app (dashboard, orders,
products, bot messages) because each call site formatted the number "correctly"
in a generic sense — nothing crashed, it just displayed comma-separated numbers
to Vietnamese users. Only caught via manual/QA review of the rendered price.

**How to apply:**
- Backend/bot Python (f-strings, `bot/*.py`, `services/*.py`): use
  `format_vnd(value)` from `services/normalize.py` — returns just the digits
  with dot separators (no currency suffix); callers append `"đ"` themselves.
- Jinja templates: don't add a filter registration per-router; instead keep the
  inline pattern `"{:,.0f}".format(x).replace(",", ".")` consistent with the
  rest of the codebase (each router has its own `Jinja2Templates` instance, so
  there's no single shared filter setup point without a broader refactor).
- Any *new* VND-formatting call site (template or Python) must follow one of
  the two patterns above — never reintroduce a bare `{:,.0f}`.
