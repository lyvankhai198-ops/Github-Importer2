"""
Test matrix for the generic API connection engine (integrations/generic/).
Covers every auth mode, URL joining, JSON path resolution, product mapping,
order body templating + response mapping, and the CanBoSo/Zampto/Custom
presets end-to-end through GenericApiClient (with httpx faked out).
"""
import json
import pytest

from integrations.generic.auth_builder import build_auth
from integrations.generic.url_builder import build_url
from integrations.generic.template_renderer import render_template
from integrations.generic.json_path import resolve_path, resolve_list, JsonPathError
from integrations.generic.product_mapper import ProductMapper
from integrations.generic.order_mapper import OrderMapper
from integrations.generic.client import GenericApiClient
from integrations.generic.presets import PRESETS

from tests.fake_httpx import make_fake_async_client, FakeResponse


# ── Auth builder ─────────────────────────────────────────────────────────

def test_auth_x_api_key_header():
    headers, params = build_auth("x_api_key", api_key="secret123", header_name="X-API-Key")
    assert headers == {"X-API-Key": "secret123"}
    assert params == {}


def test_auth_bearer_token():
    headers, params = build_auth("bearer", api_key="tok123", prefix="Bearer")
    assert headers == {"Authorization": "Bearer tok123"}
    assert params == {}


def test_auth_query_param():
    headers, params = build_auth("query_param", api_key="qk123", query_name="apikey")
    assert headers == {}
    assert params == {"apikey": "qk123"}


def test_auth_custom_header():
    headers, params = build_auth("custom_header", api_key="ck123", header_name="X-Custom-Auth", prefix="Token")
    assert headers == {"X-Custom-Auth": "Token ck123"}


def test_auth_basic_auth():
    headers, params = build_auth("basic_auth", username="admin", password="pw123")
    assert headers["Authorization"].startswith("Basic ")
    import base64
    decoded = base64.b64decode(headers["Authorization"].split(" ")[1]).decode()
    assert decoded == "admin:pw123"


def test_auth_none():
    headers, params = build_auth("none")
    assert headers == {} and params == {}


# ── URL builder ──────────────────────────────────────────────────────────

def test_url_builder_relative_join():
    assert build_url("https://api.example.com", "/products") == "https://api.example.com/products"


def test_url_builder_no_double_slash():
    assert build_url("https://api.example.com/", "/products") == "https://api.example.com/products"


def test_url_builder_absolute_endpoint_ignores_base():
    assert build_url("https://api.example.com", "https://other.example.com/v2/x") == "https://other.example.com/v2/x"


def test_url_builder_placeholder_substitution():
    url = build_url("https://api.example.com", "/products/{product_id}/buy", {"product_id": "abc-1"})
    assert url == "https://api.example.com/products/abc-1/buy"


def test_url_builder_blank_endpoint_returns_base():
    assert build_url("https://api.example.com/", None) == "https://api.example.com"


# ── JSON path ────────────────────────────────────────────────────────────

def test_json_path_simple():
    data = {"data": {"products": [{"id": 1}]}}
    assert resolve_path(data, "data.products") == [{"id": 1}]


def test_json_path_array_index():
    data = {"result": {"items": [{"id": "x"}, {"id": "y"}]}}
    assert resolve_path(data, "result.items[1]") == {"id": "y"}


def test_json_path_missing_required_raises():
    with pytest.raises(JsonPathError):
        resolve_path({"a": 1}, "b.c", required=True)


def test_json_path_missing_optional_returns_none():
    assert resolve_path({"a": 1}, "b.c", required=False) is None


def test_resolve_list_explicit_path():
    data = {"result": {"items": [1, 2, 3]}}
    assert resolve_list(data, "result.items") == [1, 2, 3]


def test_resolve_list_fallback_data_products():
    data = {"data": {"products": [{"id": 1}]}}
    assert resolve_list(data, None) == [{"id": 1}]


def test_resolve_list_fallback_root_list():
    data = [{"id": 1}, {"id": 2}]
    assert resolve_list(data, None) == data


# ── Template renderer ───────────────────────────────────────────────────

def test_render_template_full_token_preserves_type():
    result = render_template({"quantity": "{{quantity}}"}, {"quantity": 3})
    assert result == {"quantity": 3}


def test_render_template_inline_substitution():
    result = render_template({"email": "user: {{customer_email}}"}, {"customer_email": "a@b.com"})
    assert result == {"email": "user: a@b.com"}


def test_render_template_json_string_input():
    result = render_template('{"product_id": "{{external_product_id}}"}', {"external_product_id": "p1"})
    assert result == {"product_id": "p1"}


def test_render_template_none_returns_none():
    assert render_template(None, {}) is None


# ── Product mapper ───────────────────────────────────────────────────────

def test_product_mapper_explicit_paths():
    config = {
        "product_id_path": "sku",
        "product_name_path": "title",
        "product_price_path": "cost.amount",
        "product_stock_path": "inv",
    }
    mapper = ProductMapper(config)
    raw = {"sku": "P1", "title": "Widget", "cost": {"amount": 9.99}, "inv": 5}
    mapped = mapper.map(raw)
    assert mapped["id"] == "P1"
    assert mapped["name"] == "Widget"
    assert mapped["price"] == 9.99
    assert mapped["stock"] == 5


def test_product_mapper_fallback_heuristic():
    mapper = ProductMapper({})
    raw = {"id": "P2", "name": "Gadget", "price": 12.5, "stock": 3}
    mapped = mapper.map(raw)
    assert mapped["id"] == "P2"
    assert mapped["name"] == "Gadget"
    assert mapped["price"] == 12.5
    assert mapped["stock"] == 3


def test_product_mapper_extra_mapping_item_type():
    config = {"product_extra_mapping": {"item_type_path": "productType"}}
    mapper = ProductMapper(config)
    raw = {"id": "P3", "name": "X", "price": 1, "stock": 1, "productType": "slot_seller"}
    mapped = mapper.map(raw)
    assert mapped["item_type"] == "slot"


def test_product_mapper_missing_required_path_raises():
    mapper = ProductMapper({"product_id_path": "missing.path"})
    with pytest.raises(JsonPathError):
        mapper.map({"id": "x"})


# ── Order mapper ─────────────────────────────────────────────────────────

def test_order_mapper_fallback():
    mapper = OrderMapper({})
    raw = {"order_id": "O1", "success": True, "message": "ok"}
    result = mapper.parse(raw)
    assert result["order_id"] == "O1"
    assert result["success"] is True


def test_order_mapper_explicit_paths():
    config = {
        "order_response_id_path": "order.id",
        "order_response_status_path": "order.ok",
        "order_response_message_path": "order.msg",
    }
    mapper = OrderMapper(config)
    raw = {"order": {"id": "O2", "ok": True, "msg": "done"}}
    result = mapper.parse(raw)
    assert result["order_id"] == "O2"
    assert result["success"] is True
    assert result["message"] == "done"


# ── GenericApiClient end-to-end (httpx faked) ────────────────────────────

@pytest.mark.asyncio
async def test_client_test_connection_x_api_key(monkeypatch):
    calls = []
    fake_factory = make_fake_async_client([FakeResponse(200, {"ok": True})], calls)
    monkeypatch.setattr("httpx.AsyncClient", fake_factory)

    config = {"test_endpoint": "/products", "test_method": "GET"}
    client = GenericApiClient(
        base_url="https://api.example.com", auth_type="x_api_key", config=config,
        api_key="secret", connection_id=1, connection_name="Test", api_type="custom",
    )
    result = await client.test_connection()
    assert result["success"] is True
    assert calls[0][0] == "GET"
    assert calls[0][1] == "https://api.example.com/products"


@pytest.mark.asyncio
async def test_client_buy_product_body_template_and_order_mapping(monkeypatch):
    calls = []
    fake_factory = make_fake_async_client(
        [FakeResponse(200, {"order_id": "ORD1", "success": True, "items": ["acc1:pw1"]})], calls
    )
    monkeypatch.setattr("httpx.AsyncClient", fake_factory)

    config = {
        "order_endpoint": "/products/{product_id}/buy",
        "order_method": "POST",
        "order_body_template": {"quantity": "{{quantity}}", "email": "{{customer_email}}"},
    }
    client = GenericApiClient(
        base_url="https://api.example.com", auth_type="bearer", config=config,
        api_key="tok", connection_id=2, connection_name="Test2", api_type="custom",
    )
    result = await client.buy_product(quantity=2, idempotency_key="idem1", product_id="ABC", buyer_email="x@y.com")
    assert result["success"] is True
    assert result["order_id"] == "ORD1"
    method, url, body = calls[0]
    assert url == "https://api.example.com/products/ABC/buy"
    assert body == {"quantity": 2, "email": "x@y.com"}


@pytest.mark.asyncio
async def test_client_query_param_auth_redacted_in_log(monkeypatch, caplog):
    calls = []
    fake_factory = make_fake_async_client([FakeResponse(200, {"ok": True})], calls)
    monkeypatch.setattr("httpx.AsyncClient", fake_factory)

    config = {"test_endpoint": "/ping", "test_method": "GET"}
    client = GenericApiClient(
        base_url="https://api.example.com", auth_type="query_param", config=config,
        api_key="topsecret", connection_id=3, connection_name="QP", api_type="custom",
    )
    client.config["auth_query_name"] = "api_key"
    await client.test_connection()
    # The real key must never appear as a query param value in the call the
    # fake transport recorded (it's sent to the real API, which is fine —
    # but check our own redaction helper produces a masked URL for logs).
    _, url, params = calls[0]
    assert params.get("api_key") == "topsecret"
    redacted = client.redact_url(url + "?api_key=topsecret")
    assert "topsecret" not in redacted


@pytest.mark.asyncio
async def test_client_timeout_handled_safely(monkeypatch):
    fake_factory = make_fake_async_client([], [], raise_timeout=True)
    monkeypatch.setattr("httpx.AsyncClient", fake_factory)

    config = {"test_endpoint": "/ping"}
    client = GenericApiClient(
        base_url="https://api.example.com", auth_type="none", config=config,
        connection_id=4, connection_name="TO", api_type="custom",
    )
    result = await client.test_connection()
    assert result["success"] is False
    assert "Timeout" in result["message"]


@pytest.mark.asyncio
async def test_client_get_products_with_pagination(monkeypatch):
    calls = []
    responses = [
        FakeResponse(200, {"data": {"products": [{"id": "1", "name": "A", "price": 1, "stock": 1}]}}),
        FakeResponse(200, {"data": {"products": []}}),
    ]
    fake_factory = make_fake_async_client(responses, calls)
    monkeypatch.setattr("httpx.AsyncClient", fake_factory)

    config = {
        "products_endpoint": "/products",
        "products_method": "GET",
        "products_pagination": {"enabled": True, "page_param": "page", "limit_param": "limit", "limit": 1, "start_page": 1, "max_pages": 5},
    }
    client = GenericApiClient(
        base_url="https://api.example.com", auth_type="x_api_key", config=config,
        api_key="k", connection_id=5, connection_name="Pag", api_type="canboso_market",
    )
    products = await client.get_products()
    assert len(products) == 1
    assert products[0]["id"] == "1"


# ── Presets structural sanity (used by the Add/Edit UI) ─────────────────

def test_presets_exist_for_canboso_zampto_custom():
    assert set(PRESETS.keys()) == {"canboso_market", "zampto_standard", "custom"}
    for name, preset in PRESETS.items():
        assert "auth_type" in preset
        assert "products_endpoint" in preset


@pytest.mark.asyncio
async def test_canboso_preset_end_to_end_via_generic_client(monkeypatch):
    calls = []
    fake_factory = make_fake_async_client(
        [FakeResponse(200, {"data": {"products": [{"id": "cb1", "name": "CB Product", "price": 100000, "stock": 2, "productType": "account"}]}})],
        calls,
    )
    monkeypatch.setattr("httpx.AsyncClient", fake_factory)

    preset = dict(PRESETS["canboso_market"])
    preset["products_pagination"] = json.loads(preset["products_pagination"])
    preset["product_extra_mapping"] = json.loads(preset["product_extra_mapping"])
    preset["products_pagination"]["max_pages"] = 1  # keep the test to one request

    client = GenericApiClient(
        base_url=preset["base_url"], auth_type=preset["auth_type"], config=preset,
        api_key="k", connection_id=6, connection_name="CanBoSo", api_type="canboso_market",
    )
    products = await client.get_products()
    assert len(products) == 1
    assert products[0]["id"] == "cb1"
    assert products[0]["item_type"] == "account"
