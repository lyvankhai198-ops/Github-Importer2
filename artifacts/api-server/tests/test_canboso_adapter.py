"""
Unit tests for integrations.canboso.CanBosoAdapter — the real CanBoSo
Public Market API (canboso.com/api/public/market), using field names taken
from CanBoSo's own Swagger schema (MarketProduct/BuyItemResponse): test
connection, product listing/normalization/pagination, buy (account vs slot
response shapes), and error/timeout handling. No real network calls
(httpx.AsyncClient is faked).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from integrations.canboso import CanBosoAdapter
from tests.fake_httpx import FakeResponse, make_fake_async_client


def make_adapter():
    return CanBosoAdapter(base_url="https://canboso.com/api/public/market", api_key="test-key")


@pytest.mark.asyncio
async def test_connection_success(monkeypatch):
    adapter = make_adapter()
    calls = []
    monkeypatch.setattr(
        "integrations.canboso.httpx.AsyncClient",
        make_fake_async_client([FakeResponse(200, {"data": []})], calls),
    )
    result = await adapter.test_connection()
    assert result["success"] is True
    method, url, params = calls[0]
    assert method == "GET"
    assert url.endswith("/products")
    assert params == {"limit": 1}


@pytest.mark.asyncio
async def test_connection_http_error(monkeypatch):
    adapter = make_adapter()
    monkeypatch.setattr(
        "integrations.canboso.httpx.AsyncClient",
        make_fake_async_client([FakeResponse(401, {}, text="Unauthorized")]),
    )
    result = await adapter.test_connection()
    assert result["success"] is False
    assert "401" in result["message"]


@pytest.mark.asyncio
async def test_connection_timeout(monkeypatch):
    adapter = make_adapter()
    monkeypatch.setattr(
        "integrations.canboso.httpx.AsyncClient",
        make_fake_async_client([], raise_timeout=True),
    )
    result = await adapter.test_connection()
    assert result["success"] is False
    assert "Timeout" in result["message"]


@pytest.mark.asyncio
async def test_get_products_normalizes_and_paginates(monkeypatch):
    adapter = make_adapter()
    page1 = [
        {"_id": "p1", "productName": "Netflix 1 Month", "marketSalePrice": 50000,
         "slotProductType": "account", "sellerDisplayName": "shopA", "emoji": "🎬",
         "stats": {"total": 20, "sold": 10, "available": 10}},
        {"_id": "p2", "productName": "Discord Nitro Slot", "marketSalePrice": 20000,
         "isSlotProduct": True, "sellerDisplayName": "shopB", "emoji": "🎮",
         "stats": {"total": 5, "sold": 2, "available": 3}},
    ]
    # Second page returns fewer than `limit` items -> pagination stops.
    monkeypatch.setattr(
        "integrations.canboso.httpx.AsyncClient",
        make_fake_async_client([FakeResponse(200, {"data": page1}), FakeResponse(200, {"data": []})]),
    )
    products = await adapter.get_products(limit=2)
    assert len(products) == 2
    assert products[0]["id"] == "p1"
    assert products[0]["item_type"] == "account"
    assert products[0]["seller"] == "shopA"
    assert products[0]["stock"] == 10
    assert products[1]["item_type"] == "slot"
    assert products[1]["seller"] == "shopB"


@pytest.mark.asyncio
async def test_buy_account_product_success(monkeypatch):
    adapter = make_adapter()
    calls = []
    monkeypatch.setattr(
        "integrations.canboso.httpx.AsyncClient",
        make_fake_async_client(
            [FakeResponse(200, {
                "user": "user1@example.com", "password": "pass1",
                "verifyEmail": "user1@example.com", "expiryText": "12 months",
                "otherInfo": "",
            })],
            calls,
        ),
    )
    result = await adapter.buy_product("p1", 1, "idem-1", buyer_email="buyer@example.com")
    assert result["success"] is True
    method, url, payload = calls[0]
    assert url.endswith("/products/p1/buy")
    assert payload == {"quantity": 1, "email": "buyer@example.com"}
    # Flat BuyItemResponse is wrapped as {"accounts": [...]} for
    # normalize_delivery_items() to find.
    assert result["data"]["accounts"][0]["user"] == "user1@example.com"


@pytest.mark.asyncio
async def test_buy_slot_product_returns_order(monkeypatch):
    adapter = make_adapter()
    monkeypatch.setattr(
        "integrations.canboso.httpx.AsyncClient",
        make_fake_async_client(
            [FakeResponse(200, {"order_id": "ord-99", "status": "paid", "items": None})],
        ),
    )
    result = await adapter.buy_product("p2", 1, "idem-2", buyer_email="buyer@example.com")
    assert result["success"] is True
    assert result["order_id"] == "ord-99"


@pytest.mark.asyncio
async def test_buy_product_api_error(monkeypatch):
    adapter = make_adapter()
    monkeypatch.setattr(
        "integrations.canboso.httpx.AsyncClient",
        make_fake_async_client([FakeResponse(400, {}, text="Out of stock")]),
    )
    result = await adapter.buy_product("p1", 1, "idem-3", buyer_email="buyer@example.com")
    assert result["success"] is False
    assert "400" in result["message"]
    assert result["order_id"] is None


@pytest.mark.asyncio
async def test_buy_product_success_false_body(monkeypatch):
    adapter = make_adapter()
    monkeypatch.setattr(
        "integrations.canboso.httpx.AsyncClient",
        make_fake_async_client([FakeResponse(200, {"success": False, "code": "OUT_OF_STOCK", "message": "Hết hàng"})]),
    )
    result = await adapter.buy_product("p1", 1, "idem-4", buyer_email="buyer@example.com")
    assert result["success"] is False
    assert result["message"] == "Hết hàng"


@pytest.mark.asyncio
async def test_buy_product_timeout(monkeypatch):
    adapter = make_adapter()
    monkeypatch.setattr(
        "integrations.canboso.httpx.AsyncClient",
        make_fake_async_client([], raise_timeout=True),
    )
    result = await adapter.buy_product("p1", 1, "idem-5", buyer_email="buyer@example.com")
    assert result["success"] is False
    assert "Timeout" in result["message"]


@pytest.mark.asyncio
async def test_get_balance_not_supported():
    adapter = make_adapter()
    result = await adapter.get_balance()
    assert result["success"] is False
