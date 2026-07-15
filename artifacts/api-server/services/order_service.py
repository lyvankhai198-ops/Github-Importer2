import uuid
import json
import asyncio
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from models import Order, OrderSourceAttempt, Product, User, OrderStatus, DeliveryMode
from services.product_service import get_best_source
from services.normalize import normalize_delivery_items
from integrations.manager import api_manager

logger = logging.getLogger(__name__)

# Idempotency: track orders being processed to prevent double-submit
_processing_keys: set = set()


def _generate_order_code() -> str:
    return "ORD-" + uuid.uuid4().hex[:8].upper()


def get_or_create_user(
    db: Session,
    telegram_id: str,
    username: str = None,
    first_name: str = None,
    last_name: str = None,
) -> User:
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


async def _poll_source_order(adapter, external_order_id: str, max_attempts: int = 5, delay: float = 2.0):
    """Poll GET /orders/{id} until accounts arrive or attempts exhaust."""
    for attempt in range(max_attempts):
        await asyncio.sleep(delay)
        try:
            result = await adapter.get_order(external_order_id)
            if result.get("success"):
                data = result.get("data", {})
                items = normalize_delivery_items(data)
                if items:
                    return data, items
        except Exception as e:
            logger.warning(f"Poll attempt {attempt+1} failed: {e}")
    return None, []


async def create_order(
    db: Session,
    telegram_user_id: str,
    product_id: int,
    quantity: int,
    idempotency_key: str = None,
) -> Order:
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise ValueError("Product not found")

    order_code = _generate_order_code()
    total = product.sale_price * quantity

    from services.warranty import parse_warranty_to_days
    order = Order(
        order_code=order_code,
        telegram_user_id=telegram_user_id,
        product_id=product_id,
        quantity=quantity,
        unit_price=product.sale_price,
        total_price=total,
        status=OrderStatus.pending_manual,
        warranty_days=parse_warranty_to_days(product.warranty),
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    if product.delivery_mode == DeliveryMode.api_auto:
        idem_key = idempotency_key or order_code

        # Idempotency guard — prevent double-submit
        if idem_key in _processing_keys:
            logger.warning(f"Order {order_code} already processing, skipping duplicate")
            db.refresh(order)
            return order
        _processing_keys.add(idem_key)

        try:
            order.status = OrderStatus.processing_api
            db.commit()

            source = get_best_source(db, product_id)
            attempt_num = 1

            while source:
                from services.shared_catalog import resolve_api_product, resolve_api_connection
                src_api_product = resolve_api_product(db, source)
                src_connection = resolve_api_connection(db, src_api_product)
                adapter = api_manager.get_adapter(src_connection)
                # Email-requiring suppliers (e.g. AI Center Buyer) need a
                # buyer email on every purchase; the bot doesn't
                # collect one from shoppers on this manual/admin-triggered
                # path either, so a deterministic per-user placeholder is
                # used. Adapters that don't need it ignore it.
                buyer_email = f"tguser{telegram_user_id}@aicenter-orders.local"
                buy_result = await adapter.buy_product(
                    product_id=src_api_product.external_product_id,
                    quantity=quantity,
                    idempotency_key=idem_key,
                    buyer_email=buyer_email,
                    requires_customer_email=bool(src_api_product.external_requires_customer_email),
                    requires_slot_months=bool(src_api_product.external_requires_slot_months),
                )

                attempt = OrderSourceAttempt(
                    order_id=order.id,
                    product_source_id=source.id,
                    attempt_number=attempt_num,
                    status="success" if buy_result.get("success") else "failed",
                    # Do NOT log full error containing credentials
                    error_message=(buy_result.get("message") or "")[:500] if not buy_result.get("success") else None,
                    external_order_id=buy_result.get("order_id"),
                )
                db.add(attempt)
                db.commit()

                if buy_result.get("success"):
                    raw_data = buy_result.get("data", {})
                    items = normalize_delivery_items(raw_data)

                    # If no items yet, poll source for up to 5 times
                    if not items and buy_result.get("order_id"):
                        logger.info(f"No accounts yet for {order_code}, polling source order...")
                        polled_data, items = await _poll_source_order(
                            adapter, buy_result["order_id"]
                        )
                        if polled_data:
                            raw_data = polled_data

                    # Extract readable source order code
                    order_data = raw_data.get("order", raw_data)
                    external_order_code = (
                        order_data.get("order_code") or
                        order_data.get("order_id") or
                        buy_result.get("order_id") or ""
                    )

                    # Detect partial delivery
                    if items and len(items) < quantity:
                        order.status = OrderStatus.partial_delivery
                        order.partial_count = len(items)
                    elif items:
                        order.status = OrderStatus.completed
                    else:
                        # Still no items after polling → pending_manual for admin
                        order.status = OrderStatus.pending_manual

                    order.api_connection_id = src_api_product.api_connection_id
                    order.external_order_id = buy_result.get("order_id")
                    order.external_order_code = external_order_code
                    order.source_unit_price = src_api_product.external_price
                    # Store raw response — strip any sensitive balance info before logging
                    safe_data = {k: v for k, v in raw_data.items() if k not in ("balance_after", "balance")}
                    order.delivery_data = json.dumps(safe_data, ensure_ascii=False)
                    # Store normalized items (without exposing to logs)
                    order.delivery_items = json.dumps(items, ensure_ascii=False)
                    db.commit()
                    break

                attempt_num += 1
                source = None  # No fallback chain yet (extend if needed)

            if order.status == OrderStatus.processing_api:
                order.status = OrderStatus.failed
                db.commit()

        finally:
            _processing_keys.discard(idem_key)

    # Update user stats
    user = db.query(User).filter(User.telegram_id == telegram_user_id).first()
    if user:
        user.total_orders = (user.total_orders or 0) + 1
        user.total_spent = (user.total_spent or 0.0) + total
        db.commit()

    product.sold_count = (product.sold_count or 0) + quantity
    db.commit()

    # Membership rank recompute (this create_order path is the "instant",
    # non-payment-gated order flow — process_paid_order never runs for it,
    # so the rank hook has to live here instead). Best-effort, never blocks
    # order creation on failure.
    if order.status in (OrderStatus.completed, OrderStatus.partial_delivery):
        try:
            from services.rank_service import recompute_user_rank
            from services.bot_service import bot_manager
            bot = bot_manager._application.bot if bot_manager.is_running() else None
            await recompute_user_rank(db, telegram_user_id, bot=bot)
        except Exception as e:
            logger.error(f"[order_service] rank recompute failed for order {order.id}: {e}")

    db.refresh(order)
    return order


def get_order_by_id(db: Session, order_id: int) -> Order:
    return db.query(Order).filter(Order.id == order_id).first()


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


def get_delivery_items(order: Order) -> list:
    """Parse normalized delivery items from order."""
    if not order.delivery_items:
        return []
    try:
        return json.loads(order.delivery_items)
    except Exception:
        return []
