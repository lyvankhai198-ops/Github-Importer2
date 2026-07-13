"""
Unit tests for integrations.canboso.CanBosoAdapter — test connection,
product listing/normalization, buy (account success), API error handling,
and timeout handling. No real network calls (httpx.AsyncClient is faked).
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
        {"id": "p1", "name": "Netflix 1 Month", "price": 50000, "stock": 10,
         "slotProductType": "account", "seller": "shopA", "emoji": "🎬"},
        {"id": "p2", "name": "Discord Nitro Slot", "price": 20000, "stock": 3,
         "type": "slot", "seller": "shopB", "category": "gaming"},
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
    assert products[1]["item_type"] == "slot"
    assert products[1]["seller"] == "shopB"


@pytest.mark.asyncio
async def test_buy_product_account_success(monkeypatch):
    adapter = make_adapter()
    calls = []
    monkeypatch.setattr(
        "integrations.canboso.httpx.AsyncClient",
        make_fake_async_client(
            [FakeResponse(200, {"success": True, "order_id": "ord-123", "accounts": ["user1|pass1"]})],
            calls,
        ),
    )
    result = await adapter.buy_product("p1", 1, "idem-1", buyer_email="user@example.com")
    assert result["success"] is True
    assert result["order_id"] == "ord-123"
    method, url, payload = calls[0]
    assert url.endswith("/products/p1/buy")
    assert payload == {"quantity": 1, "email": "user@example.com"}


@pytest.mark.asyncio
async def test_buy_product_api_error(monkeypatch):
    adapter = make_adapter()
    monkeypatch.setattr(
        "integrations.canboso.httpx.AsyncClient",
        make_fake_async_client([FakeResponse(400, {}, text="Out of stock")]),
    )
    result = await adapter.buy_product("p1", 1, "idem-2", buyer_email="user@example.com")
    assert result["success"] is False
    assert "400" in result["message"]
    assert result["order_id"] is None


@pytest.mark.asyncio
async def test_buy_product_timeout(monkeypatch):
    adapter = make_adapter()
    monkeypatch.setattr(
        "integrations.canboso.httpx.AsyncClient",
        make_fake_async_client([], raise_timeout=True),
    )
    result = await adapter.buy_product("p1", 1, "idem-3", buyer_email="user@example.com")
    assert result["success"] is False
    assert "Timeout" in result["message"]


@pytest.mark.asyncio
async def test_get_balance_not_supported():
    adapter = make_adapter()
    result = await adapter.get_balance()
    assert result["success"] is False
