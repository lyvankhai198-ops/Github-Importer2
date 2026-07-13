"""
Tests for services.payment_service.process_paid_order's CanBoSo Market
branching: account-type items deliver instantly (existing behavior), while
slot-type items go to pending_seller_fulfillment. Also covers the API-error
path and that buy_product is called at most once per order (no double
purchase on a repeated call).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from models import (
    ApiConnection, ApiProduct, ApiType, AuthType, Product, ProductSource,
    User, Order, OrderStatus, PaymentStatus, DeliveryMode, SourceType,
)
from services import payment_service


def make_session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine), engine


class FakeAdapter:
    def __init__(self, buy_result):
        self.buy_result = buy_result
        self.buy_calls = 0

    async def buy_product(self, product_id, quantity, idempotency_key, buyer_email=None, **kwargs):
        self.buy_calls += 1
        return self.buy_result


def _seed_order(session, item_type: str, delivery_mode=DeliveryMode.api_auto):
    conn = ApiConnection(
        name="CanBoSo", base_url="https://canboso.com/api/public/market",
        api_key_encrypted="", auth_type=AuthType.x_api_key, api_type=ApiType.canboso_market,
    )
    session.add(conn)
    session.commit()

    api_product = ApiProduct(
        api_connection_id=conn.id, external_product_id="cb-1", external_name="Test item",
        external_price=10000, external_stock=5, external_item_type=item_type,
        external_seller="sellerX", external_category="cat",
    )
    session.add(api_product)
    session.commit()

    product = Product(
        product_code="PC-1", name="Test Product", sale_price=10000,
        source_type=SourceType.api, delivery_mode=delivery_mode,
    )
    session.add(product)
    session.commit()

    source = ProductSource(product_id=product.id, api_product_id=api_product.id, is_active=True, last_stock=5)
    session.add(source)

    user = User(telegram_id="111", balance=0.0)
    session.add(user)
    session.commit()

    order = Order(
        order_code="ORD-TEST-1", telegram_user_id=user.telegram_id, product_id=product.id,
        quantity=1, unit_price=10000, total_price=10000,
        status=OrderStatus.pending_payment,
        payment_status=PaymentStatus.paid,
    )
    session.add(order)
    session.commit()
    return order.id


@pytest.fixture()
def isolated_db(monkeypatch):
    TestSessionLocal, engine = make_session_factory()
    monkeypatch.setattr("database.SessionLocal", TestSessionLocal)
    yield TestSessionLocal
    engine.dispose()


@pytest.mark.asyncio
async def test_account_type_delivers_instantly(isolated_db, monkeypatch):
    session = isolated_db()
    order_id = _seed_order(session, item_type="account")
    session.close()

    fake_adapter = FakeAdapter({"success": True, "order_id": "ext-1", "data": {"accounts": ["user1|pass1"]}})
    monkeypatch.setattr("integrations.manager.api_manager.get_adapter", lambda conn: fake_adapter)

    await payment_service.process_paid_order(order_id)

    session2 = isolated_db()
    order = session2.query(Order).filter(Order.id == order_id).first()
    assert order.status == OrderStatus.completed
    assert fake_adapter.buy_calls == 1
    session2.close()


@pytest.mark.asyncio
async def test_slot_type_goes_pending_seller_fulfillment(isolated_db, monkeypatch):
    session = isolated_db()
    order_id = _seed_order(session, item_type="slot")
    session.close()

    fake_adapter = FakeAdapter({"success": True, "order_id": "ext-2", "data": {"order": {"order_id": "ext-2"}}})
    monkeypatch.setattr("integrations.manager.api_manager.get_adapter", lambda conn: fake_adapter)

    await payment_service.process_paid_order(order_id)

    session2 = isolated_db()
    order = session2.query(Order).filter(Order.id == order_id).first()
    assert order.status == OrderStatus.pending_seller_fulfillment
    assert order.external_order_id == "ext-2"
    session2.close()


@pytest.mark.asyncio
async def test_buy_api_error_marks_api_failed(isolated_db, monkeypatch):
    session = isolated_db()
    order_id = _seed_order(session, item_type="account")
    session.close()

    fake_adapter = FakeAdapter({"success": False, "message": "HTTP 400: bad request", "order_id": None, "data": {}})
    monkeypatch.setattr("integrations.manager.api_manager.get_adapter", lambda conn: fake_adapter)

    await payment_service.process_paid_order(order_id)

    session2 = isolated_db()
    order = session2.query(Order).filter(Order.id == order_id).first()
    assert order.status == OrderStatus.api_failed
    session2.close()


@pytest.mark.asyncio
async def test_no_double_buy_on_repeated_call(isolated_db, monkeypatch):
    """process_paid_order is documented idempotent — calling it again after
    a successful buy must not call the supplier's buy endpoint a second
    time (no double charge / no duplicate order)."""
    session = isolated_db()
    order_id = _seed_order(session, item_type="account")
    session.close()

    fake_adapter = FakeAdapter({"success": True, "order_id": "ext-3", "data": {"accounts": ["u|p"]}})
    monkeypatch.setattr("integrations.manager.api_manager.get_adapter", lambda conn: fake_adapter)

    await payment_service.process_paid_order(order_id)
    await payment_service.process_paid_order(order_id)  # retry

    assert fake_adapter.buy_calls == 1

    session2 = isolated_db()
    orders = session2.query(Order).filter(Order.id == order_id).all()
    assert len(orders) == 1  # never duplicated into a second Order row
    session2.close()
