"""
GenericApiClient — the single engine that builds and sends every request
(test connection, product sync, balance check, order/buy creation, order
lookup, order listing) for ANY ApiConnection, purely from its config
columns. No supplier-specific branching lives here.
"""
import json
import time
import logging
import httpx

from integrations.generic.auth_builder import build_auth
from integrations.generic.url_builder import build_url
from integrations.generic.template_renderer import render_template
from integrations.generic.json_path import resolve_path, resolve_list, JsonPathError
from integrations.generic.product_mapper import ProductMapper
from integrations.generic.order_mapper import OrderMapper

logger = logging.getLogger(__name__)


def _to_config_dict(conn) -> dict:
    """Extract the generic-engine config columns off an ApiConnection row
    into a plain dict (also used directly by tests without a DB row)."""
    fields = [
        "auth_header_name", "auth_query_name", "auth_prefix",
        "test_endpoint", "test_method",
        "products_endpoint", "products_method",
        "order_endpoint", "order_method",
        "balance_endpoint", "balance_method",
        "order_get_endpoint", "order_get_method",
        "orders_list_endpoint", "orders_list_method",
        "default_query_params", "test_query_params", "products_query_params",
        "order_query_params", "order_body_template", "products_pagination",
        "products_list_path", "product_id_path", "product_name_path",
        "product_price_path", "product_stock_path", "product_description_path",
        "product_category_path", "product_status_path", "product_extra_mapping",
        "balance_value_path", "balance_currency_path",
        "order_response_id_path", "order_response_status_path",
        "order_response_items_path", "order_response_message_path",
    ]
    cfg = {}
    for f in fields:
        val = getattr(conn, f, None)
        if val and isinstance(val, str) and f in (
            "products_query_params", "test_query_params", "order_query_params",
            "default_query_params", "order_body_template", "products_pagination",
            "product_extra_mapping",
        ):
            try:
                cfg[f] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                cfg[f] = None
        else:
            cfg[f] = val
    return cfg


_SAFE_LOG_PREVIEW_LEN = 500


class GenericApiClient:
    def __init__(
        self,
        base_url: str,
        auth_type: str,
        config: dict,
        api_key: str = "",
        username: str = "",
        password: str = "",
        connection_id=None,
        connection_name: str = "",
        api_type: str = "",
        timeout: int = 30,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.auth_type = (auth_type or "none")
        self.config = config or {}
        self.api_key = api_key or ""
        self.username = username or ""
        self.password = password or ""
        self.connection_id = connection_id
        self.connection_name = connection_name
        self.api_type = api_type
        self.timeout = timeout

    # ── Request building ────────────────────────────────────────────────

    def _auth(self):
        return build_auth(
            self.auth_type,
            api_key=self.api_key,
            username=self.username,
            password=self.password,
            header_name=self.config.get("auth_header_name"),
            query_name=self.config.get("auth_query_name"),
            prefix=self.config.get("auth_prefix"),
        )

    def build_request(self, endpoint: str, method: str, query_template=None, body_template=None,
                       context: dict = None, path_params: dict = None):
        context = context or {}
        url = build_url(self.base_url, endpoint, path_params)
        auth_headers, auth_query = self._auth()

        headers = {"Content-Type": "application/json", "Accept": "application/json", **auth_headers}

        params = {}
        default_q = self.config.get("default_query_params")
        if default_q:
            params.update(render_template(default_q, context) or {})
        if query_template:
            params.update(render_template(query_template, context) or {})
        params.update(auth_query)

        body = render_template(body_template, context) if body_template else None

        return url, (method or "GET").upper(), headers, params, body

    def redact_url(self, url: str) -> str:
        """Never let a query-param API key show up in logs."""
        if self.auth_type != "query_param":
            return url
        name = self.config.get("auth_query_name") or "api_key"
        if f"{name}=" not in url:
            return url
        import re as _re
        return _re.sub(rf"({_re.escape(name)}=)[^&]*", r"\1***", url)

    async def _send(self, method: str, url: str, headers: dict, params: dict, body):
        start = time.time()
        status_code = None
        error = None
        response_json = None
        response_text = ""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if method == "GET":
                    r = await client.get(url, headers=headers, params=params or None)
                elif method == "POST":
                    r = await client.post(url, headers=headers, params=params or None, json=body)
                elif method == "PUT":
                    r = await client.put(url, headers=headers, params=params or None, json=body)
                elif method == "PATCH":
                    r = await client.patch(url, headers=headers, params=params or None, json=body)
                elif method == "DELETE":
                    r = await client.delete(url, headers=headers, params=params or None)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")
                status_code = r.status_code
                response_text = r.text or ""
                try:
                    response_json = r.json()
                except Exception:
                    response_json = None
        except httpx.TimeoutException:
            error = "Timeout"
        except Exception as e:
            error = str(e)
        duration_ms = int((time.time() - start) * 1000)
        self._log(method, url, status_code, duration_ms, error, response_json, response_text)
        return status_code, duration_ms, response_json, response_text, error

    def _log(self, method, url, status_code, duration_ms, error, response_json, response_text):
        preview_source = response_text or (json.dumps(response_json) if response_json is not None else "")
        response_preview = (preview_source or "")[:_SAFE_LOG_PREVIEW_LEN]
        logger.info(
            "GENERIC_API_REQUEST connection_id=%s connection_name=%s api_type=%s auth_type=%s "
            "method=%s url=%s status_code=%s duration_ms=%s has_api_key=%s error=%s response_preview=%r",
            self.connection_id, self.connection_name, self.api_type, self.auth_type,
            method, self.redact_url(url), status_code, duration_ms, bool(self.api_key),
            error, response_preview,
        )

    # ── High-level operations ───────────────────────────────────────────

    async def test_connection(self, context: dict = None) -> dict:
        endpoint = self.config.get("test_endpoint") or self.config.get("products_endpoint")
        method = self.config.get("test_method") or "GET"
        query_template = self.config.get("test_query_params")
        url, method, headers, params, body = self.build_request(endpoint, method, query_template, None, context)
        status_code, duration_ms, response_json, response_text, error = await self._send(method, url, headers, params, body)
        if error:
            return {"success": False, "message": self._safe_error(error), "latency_ms": duration_ms}
        if status_code and 200 <= status_code < 300:
            return {"success": True, "message": "Connection successful", "latency_ms": duration_ms, "data": response_json}
        return {
            "success": False,
            "message": f"HTTP {status_code}: {(response_text or '')[:300]}",
            "latency_ms": duration_ms,
        }

    async def get_balance(self, context: dict = None) -> dict:
        endpoint = self.config.get("balance_endpoint")
        if not endpoint:
            return {"success": False, "message": "Kết nối này không hỗ trợ tra cứu số dư", "balance": 0, "currency": "VND"}
        method = self.config.get("balance_method") or "GET"
        url, method, headers, params, body = self.build_request(endpoint, method, None, None, context)
        status_code, duration_ms, response_json, response_text, error = await self._send(method, url, headers, params, body)
        if error or not status_code or not (200 <= status_code < 300):
            msg = self._safe_error(error) if error else f"HTTP {status_code}: {(response_text or '')[:200]}"
            return {"success": False, "message": msg, "balance": 0, "currency": "VND"}
        data = response_json or {}
        value_path = self.config.get("balance_value_path")
        currency_path = self.config.get("balance_currency_path")
        balance = resolve_path(data, value_path, required=True) if value_path else (data.get("balance") or data.get("amount") or 0)
        currency = resolve_path(data, currency_path, required=True) if currency_path else (data.get("currency") or "VND")
        return {"success": True, "balance": balance, "currency": currency, "data": data}

    async def get_products(self, context: dict = None) -> list:
        context = context or {}
        endpoint = self.config.get("products_endpoint")
        method = self.config.get("products_method") or "GET"
        query_template = self.config.get("products_query_params")
        pagination = self.config.get("products_pagination") or {}
        mapper = ProductMapper(self.config)

        if not pagination.get("enabled"):
            url, m, headers, params, body = self.build_request(endpoint, method, query_template, None, context)
            status_code, _, response_json, _, error = await self._send(m, url, headers, params, body)
            if error or not status_code or not (200 <= status_code < 300):
                return []
            items = resolve_list(response_json, self.config.get("products_list_path"))
            return [mapper.map(item) for item in items]

        page_param = pagination.get("page_param", "page")
        limit_param = pagination.get("limit_param", "limit")
        limit = pagination.get("limit", 100)
        page = pagination.get("start_page", 1)
        max_pages = pagination.get("max_pages", 200)

        all_items = []
        pages_fetched = 0
        while pages_fetched < max_pages:
            page_context = dict(context)
            extra_q = {page_param: page, limit_param: limit}
            url, m, headers, params, body = self.build_request(endpoint, method, query_template, None, page_context)
            params.update(extra_q)
            status_code, _, response_json, _, error = await self._send(m, url, headers, params, body)
            pages_fetched += 1
            if error or not status_code or not (200 <= status_code < 300):
                break
            items = resolve_list(response_json, self.config.get("products_list_path"))
            if not items:
                break
            all_items.extend(items)
            if len(items) < limit:
                break
            page += 1
        return [mapper.map(item) for item in all_items]

    async def buy_product(self, quantity: int, idempotency_key: str, product_id: str = None,
                           buyer_email: str = None, price=None, user_id=None, **kwargs) -> dict:
        endpoint = self.config.get("order_endpoint")
        method = self.config.get("order_method") or "POST"
        query_template = self.config.get("order_query_params")
        body_template = self.config.get("order_body_template")

        context = {
            "quantity": quantity,
            "customer_email": buyer_email or "",
            "product_id": product_id,
            "external_product_id": product_id,
            "user_id": user_id,
            "price": price,
            "reference": idempotency_key,
            "order_id": idempotency_key,
        }
        path_params = {"product_id": product_id or "", "external_product_id": product_id or ""}
        url, m, headers, params, body = self.build_request(
            endpoint, method, query_template, body_template, context, path_params
        )
        status_code, _, response_json, response_text, error = await self._send(m, url, headers, params, body)
        mapper = OrderMapper(self.config)
        if error:
            return {"success": False, "message": self._safe_error(error), "order_id": None, "data": {}}
        if not status_code or not (200 <= status_code < 300):
            return {
                "success": False,
                "message": f"HTTP {status_code}: {(response_text or '')[:300]}",
                "order_id": None,
                "data": {},
            }
        try:
            return mapper.parse(response_json or {})
        except JsonPathError as e:
            return {"success": False, "message": f"Response mapping error: {e}", "order_id": None, "data": response_json or {}}

    async def get_orders(self, limit: int = 50, context: dict = None) -> list:
        endpoint = self.config.get("orders_list_endpoint")
        if not endpoint:
            return []
        method = self.config.get("orders_list_method") or "GET"
        ctx = dict(context or {})
        ctx["limit"] = limit
        url, m, headers, params, body = self.build_request(endpoint, method, {"limit": "{{limit}}"}, None, ctx)
        status_code, _, response_json, _, error = await self._send(m, url, headers, params, body)
        if error or not status_code or not (200 <= status_code < 300):
            return []
        data = response_json
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("data", data.get("orders", []))
        return []

    async def get_order(self, order_id: str) -> dict:
        endpoint = self.config.get("order_get_endpoint")
        if not endpoint:
            return {"success": False, "message": "Kết nối này không hỗ trợ tra cứu đơn theo id", "data": {}}
        method = self.config.get("order_get_method") or "GET"
        url, m, headers, params, body = self.build_request(
            endpoint, method, None, None, {"order_id": order_id}, {"order_id": order_id}
        )
        status_code, _, response_json, response_text, error = await self._send(m, url, headers, params, body)
        if error:
            return {"success": False, "message": self._safe_error(error), "data": {}}
        if status_code and 200 <= status_code < 300:
            return {"success": True, "data": response_json or {}}
        return {"success": False, "message": f"HTTP {status_code}: {(response_text or '')[:300]}", "data": {}}

    @staticmethod
    def _safe_error(error: str) -> str:
        # `error` here only ever comes from our own _send() (Timeout / str(e))
        # — never raw headers/credentials — so it's already safe to surface.
        return error or "Unknown error"
