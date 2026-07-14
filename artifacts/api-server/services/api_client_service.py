"""
api_client_service.py — business logic for customer programmatic API keys
and API-originated orders.

Money-moving rule (see .agents/memory/wallet-ledger-design.md): the wallet
debit and the order's payment_status flip to "paid" happen in ONE atomic
transaction via wallet_service.debit_wallet(..., extra_updates=[...]) guarded
on `payment_status = 'pending'`. This mirrors the existing wallet-deposit
confirm/reject and wallet-purchase-debit flows — never a separate
check-then-write.

client_order_id idempotency: enforced by a partial unique index on
orders(api_client_id, client_order_id) (see main.py migrations). A retry
with the same client_order_id from the same client always returns the
original order's current result instead of creating/charging a second time.
"""
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from database import SessionLocal
from models import (
    ApiClient, ApiClientStatus, ApiRequestLog, Order, OrderStatus, PaymentStatus,
    Product, User, WalletCurrency, WalletTxType,
)
from services import wallet_service
from services.api_key_service import generate_api_key, hash_api_key
from services.order_service import get_delivery_items, get_or_create_user

logger = logging.getLogger(__name__)

DEFAULT_PERMISSIONS = ["account:read", "products:read", "orders:read", "orders:create"]

# Public-safe order status → response status mapping (never leaks internals)
_ORDER_RESPONSE_STATUS = {
    OrderStatus.completed: "completed",
    OrderStatus.partial_delivery: "partial_delivery",
    OrderStatus.pending_manual: "processing",
    OrderStatus.processing_api: "processing",
    OrderStatus.paid_waiting_stock: "processing",
    OrderStatus.api_failed: "failed",
    OrderStatus.failed: "failed",
    OrderStatus.cancelled: "cancelled",
}


class ApiOrderError(Exception):
    """Raised for client-facing order-creation errors (400-class)."""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class InsufficientBalanceApiError(Exception):
    def __init__(self, currency: str, balance: float, needed: float):
        self.currency = currency
        self.balance = balance
        self.needed = needed
        super().__init__(f"Insufficient {currency} balance")


# ── Key lifecycle ────────────────────────────────────────────────────────────

def get_client_for_user(db: Session, telegram_user_id: str) -> ApiClient:
    return db.query(ApiClient).filter(ApiClient.telegram_user_id == telegram_user_id).first()


def generate_key_for_user(db: Session, telegram_user_id: str) -> tuple[ApiClient, str]:
    """Create (or reset, if one already exists) the customer's API client
    and return (client, full_raw_key). The raw key is never stored — show it
    to the customer exactly once, at the call site."""
    full_key, prefix = generate_api_key()
    key_hash = hash_api_key(full_key)

    client = get_client_for_user(db, telegram_user_id)
    if client:
        client.key_hash = key_hash
        client.key_prefix = prefix
        client.status = ApiClientStatus.active
        client.updated_at = datetime.utcnow()
    else:
        client = ApiClient(
            telegram_user_id=telegram_user_id,
            key_hash=key_hash,
            key_prefix=prefix,
            status=ApiClientStatus.active,
            permissions=json.dumps(DEFAULT_PERMISSIONS),
        )
        db.add(client)
    db.commit()
    db.refresh(client)
    return client, full_key


def regenerate_key(db: Session, client: ApiClient) -> str:
    """Issues a brand-new key value for an existing client; the old key stops
    working immediately (key_hash is overwritten in the same commit)."""
    full_key, prefix = generate_api_key()
    client.key_hash = hash_api_key(full_key)
    client.key_prefix = prefix
    client.status = ApiClientStatus.active
    client.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(client)
    return full_key


def revoke_key(db: Session, client: ApiClient):
    client.status = ApiClientStatus.revoked
    client.updated_at = datetime.utcnow()
    db.commit()


def set_lock(db: Session, client: ApiClient, locked: bool):
    client.status = ApiClientStatus.locked if locked else ApiClientStatus.active
    client.updated_at = datetime.utcnow()
    db.commit()


def get_permissions(client: ApiClient) -> list:
    try:
        return json.loads(client.permissions) if client.permissions else list(DEFAULT_PERMISSIONS)
    except Exception:
        return list(DEFAULT_PERMISSIONS)


# ── Rate limiting ────────────────────────────────────────────────────────────

def check_rate_limits(db: Session, client: ApiClient) -> str | None:
    """Returns an error code string if a limit is exceeded, else None.
    Reads existing ApiRequestLog rows only — the current request's own row
    is written afterwards by the logging middleware, so it never counts
    itself."""
    now = datetime.utcnow()
    minute_ago = now - timedelta(seconds=60)
    recent = db.query(ApiRequestLog).filter(
        ApiRequestLog.api_client_id == client.id,
        ApiRequestLog.created_at >= minute_ago,
    ).count()
    if recent >= client.rate_limit_per_minute:
        return "rate_limit_per_minute_exceeded"

    today_start = datetime(now.year, now.month, now.day)
    today_count = db.query(ApiRequestLog).filter(
        ApiRequestLog.api_client_id == client.id,
        ApiRequestLog.created_at >= today_start,
    ).count()
    if today_count >= client.daily_limit:
        return "daily_limit_exceeded"
    return None


# ── Order creation (wallet-funded, replay-safe) ─────────────────────────────

def _order_public_dict(order: Order) -> dict:
    status_key = _ORDER_RESPONSE_STATUS.get(order.status, "processing")
    result = {
        "order_code": order.order_code,
        "client_order_id": order.client_order_id,
        "status": status_key,
        "product_id": order.product_id,
        "quantity": order.quantity,
        "currency": order.payment_currency or "VND",
        "unit_price": order.unit_price,
        "total_price": order.total_price,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }
    if order.status in (OrderStatus.completed, OrderStatus.partial_delivery):
        items = get_delivery_items(order)
        result["delivered_count"] = len(items)
        result["items"] = items  # already stripped of raw supplier payload
    return result


async def create_api_order(client: ApiClient, product_id: int, quantity: int,
                            client_order_id: str, currency: str = "VND") -> dict:
    """
    Validate → dedupe (client_order_id) → atomically debit wallet → hand off
    to the existing paid-order fulfillment path → return a safe response.
    Uses its own SessionLocal so it can be called from a request handler
    without holding the request's db session open across the async
    fulfillment call.
    """
    from services.payment_service import process_paid_order

    db = SessionLocal()
    try:
        client_order_id = (client_order_id or "").strip()
        if not client_order_id:
            raise ApiOrderError("invalid_request", "client_order_id is required")
        currency = (currency or "VND").upper()
        if currency not in ("VND", "USDT"):
            raise ApiOrderError("invalid_request", "currency must be VND or USDT")
        if not isinstance(quantity, int) or quantity < 1:
            raise ApiOrderError("invalid_request", "quantity must be a positive integer")

        # ── Replay: same client + client_order_id already seen ──────────────
        existing = db.query(Order).filter(
            Order.api_client_id == client.id,
            Order.client_order_id == client_order_id,
        ).first()
        if existing:
            return _order_public_dict(existing)

        product = db.query(Product).filter(Product.id == product_id, Product.is_active == True).first()
        if not product:
            raise ApiOrderError("product_not_found", "Product not found or inactive")
        if quantity < (product.min_quantity or 1):
            raise ApiOrderError("invalid_request", f"Minimum quantity is {product.min_quantity or 1}")

        from services.product_service import get_product_stock_status
        stock_info = get_product_stock_status(product.id, db)
        if stock_info["status"] == "out_of_stock":
            raise ApiOrderError("out_of_stock", "Product is currently out of stock")

        unit_price = product.sale_price if currency == "VND" else product.price_usdt
        if not unit_price or unit_price <= 0:
            raise ApiOrderError("invalid_request", f"Product has no {currency} price configured")
        total = round(unit_price * quantity, 2 if currency == "USDT" else 0)

        from services.order_service import _generate_order_code
        from services.warranty import parse_warranty_to_days
        order = Order(
            order_code=_generate_order_code(),
            telegram_user_id=client.telegram_user_id,
            product_id=product.id,
            quantity=quantity,
            unit_price=unit_price,
            total_price=total,
            expected_amount=total,
            paid_amount=0.0,
            status=OrderStatus.pending_payment,
            payment_status=PaymentStatus.pending,
            payment_method="api_key",
            payment_currency=currency,
            api_client_id=client.id,
            client_order_id=client_order_id,
            warranty_days=parse_warranty_to_days(product.warranty),
        )
        db.add(order)
        try:
            db.commit()
        except IntegrityError:
            # Concurrent duplicate submit raced us to the unique index —
            # the other request created it first; return its result.
            db.rollback()
            existing = db.query(Order).filter(
                Order.api_client_id == client.id,
                Order.client_order_id == client_order_id,
            ).first()
            if existing:
                return _order_public_dict(existing)
            raise ApiOrderError("internal_error", "Could not create order")
        db.refresh(order)

        wallet_currency = WalletCurrency.VND if currency == "VND" else WalletCurrency.USDT
        try:
            wallet_service.debit_wallet(
                db, client.telegram_user_id, wallet_currency, total, WalletTxType.purchase,
                order_id=order.id, note=f"API order {order.order_code} ({client.name or client.key_prefix})",
                actor="api",
                extra_updates=[(
                    "UPDATE orders SET payment_status = 'paid', paid_amount = ?, paid_at = ? "
                    "WHERE id = ? AND payment_status = 'pending'",
                    (total, datetime.utcnow().isoformat(sep=" "), order.id),
                )],
            )
            db.refresh(order)
        except wallet_service.InsufficientBalanceError as e:
            # No money moved — safe to mark the order failed with a plain
            # (non-wallet) update; the client_order_id stays claimed so a
            # retry with the same key returns this same failure, never a
            # second debit attempt.
            order.status = OrderStatus.cancelled
            order.payment_status = PaymentStatus.failed
            order.notes = "Insufficient wallet balance for API order"
            db.commit()
            raise InsufficientBalanceApiError(currency, e.balance, e.amount)
        except wallet_service.AlreadyProcessedError:
            db.refresh(order)
            return _order_public_dict(order)

        client.total_orders = (client.total_orders or 0) + 1
        if currency == "VND":
            client.total_revenue_vnd = (client.total_revenue_vnd or 0.0) + total
        else:
            client.total_revenue_usdt = (client.total_revenue_usdt or 0.0) + total
        db.commit()

        await process_paid_order(order.id)
        db.refresh(order)

        await _notify_admin_api_order(order, success=order.status in (
            OrderStatus.completed, OrderStatus.partial_delivery, OrderStatus.pending_manual,
        ))

        return _order_public_dict(order)
    finally:
        db.close()


async def _notify_admin_api_order(order: Order, success: bool):
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        from models import TelegramBotConfig
        db = SessionLocal()
        try:
            cfg = db.query(TelegramBotConfig).first()
            admin_id = cfg.admin_telegram_id if cfg else ""
        finally:
            db.close()
        if not admin_id:
            return
        from bot.notifier import notify_admin_api_order_result
        await notify_admin_api_order_result(bot_manager._application.bot, order, admin_id, success=success)
    except Exception as e:
        logger.error(f"[api_client_service] admin notify failed: {e}")
