"""
AuthBuilder — turns a connection's auth_type + decrypted credentials into
request headers and/or query params. This is the ONLY place auth logic
lives; no adapter/client code should build an Authorization header itself.
"""
import base64


def build_auth(
    auth_type: str,
    api_key: str = "",
    username: str = "",
    password: str = "",
    header_name: str = None,
    query_name: str = None,
    prefix: str = None,
) -> tuple[dict, dict]:
    """Returns (extra_headers, extra_query_params)."""
    auth_type = (auth_type or "none").lower()

    if auth_type == "none":
        return {}, {}

    if auth_type == "x_api_key":
        name = header_name or "X-API-Key"
        return {name: api_key or ""}, {}

    if auth_type == "bearer":
        pfx = prefix if prefix is not None else "Bearer"
        value = f"{pfx} {api_key}".strip() if pfx else (api_key or "")
        return {"Authorization": value}, {}

    if auth_type == "basic_auth":
        raw = f"{username or ''}:{password or ''}".encode("utf-8")
        encoded = base64.b64encode(raw).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}, {}

    if auth_type == "query_param":
        name = query_name or "api_key"
        return {}, {name: api_key or ""}

    if auth_type == "custom_header":
        name = header_name or "Authorization"
        pfx = prefix or ""
        value = f"{pfx} {api_key}".strip() if pfx else (api_key or "")
        return {name: value}, {}

    # Unknown auth type — fail safe with no auth rather than guessing.
    return {}, {}
