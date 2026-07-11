import uuid
import json
from datetime import datetime
from sqlalchemy.orm import Session
from models import Order, OrderSourceAttempt, Product, User, OrderStatus, DeliveryMode
from services.product_service import get_best_source
from integrations.manager import api_manager


def _generate_order_code() -> str:
    return "ORD-" + uuid.uuid4().hex[:8].upper()


def get_or_create_user(db: Session, telegram_id: str, username: str = None, first_name: str = None, last_name: str = None) -> User:
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            last_active_at=datetime.utcnow(),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        user.last_active_at = datetime.utcnow()
        if username:
            user.username = username
        if first_name:
            user.first_name = first_name
        if last_name:
            user.last_name = last_name
        db.commit()
    return user


async def create_order(db: Session, telegram_user_id: str, product_id: int, quantity: int) -> Order:
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise ValueError("Product not found")

    order_code = _generate_order_code()
    total = product.sale_price * quantity

    order = Order(
        order_code=order_code,
        telegram_user_id=telegram_user_id,
        product_id=product_id,
        quantity=quantity,
        unit_price=product.sale_price,
        total_price=total,
        status=OrderStatus.pending_manual,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    if product.delivery_mode == DeliveryMode.api_auto:
        order.status = OrderStatus.processing_api
        db.commit()
        attempt_num = 1
        source = get_best_source(db, product_id)
        while source:
            adapter = api_manager.get_adapter(source.api_product.connection)
            result = await adapter.buy_product(
                product_id=source.api_product.external_product_id,
                quantity=quantity,
                idempotency_key=order_code,
            )
            attempt = OrderSourceAttempt(
                order_id=order.id,
                product_source_id=source.id,
                attempt_number=attempt_num,
                status="success" if result.get("success") else "failed",
                error_message=result.get("message") if not result.get("success") else None,
                external_order_id=result.get("order_id"),
            )
            db.add(attempt)
            db.commit()
            if result.get("success"):
                order.status = OrderStatus.completed
                order.api_connection_id = source.api_product.api_connection_id
                order.external_order_id = result.get("order_id")
                order.delivery_data = json.dumps(result.get("data", {}))
                db.commit()
                break
            attempt_num += 1
            source = None
        else:
            order.status = OrderStatus.failed
            db.commit()

    user = db.query(User).filter(User.telegram_id == telegram_user_id).first()
    if user:
        user.total_orders = (user.total_orders or 0) + 1
        user.total_spent = (user.total_spent or 0.0) + total
        db.commit()

    product.sold_count = (product.sold_count or 0) + quantity
    db.commit()

    db.refresh(order)
    return order


def get_order_status(db: Session, order_id: int) -> Order:
    return db.query(Order).filter(Order.id == order_id).first()


def update_order_delivery(db: Session, order_id: int, delivery_data: str, status: OrderStatus) -> Order:
    order = db.query(Order).filter(Order.id == order_id).first()
    if order:
        order.delivery_data = delivery_data
        order.status = status
        order.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(order)
    return order
