"""
UrlBuilder — joins a connection's base_url with an admin-configured endpoint,
without producing double slashes or duplicated path segments, and supports
both relative endpoints ("/products") and absolute ones
("https://other-host.example.com/v2/products").

Placeholders like {product_id} or {order_id} inside the endpoint path are
substituted from `path_params` before joining.
"""
import re

_ABS_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def substitute_path_params(path: str, path_params: dict | None) -> str:
    if not path:
        return path
    if not path_params:
        return path
    for key, value in path_params.items():
        path = path.replace("{" + str(key) + "}", str(value))
    return path


def build_url(base_url: str, endpoint: str | None, path_params: dict | None = None) -> str:
    """
    - If `endpoint` is blank, returns base_url unchanged.
    - If `endpoint` is an absolute URL (http:// or https://), it is used
      as-is (after placeholder substitution) — base_url is ignored.
    - Otherwise, endpoint is treated as relative to base_url: exactly one
      slash is placed between them, and any run of consecutive slashes
      inside the resulting path (but not in the "scheme://" separator) is
      collapsed to one, which also prevents duplicated path segments like
      "/api/public/market//products".
    """
    endpoint = (endpoint or "").strip()
    endpoint = substitute_path_params(endpoint, path_params)

    if not endpoint:
        return base_url.rstrip("/")

    if _ABS_URL_RE.match(endpoint):
        return endpoint

    base = (base_url or "").rstrip("/")
    rel = endpoint.lstrip("/")
    joined = f"{base}/{rel}"

    # Collapse duplicate slashes, but keep the "scheme://" double-slash intact.
    scheme_match = re.match(r"^(https?://)", joined, re.IGNORECASE)
    scheme = scheme_match.group(1) if scheme_match else ""
    rest = joined[len(scheme):]
    rest = re.sub(r"/+", "/", rest)
    return scheme + rest
