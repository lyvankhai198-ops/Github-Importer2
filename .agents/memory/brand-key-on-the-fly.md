---
name: brand_key computed on the fly, not persisted
description: Product brand grouping for bot list sort order is derived from name at query time (services/normalize.compute_brand_key), not stored as a DB column.
---

The bot's product list groups/sorts by "brand" (e.g. all "Grok ..." variants together, not interleaved with other brands). `compute_brand_key(name)` takes the first alphanumeric token of the lowercased name and is called inline inside the sort key in `services/product_service.get_active_products_for_bot` — there is no `Product.brand_key` column.

**Why:** No other requirement (admin filtering/UI, reporting) needs the value persisted or indexed — only this one sort matters — so a migration/backfill would add complexity with no other consumer.

**How to apply:** If a future requirement needs to filter/search by brand (not just sort), that's the trigger to promote this into a real persisted+indexed column; until then, keep it computed on the fly.