"""
ProductMapper — maps one raw supplier product item onto the internal
normalized shape used by services/api_service.py's sync logic:
  id, name, description, price, stock, min_quantity, max_quantity, status,
  image_url, warranty, duration, raw, and (only if configured) item_type,
  seller, category, metadata.

If an admin configures an explicit JSON path for a field, that path is used
(and a missing path raises a clear error — the config is simply wrong).
If a field's path is left blank, a generic key-guessing fallback (shared by
every supplier that doesn't configure explicit paths) is used — this is the
same fallback behavior previously hardcoded per-adapter, now centralized
here so it applies uniformly to any custom API.
"""
from integrations.generic.json_path import resolve_path, JsonPathError


def _safe_int(val, default: int = 0):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _first(raw_item: dict, keys: list):
    for k in keys:
        v = raw_item.get(k)
        if v is not None and v != "":
            return v
    return None


class ProductMapper:
    def __init__(self, config: dict):
        self.config = config or {}
        extra = self.config.get("product_extra_mapping") or {}
        self.extra_mapping = extra if isinstance(extra, dict) else {}

    def _resolve_or_fallback(self, raw_item, path_key: str, fallback_keys: list, required_path: bool = True):
        path = self.config.get(path_key)
        if path:
            try:
                return resolve_path(raw_item, path, required=True)
            except JsonPathError as e:
                raise JsonPathError(path, f"[{path_key}] {e.reason}")
        return _first(raw_item, fallback_keys)

    def map(self, raw_item: dict) -> dict:
        product_id = str(self._resolve_or_fallback(raw_item, "product_id_path", ["product_id", "id"]) or "")
        name = self._resolve_or_fallback(raw_item, "product_name_path", ["name", "title"]) or ""
        description = self._resolve_or_fallback(
            raw_item, "product_description_path",
            ["description", "desc", "details", "content", "note"],
        ) or ""
        price_raw = self._resolve_or_fallback(
            raw_item, "product_price_path", ["price", "unit_price", "amount", "cost"]
        )
        try:
            price = float(price_raw or 0)
        except (TypeError, ValueError):
            price = 0.0
        stock_raw = self._resolve_or_fallback(
            raw_item, "product_stock_path", ["stock", "quantity", "available", "inventory"]
        )
        stock = _safe_int(stock_raw, 0)
        status = str(self._resolve_or_fallback(raw_item, "product_status_path", ["status"]) or "active")
        category = self._resolve_or_fallback(raw_item, "product_category_path", ["category"]) or ""

        # Fields with no dedicated top-level column — configured via the
        # generic product_extra_mapping JSON blob (path per field).
        min_qty = _safe_int(
            self._resolve_extra(raw_item, "min_quantity_path", ["min_quantity", "min_qty", "minimum"]), 1
        ) or 1
        max_qty_raw = self._resolve_extra(raw_item, "max_quantity_path", ["max_quantity", "max_qty", "maximum"])
        max_qty = _safe_int(max_qty_raw) if max_qty_raw else None
        image_url = str(self._resolve_extra(raw_item, "image_path", ["image", "image_url", "thumbnail", "photo"]) or "")
        warranty = str(self._resolve_extra(raw_item, "warranty_path", ["warranty", "guarantee"]) or "")
        duration = str(self._resolve_extra(raw_item, "duration_path", ["duration", "period", "validity", "expire"]) or "")

        result = {
            "id": product_id,
            "name": name,
            "description": description,
            "price": price,
            "stock": stock,
            "min_quantity": min_qty,
            "max_quantity": max_qty,
            "status": status,
            "image_url": image_url,
            "warranty": warranty,
            "duration": duration,
            "raw": raw_item,
        }
        if category:
            result["category"] = category

        item_type_path = self.extra_mapping.get("item_type_path")
        if item_type_path:
            raw_type = str(resolve_path(raw_item, item_type_path, required=False) or "").strip().lower()
            result["item_type"] = "slot" if "slot" in raw_type else "account"

        seller_path = self.extra_mapping.get("seller_path")
        if seller_path:
            result["seller"] = str(resolve_path(raw_item, seller_path, required=False) or "")

        metadata_paths = self.extra_mapping.get("metadata_paths") or {}
        if isinstance(metadata_paths, dict) and metadata_paths:
            result["metadata"] = {
                k: resolve_path(raw_item, p, required=False) for k, p in metadata_paths.items()
            }

        return result

    def _resolve_extra(self, raw_item, extra_key: str, fallback_keys: list):
        path = self.extra_mapping.get(extra_key)
        if path:
            return resolve_path(raw_item, path, required=False)
        return _first(raw_item, fallback_keys)
