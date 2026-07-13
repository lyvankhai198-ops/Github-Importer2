"""
OrderMapper — maps a raw "create order / buy" API response onto the
internal normalized shape used by order_service.py / payment_service.py:
  {success, order_id, status, message, items, data}

`items` (the delivered accounts/keys/credentials) reuses the existing
generic services.normalize.normalize_delivery_items() heuristic when no
explicit order_response_items_path is configured — that heuristic was
already shared across every supplier, so it isn't provider-specific.
"""
from integrations.generic.json_path import resolve_path, JsonPathError
from services.normalize import normalize_delivery_items


class OrderMapper:
    def __init__(self, config: dict):
        self.config = config or {}

    def parse(self, raw: dict) -> dict:
        if not isinstance(raw, dict):
            raw = {}
        order_obj = raw.get("order", raw)

        id_path = self.config.get("order_response_id_path")
        if id_path:
            order_id = resolve_path(raw, id_path, required=True)
        else:
            order_id = (
                raw.get("order_id") or order_obj.get("order_id") or
                order_obj.get("id") or raw.get("id") or ""
            )
        order_id = str(order_id) if order_id is not None else ""

        status_path = self.config.get("order_response_status_path")
        if status_path:
            success = bool(resolve_path(raw, status_path, required=True))
        else:
            success = bool(raw.get("success", True))

        message_path = self.config.get("order_response_message_path")
        if message_path:
            message = resolve_path(raw, message_path, required=False) or ""
        else:
            message = raw.get("message", "")

        items_path = self.config.get("order_response_items_path")
        if items_path:
            items_raw = resolve_path(raw, items_path, required=True)
            items = self._normalize_items(items_raw)
        else:
            items = normalize_delivery_items(raw)

        return {
            "success": success,
            "order_id": order_id,
            "message": message,
            "items": items,
            "data": raw,
        }

    @staticmethod
    def _normalize_items(items_raw) -> list:
        if items_raw is None:
            return []
        if isinstance(items_raw, list):
            return normalize_delivery_items({"items": items_raw})
        if isinstance(items_raw, str):
            return normalize_delivery_items({"items": items_raw})
        return normalize_delivery_items({"items": [items_raw]})
