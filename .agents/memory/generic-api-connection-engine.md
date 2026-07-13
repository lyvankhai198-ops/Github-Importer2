---
name: Generic API connection engine
description: How the AI Center bot manager's supplier API integration is architected as one config-driven engine instead of per-supplier adapters.
---

`integrations/generic/` is the single engine (url_builder, auth_builder, template_renderer,
json_path, product_mapper, order_mapper, presets, client) that drives every ApiConnection's
test/sync/order/balance calls purely from config columns on the ApiConnection row.
`integrations/manager.py` always constructs `GenericAdapter` — no supplier branching.
CanBoSo/Zampto/Custom are just `presets.py` dicts that pre-fill the config; there is no
hardcoded per-supplier logic in the live request path anymore.

**Why:** the user explicitly required that adding any new supplier API be possible purely
through web-UI config, with zero new code — a hardcoded per-adapter architecture (the
project's original CanBoSo/Zampto adapters) cannot satisfy that.

**How to apply:** when asked to add a new supplier or change request/response behavior for
an existing one, look first at whether it's expressible via the existing config columns
(endpoints, auth_type, query/body templates, response JSON-paths, product/order mapping) —
do not write new adapter code. The legacy adapter files (`integrations/canboso.py`,
`zampto.py`, `custom.py`) still exist only because older tests import them directly; they are
dead code on the live path and should not be extended.
