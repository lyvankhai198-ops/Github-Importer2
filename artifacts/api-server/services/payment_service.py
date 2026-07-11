"""
SePay payment service.

Responsibilities:
  - Payment code generation
  - VietQR URL construction (public img.vietqr.io — no API key needed)
  - Pending payment order creation
  - Webhook transaction normalization + matching
  - process_paid_order (idempotent, runs API purchase after confirmed payment)
  - Expiry background loop

Security rules:
  - api_token / webhook_secret NEVER written to logs or responses.
  - POST /buy called at most once per order (idempotency set).
  - process_paid_order is idempotent: checks order.status before acting.
  - payment_status is ONLY set by webhook processing — never by user callbacks.
"""
import json
import logging
import asyncio
import uuid
import re
from datetime import datetime, timedelta
from urllib.parse import quote
from sqlalchemy.orm import Session

from models import (
    Order, OrderStatus, PaymentStatus, PaymentTransaction,
    SepayConfig, TelegramBotConfig, User,
)

logger = logging.getLogger(__name__)

# Prevent concurrent process_paid_order calls for the same order
_processing_paid: set = set()


# ── Config ─────────────────────────────────────────────────────────────────────

def get_sepay_config(db: Session):
    return db.query(SepayConfig).first()


def get_or_create_sepay_config(db: Session) -> SepayConfig:
    cfg = db.query(SepayConfig).first()
    if not cfg:
        cfg = SepayConfig()
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def is_sepay_enabled(db: Session) -> bool:
    cfg = get_sepay_config(db)
    return bool(cfg and cfg.is_enabled)


# ── Payment code ───────────────────────────────────────────────────────────────

def generate_payment_code(order_code: str, prefix: str = "AIC") -> str:
    """
    Generate a unique payment transfer content code.
    Format: {PREFIX}{8 uppercase hex chars}
    Example: AICCF4B8D1A
    """
    import hashlib
    seed = order_code + uuid.uuid4().hex
    hex_part = hashlib.md5(seed.encode()).hexdigest()[:8].upper()
    return f"{prefix}{hex_part}"


# ── VietQR ─────────────────────────────────────────────────────────────────────

def generate_vietqr_url(bank_bin: str, account_number: str, amount: float,
                         payment_code: str, account_name: str = "") -> str:
    """
    Build VietQR image URL using public img.vietqr.io.
    No API key required.
    """
    amount_int = int(amount)
    encoded_code = quote(payment_code, safe="")
    encoded_name = quote(account_name, safe="")
    return (
        f"https://img.vietqr.io/image/{bank_bin}-{account_number}-qr_only.jpg"
        f"?amount={amount_int}&addInfo={encoded_code}&accountName={encoded_name}"
    )


# ── Create pending payment order ───────────────────────────────────────────────

def create_pending_payment_order(
    db: Session,
    telegram_user_id: str,
    product_id: int,
    quantity: int,
) -> Order:
    """
    Create an order in pending_payment state.
    Does NOT call API source — payment must arrive first.
    """
    from models import Product
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise ValueError("Product not found")

    cfg = get_sepay_config(db)
    prefix = (cfg.payment_prefix or "AIC") if cfg else "AIC"
    timeout = cfg.payment_timeout_minutes if cfg else 15

    order_code = "ORD-" + uuid.uuid4().hex[:8].upper()
    total = product.sale_price * quantity
    payment_code = generate_payment_code(order_code, prefix)

    order = Order(
        order_code=order_code,
        telegram_user_id=telegram_user_id,
        product_id=product_id,
        quantity=quantity,
        unit_price=product.sale_price,
        total_price=total,
        expected_amount=total,
        paid_amount=0.0,
        status=OrderStatus.pending_payment,
        payment_status=PaymentStatus.pending,
        payment_method="bank_transfer",
        payment_code=payment_code,
        payment_expires_at=datetime.utcnow() + timedelta(minutes=timeout),
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    # Update user activity
    user = db.query(User).filter(User.telegram_id == telegram_user_id).first()
    if user:
        user.last_active_at = datetime.utcnow()
        db.commit()

    return order


# ── Webhook transaction processing ─────────────────────────────────────────────

def _normalize_sepay_transaction(raw: dict) -> dict:
    """
    Map SePay webhook fields to canonical names.
    SePay sends: id, gateway, transactionDate, accountNumber,
                 transferContent, transferAmount, referenceCode.
    """
    return {
        "transaction_id": str(raw.get("id") or raw.get("transactionId") or ""),
        "gateway": raw.get("gateway", ""),
        "transaction_date": raw.get("transactionDate") or raw.get("transaction_date") or "",
        "account_number": raw.get("accountNumber") or raw.get("account_number") or "",
        "transfer_content": raw.get("transferContent") or raw.get("transfer_content") or raw.get("content") or "",
        "amount_in": float(raw.get("transferAmount") or raw.get("amount_in") or 0),
        "amount_out": float(raw.get("deductionAmount") or raw.get("amount_out") or 0),
        "reference_code": raw.get("referenceCode") or raw.get("reference_code") or "",
    }


def _find_payment_code(content: str, prefix: str = "AIC") -> str | None:
    """Find payment_code (prefix + 8 hex chars) in transfer content, case-insensitive."""
    pattern = re.compile(rf"({re.escape(prefix)}[0-9A-Fa-f]{{8}})", re.IGNORECASE)
    match = pattern.search(content or "")
    return match.group(1).upper() if match else None


def _parse_tx_date(raw_date: str) -> datetime | None:
    if not raw_date:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            return datetime.strptime(str(raw_date), fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(str(raw_date).replace("Z", ""))
    except Exception:
        return datetime.utcnow()


def process_webhook_transaction(db: Session, raw: dict) -> dict:
    """
    Save and match a SePay webhook event.
    Returns dict with action, order_id, new_paid, expected.

    Idempotent: duplicate tx_id → ignored via unique constraint check.
    Only processes amount_in > 0.
    """
    tx_data = _normalize_sepay_transaction(raw)
    tx_id = tx_data["transaction_id"]
    if not tx_id:
        return {"success": False, "reason": "missing_transaction_id"}

    amount_in = tx_data["amount_in"]
    if amount_in <= 0:
        return {"success": True, "action": "ignored_outgoing"}

    # Deduplication
    existing = db.query(PaymentTransaction).filter_by(
        provider="sepay", external_transaction_id=tx_id
    ).first()
    if existing:
        logger.info(f"[payment] duplicate tx {tx_id} ignored")
        return {"success": True, "action": "duplicate_ignored"}

    tx_date = _parse_tx_date(tx_data["transaction_date"])

    # Build transaction record
    tx = PaymentTransaction(
        provider="sepay",
        external_transaction_id=tx_id,
        gateway=tx_data["gateway"],
        transaction_date=tx_date,
        account_number=tx_data["account_number"],
        transfer_content=tx_data["transfer_content"],
        amount_in=amount_in,
        amount_out=tx_data["amount_out"],
        reference_code=tx_data["reference_code"],
        match_status="unmatched",
        raw_json=json.dumps(raw, ensure_ascii=False)[:10000],
    )

    # Find matching order via payment_code in transfer content
    sepay_cfg = get_sepay_config(db)
    prefix = (sepay_cfg.payment_prefix or "AIC") if sepay_cfg else "AIC"
    payment_code = _find_payment_code(tx_data["transfer_content"], prefix)

    order = None
    if payment_code:
        order = db.query(Order).filter(Order.payment_code == payment_code).first()

    if not order:
        db.add(tx)
        db.commit()
        logger.warning(f"[payment] tx {tx_id}: no order for content='{tx_data['transfer_content']}'")
        return {"success": True, "action": "unmatched"}

    tx.matched_order_id = order.id

    # Expired orders
    if order.status == OrderStatus.payment_expired:
        tx.match_status = "late_payment"
        db.add(tx)
        db.commit()
        return {"success": True, "action": "late_payment", "order_id": order.id}

    # Already-terminal orders
    if order.status in (OrderStatus.completed, OrderStatus.cancelled,
                         OrderStatus.api_failed, OrderStatus.failed):
        tx.match_status = "late_payment"
        db.add(tx)
        db.commit()
        return {"success": True, "action": "order_already_done", "order_id": order.id}

    # Accumulate paid_amount
    current_paid = order.paid_amount or 0.0
    new_paid = current_paid + amount_in
    expected = order.expected_amount or order.total_price
    allow_overpay = sepay_cfg.allow_overpay if sepay_cfg else True

    order.paid_amount = new_paid
    order.payment_transaction_id = tx_id

    if new_paid < expected:
        order.payment_status = PaymentStatus.partial
        tx.match_status = "partial"
        action = "partial"
    elif abs(new_paid - expected) < 1:  # within 1đ tolerance
        order.payment_status = PaymentStatus.paid
        order.paid_at = datetime.utcnow()
        tx.match_status = "matched"
        action = "paid"
    else:  # overpaid
        if allow_overpay:
            order.payment_status = PaymentStatus.overpaid
            order.paid_at = datetime.utcnow()
            tx.match_status = "matched"
            action = "overpaid"
        else:
            order.payment_status = PaymentStatus.paid
            order.paid_at = datetime.utcnow()
            surplus = new_paid - expected
            order.notes = (order.notes or "") + f"\nThừa {surplus:,.0f}đ — chờ hoàn tiền."
            tx.match_status = "matched"
            action = "paid"

    order.updated_at = datetime.utcnow()
    db.add(tx)
    db.commit()
    db.refresh(order)

    return {
        "success": True,
        "action": action,
        "order_id": order.id,
        "order": order,
        "new_paid": new_paid,
        "expected": expected,
    }


# ── Process paid order (idempotent) ───────────────────────────────────────────

async def process_paid_order(order_id: int):
    """
    Background task: call API source and deliver after payment confirmed.
    Idempotent — safe to call multiple times.
    POST /buy is called exactly once per order.
    """
    from database import SessionLocal
    from services.order_service import _poll_source_order, _processing_keys
    from services.normalize import normalize_delivery_items
    from services.product_service import get_best_source
    from integrations.manager import api_manager
    from models import Product, DeliveryMode, OrderSourceAttempt

    if order_id in _processing_paid:
        logger.info(f"[payment] process_paid_order {order_id} already running")
        return
    _processing_paid.add(order_id)

    db = SessionLocal()
    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            return

        # Gate: only process pending_payment orders
        if order.status != OrderStatus.pending_payment:
            logger.info(f"[payment] order {order_id} status={order.status} — skip")
            return

        # Gate: payment must be confirmed
        if order.payment_status not in (PaymentStatus.paid, PaymentStatus.overpaid):
            logger.warning(f"[payment] order {order_id} payment_status={order.payment_status} — not ready")
            return

        # Transition to processing
        order.status = OrderStatus.processing_api
        db.commit()

        product = db.query(Product).filter(Product.id == order.product_id).first()

        # Manual delivery product → admin handles it
        if not product or product.delivery_mode != DeliveryMode.api_auto:
            order.status = OrderStatus.pending_manual
            db.commit()
            await _notify_admin_manual_needed(order, db)
            return

        source = get_best_source(db, order.product_id)
        if not source:
            order.status = OrderStatus.api_failed
            db.commit()
            await _notify_paid_api_failed(order, db, "Không tìm thấy nguồn hàng")
            return

        idem_key = order.order_code
        if idem_key in _processing_keys:
            logger.warning(f"[payment] order {idem_key} already in order_service processing")
            return
        _processing_keys.add(idem_key)

        try:
            adapter = api_manager.get_adapter(source.api_product.connection)
            buy_result = await adapter.buy_product(
                product_id=source.api_product.external_product_id,
                quantity=order.quantity,
                idempotency_key=idem_key,
            )

            attempt = OrderSourceAttempt(
                order_id=order.id,
                product_source_id=source.id,
                attempt_number=1,
                status="success" if buy_result.get("success") else "failed",
                error_message=(buy_result.get("message") or "")[:500] if not buy_result.get("success") else None,
                external_order_id=buy_result.get("order_id"),
            )
            db.add(attempt)
            db.commit()

            if not buy_result.get("success"):
                order.status = OrderStatus.api_failed
                db.commit()
                await _notify_paid_api_failed(order, db, (buy_result.get("message") or "API error")[:200])
                return

            raw_data = buy_result.get("data", {})
            items = normalize_delivery_items(raw_data)

            if not items and buy_result.get("order_id"):
                logger.info(f"[payment] polling source for {order.order_code}")
                polled_data, items = await _poll_source_order(adapter, buy_result["order_id"])
                if polled_data:
                    raw_data = polled_data

            order_data = raw_data.get("order", raw_data)
            external_order_code = (
                order_data.get("order_code") or
                order_data.get("order_id") or
                buy_result.get("order_id") or ""
            )

            if items and len(items) < order.quantity:
                order.status = OrderStatus.partial_delivery
                order.partial_count = len(items)
            elif items:
                order.status = OrderStatus.completed
            else:
                order.status = OrderStatus.pending_manual

            order.api_connection_id = source.api_product.api_connection_id
            order.external_order_id = buy_result.get("order_id")
            order.external_order_code = external_order_code
            order.source_unit_price = source.api_product.external_price
            safe_data = {k: v for k, v in raw_data.items() if k not in ("balance_after", "balance")}
            order.delivery_data = json.dumps(safe_data, ensure_ascii=False)
            order.delivery_items = json.dumps(items, ensure_ascii=False)
            order.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(order)

        finally:
            _processing_keys.discard(idem_key)

        # Deliver to user
        await _deliver_to_user(order, db)

        # Update product stats
        if product:
            product.sold_count = (product.sold_count or 0) + order.quantity
            db.commit()

    except Exception as e:
        logger.error(f"[payment] process_paid_order {order_id} error: {e}")
        try:
            order = db.query(Order).filter(Order.id == order_id).first()
            if order and order.status == OrderStatus.processing_api:
                order.status = OrderStatus.api_failed
                db.commit()
                await _notify_paid_api_failed(order, db, str(e)[:200])
        except Exception:
            pass
    finally:
        _processing_paid.discard(order_id)
        db.close()


async def _deliver_to_user(order: Order, db: Session):
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        cfg = db.query(TelegramBotConfig).first()
        support = cfg.support_username if cfg else ""
        admin_id = cfg.admin_telegram_id if cfg else ""
        bot = bot_manager._application.bot
        sv = order.status.value if hasattr(order.status, "value") else str(order.status)

        if sv == "completed":
            from bot.notifier import notify_user_delivery, notify_admin_payment_success
            await notify_user_delivery(bot, order.telegram_user_id, order, support_username=support)
            if admin_id:
                await notify_admin_payment_success(bot, order, admin_id)
        elif sv == "partial_delivery":
            items_list = json.loads(order.delivery_items) if order.delivery_items else []
            from bot.notifier import notify_admin_partial_delivery
            if admin_id:
                await notify_admin_partial_delivery(bot, order, admin_id, len(items_list))
        elif sv in ("api_failed", "pending_manual"):
            from bot.notifier import notify_user_api_failed_after_payment, notify_admin_api_failed_after_payment
            await notify_user_api_failed_after_payment(bot, order.telegram_user_id, order)
            if admin_id:
                await notify_admin_api_failed_after_payment(bot, order, admin_id)
    except Exception as e:
        logger.error(f"[payment] _deliver_to_user error: {e}")


async def _notify_paid_api_failed(order: Order, db: Session, reason: str = ""):
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        cfg = db.query(TelegramBotConfig).first()
        admin_id = cfg.admin_telegram_id if cfg else ""
        bot = bot_manager._application.bot
        from bot.notifier import notify_user_api_failed_after_payment, notify_admin_api_failed_after_payment
        await notify_user_api_failed_after_payment(bot, order.telegram_user_id, order)
        if admin_id:
            await notify_admin_api_failed_after_payment(bot, order, admin_id, reason)
    except Exception as e:
        logger.error(f"[payment] _notify_paid_api_failed error: {e}")


async def _notify_admin_manual_needed(order: Order, db: Session):
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        cfg = db.query(TelegramBotConfig).first()
        admin_id = cfg.admin_telegram_id if cfg else ""
        bot = bot_manager._application.bot
        if admin_id:
            from bot.notifier import notify_admin_new_payment_pending
            await notify_admin_new_payment_pending(bot, order, admin_id)
    except Exception as e:
        logger.error(f"[payment] _notify_admin_manual_needed error: {e}")


# ── Expiry loop ────────────────────────────────────────────────────────────────

async def expire_payment_orders_loop():
    """Background loop: every 60 s, mark overdue pending_payment orders as expired."""
    from database import SessionLocal
    while True:
        await asyncio.sleep(60)
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            expired = (
                db.query(Order)
                .filter(
                    Order.status == OrderStatus.pending_payment,
                    Order.payment_expires_at < now,
                )
                .all()
            )
            for o in expired:
                o.status = OrderStatus.payment_expired
                o.payment_status = PaymentStatus.expired
                o.updated_at = now
                logger.info(f"[payment] expired order {o.order_code}")
            if expired:
                db.commit()
        except Exception as e:
            logger.error(f"[payment] expiry loop error: {e}")
        finally:
            db.close()
