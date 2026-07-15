"""
Tests for services.api_service.sync_api_products against a slot-capable
supplier connection (e.g. AI Center Buyer): creates ApiProduct rows, never
duplicates them on re-sync, and updates price/stock in place. Uses a fake
adapter (no real HTTP calls) so the test only exercises the sync/persistence
logic shared by every adapter.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from models import ApiConnection, ApiProduct, ApiType, AuthType
from services import api_service


class FakeSlotSupplierAdapter:
    def __init__(self, products):
        self._products = products

    async def get_products(self, **filters):
        return self._products


@pytest.fixture()
def slot_supplier_connection(db_session):
    conn = ApiConnection(
        name="Slot Supplier Test",
        base_url="https://example.com/api/products",
        api_key_encrypted="",
        auth_type=AuthType.x_api_key,
        api_type=ApiType.aicenter_buyer,
    )
    db_session.add(conn)
    db_session.commit()
    db_session.refresh(conn)
    return conn


def _install_fake_adapter(monkeypatch, products):
    monkeypatch.setattr(
        api_service.api_manager, "get_adapter", lambda conn: FakeSlotSupplierAdapter(products)
    )


@pytest.mark.asyncio
async def test_sync_creates_products(db_session, slot_supplier_connection, monkeypatch):
    products = [
        {"id": "sp-1", "name": "Netflix Premium", "price": 60000, "stock": 5,
         "item_type": "account", "seller": "seller1", "category": "streaming", "raw": {"id": "sp-1"}},
        {"id": "sp-2", "name": "Discord Slot", "price": 15000, "stock": 2,
         "item_type": "slot", "seller": "seller2", "category": "gaming", "raw": {"id": "sp-2"}},
    ]
    _install_fake_adapter(monkeypatch, products)

    result = await api_service.sync_api_products(db_session, slot_supplier_connection.id)

    assert result["success"] is True
    assert result["created"] == 2
    assert result["updated"] == 0
    rows = db_session.query(ApiProduct).filter(ApiProduct.api_connection_id == slot_supplier_connection.id).all()
    assert len(rows) == 2
    by_id = {r.external_product_id: r for r in rows}
    assert by_id["sp-1"].external_item_type == "account"
    assert by_id["sp-1"].external_seller == "seller1"
    assert by_id["sp-2"].external_item_type == "slot"


@pytest.mark.asyncio
async def test_resync_does_not_duplicate_and_updates_price_stock(db_session, slot_supplier_connection, monkeypatch):
    initial = [
        {"id": "sp-1", "name": "Netflix Premium", "price": 60000, "stock": 5,
         "item_type": "account", "seller": "seller1", "category": "streaming", "raw": {"id": "sp-1"}},
    ]
    _install_fake_adapter(monkeypatch, initial)
    r1 = await api_service.sync_api_products(db_session, slot_supplier_connection.id)
    assert r1["created"] == 1

    updated = [
        {"id": "sp-1", "name": "Netflix Premium", "price": 55000, "stock": 1,
         "item_type": "account", "seller": "seller1", "category": "streaming", "raw": {"id": "sp-1"}},
    ]
    _install_fake_adapter(monkeypatch, updated)
    r2 = await api_service.sync_api_products(db_session, slot_supplier_connection.id)

    assert r2["created"] == 0
    assert r2["updated"] == 1

    rows = db_session.query(ApiProduct).filter(
        ApiProduct.api_connection_id == slot_supplier_connection.id,
        ApiProduct.external_product_id == "sp-1",
    ).all()
    # Never duplicated — still exactly one row for this external_product_id.
    assert len(rows) == 1
    assert rows[0].external_price == 55000
    assert rows[0].external_stock == 1


@pytest.mark.asyncio
async def test_sync_counts_item_errors_without_aborting(db_session, slot_supplier_connection, monkeypatch):
    class BadItem(dict):
        def get(self, key, default=None):
            if key == "id":
                raise ValueError("malformed item")
            return super().get(key, default)

    products = [
        BadItem({"id": "sp-bad", "name": "Broken"}),
        {"id": "sp-good", "name": "Good product", "price": 1000, "stock": 1,
         "item_type": "account", "seller": "s", "category": "c", "raw": {"id": "sp-good"}},
    ]
    _install_fake_adapter(monkeypatch, products)

    result = await api_service.sync_api_products(db_session, slot_supplier_connection.id)

    assert result["success"] is True
    assert result["errors"] == 1
    assert result["created"] == 1
    rows = db_session.query(ApiProduct).filter(ApiProduct.api_connection_id == slot_supplier_connection.id).all()
    assert len(rows) == 1
    assert rows[0].external_product_id == "sp-good"
