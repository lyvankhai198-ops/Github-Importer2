"""
Unit tests for integrations.aicenter_buyer.AICenterBuyerAdapter — correct
endpoints (/api/telegram-buyer/balance|products|purchase), header-only auth
(X-API-Key, never Authorization/Bearer/access_token, never a query param),
response.products parsing, and purchase body building.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from integrations.aicenter_buyer import AICenterBuyerAdapter
from tests.fake_httpx import FakeResponse, make_fake_async_client


def make_adapter():
    return AICenterBuyerAdapter(base_url="https://canboso.com", api_key="test-key")


@pytest.mark.asyncio
async def test_connection_hits_exact_balance_endpoint(monkeypatch):
    adapter = make_adapter()
    calls = []
    monkeypatch.setattr(
        "integrations.aicenter_buyer.httpx.AsyncClient",
        make_fake_async_client([FakeResponse(200, {"success": True, "balance": 100000})], calls),
    )
    result = await adapter.test_connection()
    assert result["success"] is True
    method, url, params = calls[0]
    assert method == "GET"
    assert url == "https://canboso.com/api/telegram-buyer/balance"


@pytest.mark.asyncio
async def test_connection_never_sends_bearer_or_query_key(monkeypatch):
    adapter = make_adapter()
    captured_headers = {}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            captured_headers.update(headers or {})
            captured_headers["_params"] = params
            return FakeResponse(200, {"success": True})

    monkeypatch.setattr("integrations.aicenter_buyer.httpx.AsyncClient", _Client)
    await adapter.test_connection()
    assert captured_headers.get("X-API-Key") == "test-key"
    assert "Authorization" not in captured_headers
    assert not captured_headers.get("_params")  # no api_key/access_token via query


@pytest.mark.asyncio
async def test_connection_success_false_is_reported_as_failure(monkeypatch):
    adapter = make_adapter()
    monkeypatch.setattr(
        "integrations.aicenter_buyer.httpx.AsyncClient",
        make_fake_async_client([FakeResponse(200, {"success": False, "message": "Thiếu access token"})]),
    )
    result = await adapter.test_connection()
    assert result["success"] is False
    assert "access token" in result["message"]


@pytest.mark.asyncio
async def test_get_products_reads_response_products(monkeypatch):
    adapter = make_adapter()
    calls = []
    payload = {
        "walletCurrency": "VND",
        "products": [
            {
                "_id": "abc123",
                "product_name": "ChatGPT Plus 1 Month",
                "product_name_raw": "chatgpt-plus-1m",
                "pricing": 150000,
                "usdPricing": 6.0,
                "description": "Tài khoản ChatGPT Plus",
                "isSlotProduct": False,
                "requiresCustomerEmail": True,
                "requiresSlotMonths": False,
                "stats": {"available": 5},
            },
            {
                "_id": "slot1",
                "product_name": "Discord Nitro Slot",
                "pricing": 20000,
                "isSlotProduct": True,
                "slotDurations": [1, 3, 6],
                "requiresSlotMonths": True,
                "stats": {"available": 2},
            },
        ],
    }
    monkeypatch.setattr(
        "integrations.aicenter_buyer.httpx.AsyncClient",
        make_fake_async_client([FakeResponse(200, payload)], calls),
    )
    products = await adapter.get_products()
    method, url, _ = calls[0]
    assert url == "https://canboso.com/api/telegram-buyer/products"
    assert len(products) == 2
    assert products[0]["id"] == "abc123"
    assert products[0]["name"] == "ChatGPT Plus 1 Month"
    assert products[0]["price"] == 150000
    assert products[0]["usd_price"] == 6.0
    assert products[0]["item_type"] == "account"
    assert products[0]["requires_customer_email"] is True
    assert products[0]["stock"] == 5
    assert products[1]["item_type"] == "slot"
    assert products[1]["requires_slot_months"] is True
    assert products[1]["slot_durations"] == [1, 3, 6]


@pytest.mark.asyncio
async def test_buy_product_hits_exact_purchase_endpoint(monkeypatch):
    adapter = make_adapter()
    calls = []
    monkeypatch.setattr(
        "integrations.aicenter_buyer.httpx.AsyncClient",
        make_fake_async_client(
            [FakeResponse(200, {"success": True, "orderCode": "AC-999", "amount": 150000})],
            calls,
        ),
    )
    result = await adapter.buy_product("abc123", 1, "idem-1", buyer_email="user@example.com",
                                        requires_customer_email=True)
    assert result["success"] is True
    assert result["order_id"] == "AC-999"
    method, url, payload = calls[0]
    assert url == "https://canboso.com/api/telegram-buyer/purchase"
    assert payload == {"product_id": "abc123", "quantity": 1, "customer_email": "user@example.com"}
    assert "key" not in payload


@pytest.mark.asyncio
async def test_buy_product_includes_slot_months_only_when_required(monkeypatch):
    adapter = make_adapter()
    calls = []
    monkeypatch.setattr(
        "integrations.aicenter_buyer.httpx.AsyncClient",
        make_fake_async_client([FakeResponse(200, {"success": True, "orderCode": "AC-1000"})], calls),
    )
    await adapter.buy_product("slot1", 1, "idem-2", requires_slot_months=True, slot_months=3)
    _, _, payload = calls[0]
    assert payload["slot_months"] == 3

    calls.clear()
    monkeypatch.setattr(
        "integrations.aicenter_buyer.httpx.AsyncClient",
        make_fake_async_client([FakeResponse(200, {"success": True, "orderCode": "AC-1001"})], calls),
    )
    await adapter.buy_product("normal1", 1, "idem-3")
    _, _, payload2 = calls[0]
    assert "slot_months" not in payload2
    assert "customer_email" not in payload2


@pytest.mark.asyncio
async def test_buy_product_error_never_calls_wrong_endpoint(monkeypatch):
    adapter = make_adapter()
    calls = []
    monkeypatch.setattr(
        "integrations.aicenter_buyer.httpx.AsyncClient",
        make_fake_async_client([FakeResponse(400, {}, text="Bad request")], calls),
    )
    result = await adapter.buy_product("p1", 1, "idem-4")
    assert result["success"] is False
    method, url, _ = calls[0]
    assert url.endswith("/api/telegram-buyer/purchase")
    assert "/api/buy" not in url


@pytest.mark.asyncio
async def test_get_balance_not_supported_reported_gracefully(monkeypatch):
    adapter = make_adapter()
    monkeypatch.setattr(
        "integrations.aicenter_buyer.httpx.AsyncClient",
        make_fake_async_client([FakeResponse(500, {}, text="error")]),
    )
    result = await adapter.get_balance()
    assert result["success"] is False
