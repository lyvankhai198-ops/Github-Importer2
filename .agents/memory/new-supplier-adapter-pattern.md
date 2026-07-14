---
name: New supplier API = new adapter class
description: When adding support for a new supplier's API, add a sibling BaseAdapter subclass, never branch inside an existing adapter.
---

When a new supplier needs to be integrated into the generic API connection engine, always add a new `BaseAdapter` subclass (its own file under `integrations/`) and a new branch in `integrations/manager.py`'s `get_adapter`, even if:

- The new supplier's domain/base URL looks similar to or overlaps with an existing integration (e.g. two different suppliers both hosted on the same domain, `canboso.com`, but exposing unrelated API surfaces — one a "market" API, one a "telegram-buyer" API).
- The new supplier's auth mechanism, field names, or response shape are only slightly different from an existing adapter's.

**Why:** Reusing or branching inside an existing adapter (e.g. adding `if api_type == X` logic inside `ZamptoAdapter`) risks silently breaking the existing, working integration — wrong headers, wrong endpoint, wrong field mapping for the original supplier. Suppliers sharing a domain are still functionally unrelated products with independent schemas.

**How to apply:** New adapter gets its own `_headers()`, endpoint paths, and a dedicated `normalize_<supplier>_product()` in `services/normalize.py` if the field-mapping shape differs from existing normalizers. Register the `ApiType` enum value, the UI dropdown option + JS defaults in `templates/api_connections.html`, and any new `ApiProduct` columns needed for supplier-specific metadata (mirrors how CanBoSo Market's `external_item_type`/`external_seller` fields were added — new nullable columns, not repurposed existing ones). Verify old adapters/connections are untouched by testing them after the change (same URLs/behavior as before).
