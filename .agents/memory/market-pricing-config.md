---
name: Market default markup & platform fee config
description: Where the chợ's default markup-on-attach and platform ("phí chợ") fee percentages live and how they interact with existing pricing features.
---

`services/market_pricing.py` holds two independently-configurable global percentages, stored as one JSON blob in the generic `Setting` table (key `market_pricing_config`), editable by the owner from Settings → "Markup mặc định & phí Chợ":

- `default_markup_percent` (default 10%): applied ONCE, only when a product is first attached/created from a supplier API with no sale_price typed in by the admin/tenant (`shared_catalog.attach_shared_product` caller in `routers/products.py`, and the API-sources product-creation flow). It does NOT change the ongoing price_sync_service auto-adjust formula — that still preserves the resulting VND margin as a fixed snapshot when the source price moves later (existing "price-margin-preserving" design, left untouched by design).
- `platform_fee_percent` (default 3%, previously a hardcoded 2%): the "phí chợ" debited from a tenant's market wallet on top of cost-of-goods per sale (`payment_service.py`'s `_debit_market_wallet_for_order` → `market_wallet_service.debit_for_sale`).

**Why:** these two were previously hardcoded (0% markup, 2% fee) with no admin control; the user wanted a coherent, adjustable default now that they're absorbing a real supplier fee, without touching the deliberate margin-preservation design of `price_sync_service.py`.

**How to apply:** if the ongoing per-tick auto-adjust should someday become percentage-of-source instead of fixed-VND-margin-preserving, that is a separate, bigger behavioral change to `price_sync_service._apply_source_price_change` — do not conflate it with this module.

**Tenant secrecy + floor (added later):** for products attached via the owner's shared catalog (`ProductSource.shared_from_admin == True`, checked via `shared_catalog.is_shared_from_admin_product`), a non-owner tenant must never see the raw source_price/margin, and their sale_price can never go below `default_sale_price(source_price)`. Enforced server-side in `routers/products.py` (`edit_product`, `create_product_from_source`) using the DB's stored source_price, not whatever the form submitted — a tenant editing/tampering the hidden source_price field must not change the computed floor. Templates gate the "Giá nguồn"/margin UI behind `product.hide_source_price` (`= is_shared_from_admin and not request.state.is_owner`). This does NOT apply to a tenant's own private API connections (no secrecy needed there) or manual products.
