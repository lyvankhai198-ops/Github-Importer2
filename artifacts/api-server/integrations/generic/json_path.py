"""
JsonPathResolver — resolves a simple dot-path (with optional [n] array
indices) into an arbitrary parsed-JSON structure. Used for every
admin-configured response field (products_list_path, product_price_path,
order_response_id_path, etc).

Examples: "", ".", None            -> the root value itself
          "data"                    -> data["data"]
          "data.products"           -> data["data"]["products"]
          "result.items[0]"         -> data["result"]["items"][0]
"""
import re

_SEGMENT_RE = re.compile(r"^([^\[\]]*)((?:\[\d+\])*)$")


class JsonPathError(Exception):
    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"JSON path '{path}' not found: {reason}")


def resolve_path(data, path: str, required: bool = False):
    """Returns the resolved value, or None if not found (unless
    required=True, in which case JsonPathError is raised)."""
    if path is None or path.strip() in ("", "."):
        return data

    current = data
    for raw_segment in path.split("."):
        raw_segment = raw_segment.strip()
        if not raw_segment:
            continue
        m = _SEGMENT_RE.match(raw_segment)
        key = m.group(1) if m else raw_segment
        indices = re.findall(r"\[(\d+)\]", m.group(2)) if m else []

        if key:
            if isinstance(current, dict):
                if key not in current:
                    if required:
                        raise JsonPathError(path, f"key '{key}' missing at this level")
                    return None
                current = current[key]
            else:
                if required:
                    raise JsonPathError(path, f"expected object to read key '{key}', got {type(current).__name__}")
                return None

        for idx_str in indices:
            idx = int(idx_str)
            if isinstance(current, list):
                if idx >= len(current):
                    if required:
                        raise JsonPathError(path, f"index [{idx}] out of range")
                    return None
                current = current[idx]
            else:
                if required:
                    raise JsonPathError(path, f"expected list to index [{idx}], got {type(current).__name__}")
                return None

    return current


_LIST_FALLBACK_PATHS = [None, "data", "data.products", "products", "result.items", "items"]


def resolve_list(data, path: str | None):
    """
    Resolve a list of items. If `path` is given, uses it directly (raising
    JsonPathError with a clear message if missing or not a list). If blank,
    tries — in order — the response root itself, then "data",
    "data.products", "products", "result.items", "items".
    """
    if path:
        value = resolve_path(data, path, required=True)
        if not isinstance(value, list):
            raise JsonPathError(path, f"resolved value is not a list (got {type(value).__name__})")
        return value

    for fallback in _LIST_FALLBACK_PATHS:
        value = resolve_path(data, fallback, required=False)
        if isinstance(value, list) and value:
            return value
    # Nothing non-empty found — prefer an explicit empty list at root/data
    # over silently returning [] from a totally wrong shape.
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for fallback in _LIST_FALLBACK_PATHS[1:]:
            value = resolve_path(data, fallback, required=False)
            if isinstance(value, list):
                return value
    return []
