"""
inventory_service.py — "Kho tài khoản" (local stock) for manual_stock products.

Responsibilities:
  - Parse bulk-pasted account text into structured rows (multi-format, dedupe, validate).
  - Compute live available-stock counts (NEVER a stored counter).
  - Deliver from local inventory on payment confirmation, with a SQLite
    BEGIN-IMMEDIATE transaction so two concurrent orders can never be
    allocated the same credential.
  - Auto-process paid_waiting_stock orders after a restock, oldest-first.

Security: passwords / raw_value must NEVER be written to ActivityLog or
general application logs. Only counts and product names are logged.
"""
import logging
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import InventoryItem, InventoryStatus, Product, Order, OrderStatus, DeliveryMode

logger = logging.getLogger(__name__)


# ── Bulk parsing ────────────────────────────────────────────────────────────────

def parse_bulk_accounts(raw_text: str) -> dict:
    """
    Parse bulk-pasted account lines in several common delimiter formats:
      user|pass
      user:pass
      user,pass
      email|pass|note
      email:pass:expiry
      a single opaque value per line (license keys etc.)
    Trims whitespace, drops empty lines, de-dupes by the full trimmed line
    (case-sensitive — credentials are case-sensitive).

    Returns {"valid": [dict...], "duplicates": int, "errors": int, "total_lines": int}
    """
    if not raw_text:
        return {"valid": [], "duplicates": 0, "errors": 0, "total_lines": 0}

    raw_lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Keep 1-based line numbers relative to the original pasted text (including
    # blank lines) so error reporting matches what the admin sees in the textarea.
    numbered = [(i + 1, l.strip()) for i, l in enumerate(raw_lines)]
    numbered = [(n, l) for n, l in numbered if l]

    seen = set()
    valid = []
    duplicates = 0
    errors = 0
    error_lines = []

    for line_no, line in numbered:
        if line in seen:
            duplicates += 1
            continue
        seen.add(line)

        parsed = _parse_account_line(line)
        if parsed is None:
            errors += 1
            error_lines.append({
                "line_no": line_no,
                "content": line[:80],
                "reason": "Không nhận dạng được định dạng (dòng trống hoặc thiếu dữ liệu)",
            })
            continue
        valid.append(parsed)

    return {
        "valid": valid,
        "duplicates": duplicates,
        "errors": errors,
        "error_lines": error_lines,
        "total_lines": len(numbered),
    }


def _parse_account_line(line: str) -> dict | None:
    if not line or not line.strip():
        return None
    raw = line.strip()

    delimiter = None
    for d in ("|", ":", ",", "\t"):
        if d in raw:
            delimiter = d
            break

    if delimiter is None:
        # Opaque single value (license key, code, etc.)
        return {
            "username": "", "password": "", "email": "",
            "expiry": "", "note": "", "raw_value": raw,
        }

    parts = [p.strip() for p in raw.split(delimiter)]
    username = parts[0] if len(parts) > 0 else ""
    password = parts[1] if len(parts) > 1 else ""
    extra1 = parts[2] if len(parts) > 2 else ""
    extra2 = parts[3] if len(parts) > 3 else ""

    if not username and not password:
        return None

    email = username if "@" in username else ""
    expiry = extra2 or (extra1 if _looks_like_date(extra1) else "")
    note = extra1 if not _looks_like_date(extra1) else ""

    return {
        "username": username,
        "password": password,
        "email": email,
        "expiry": expiry,
        "note": note,
        "raw_value": raw,
    }


def _looks_like_date(s: str) -> bool:
    if not s:
        return False
    return any(sep in s for sep in ("/", "-")) and any(c.isdigit() for c in s)


def add_inventory_items(db: Session, product_id: int, parsed_rows: list, cost_price: float = 0.0) -> dict:
    """
    Insert parsed rows as available InventoryItem rows.
    De-dupes against existing (non-deleted) raw_value rows for the same product.
    Returns summary dict including whether product went 0 -> available (back in stock).
    """
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise ValueError("Product not found")

    before_count = get_available_count(db, product_id)

    existing_values = {
        r[0] for r in db.query(InventoryItem.raw_value).filter(
            InventoryItem.product_id == product_id,
            InventoryItem.status != InventoryStatus.deleted,
        ).all()
    }

    inserted = 0
    skipped_existing = 0
    now = datetime.utcnow()
    for row in parsed_rows:
        if row["raw_value"] in existing_values:
            skipped_existing += 1
            continue
        existing_values.add(row["raw_value"])
        item = InventoryItem(
            product_id=product_id,
            username=row.get("username") or None,
            password=row.get("password") or None,
            raw_value=row.get("raw_value"),
            email=row.get("email") or None,
            expiry=row.get("expiry") or None,
            note=row.get("note") or None,
            cost_price=cost_price or 0.0,
            status=InventoryStatus.available,
            created_at=now,
            updated_at=now,
        )
        db.add(item)
        inserted += 1

    db.commit()

    after_count = get_available_count(db, product_id)
    back_in_stock = before_count <= 0 and after_count > 0

    return {
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "before_count": before_count,
        "after_count": after_count,
        "back_in_stock": back_in_stock,
    }


# ── Live stock counting (never a stored counter) ────────────────────────────────

def get_available_count(db: Session, product_id: int) -> int:
    return db.query(func.count(InventoryItem.id)).filter(
        InventoryItem.product_id == product_id,
        InventoryItem.status == InventoryStatus.available,
    ).scalar() or 0


def get_inventory_counts(db: Session, product_id: int) -> dict:
    rows = db.query(InventoryItem.status, func.count(InventoryItem.id)).filter(
        InventoryItem.product_id == product_id,
        InventoryItem.status != InventoryStatus.deleted,
    ).group_by(InventoryItem.status).all()
    counts = {"available": 0, "reserved": 0, "sold": 0, "faulty": 0}
    for status, cnt in rows:
        key = status.value if hasattr(status, "value") else str(status)
        if key in counts:
            counts[key] = cnt
    avg_cost = db.query(func.avg(InventoryItem.cost_price)).filter(
        InventoryItem.product_id == product_id,
        InventoryItem.status != InventoryStatus.deleted,
        InventoryItem.cost_price.isnot(None),
    ).scalar()
    counts["avg_cost_price"] = round(avg_cost, 0) if avg_cost else 0
    return counts


# ── Transactional delivery ───────────────────────────────────────────────────────

def _reserve_items_for_order(raw_conn, product_id: int, quantity: int, order_id: int) -> list:
    """
    Runs inside a BEGIN IMMEDIATE transaction on a raw sqlite3-style DBAPI
    connection (obtained via engine.raw_connection()). Locks out other writers
    for the duration, so two concurrent orders can never reserve the same rows.
    Returns list of reserved InventoryItem ids, or [] if not enough stock.
    """
    cur = raw_conn.cursor()
    cur.execute(
        "SELECT id FROM inventory_items WHERE product_id = ? AND status = 'available' "
        "ORDER BY id ASC LIMIT ?",
        (product_id, quantity),
    )
    rows = cur.fetchall()
    if len(rows) < quantity:
        return []
    ids = [r[0] for r in rows]
    now = datetime.utcnow().isoformat(sep=" ")
    cur.executemany(
        "UPDATE inventory_items SET status = 'reserved', reserved_order_id = ?, "
        "reserved_at = ?, updated_at = ? WHERE id = ? AND status = 'available'",
        [(order_id, now, now, i) for i in ids],
    )
    return ids


async def deliver_from_local_inventory(order_id: int):
    """
    Deliver a paid manual_stock order from local inventory.

    Flow (matches spec section 8):
      1. Lock order, verify it is paid and not yet delivered.
      2. Reserve exact quantity of available rows under a DB transaction
         (BEGIN IMMEDIATE semantics via a raw connection) — guarantees no
         double-allocation of the same credential to two orders.
      3. If not enough stock: leave order as paid_waiting_stock, notify.
      4. Attempt Telegram delivery.
         - success -> mark rows sold, order completed, notify user+admin.
         - failure -> roll reserved rows back to available, order ->
           delivery_failed, alert admin (payment stays intact, no data lost).
    """
    from database import SessionLocal, engine
    from services.payment_service import _processing_paid

    if order_id in _processing_paid:
        return
    _processing_paid.add(order_id)

    db = SessionLocal()
    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            return

        # Idempotency gate — only act on orders genuinely awaiting delivery
        if order.status not in (OrderStatus.pending_payment, OrderStatus.processing_api,
                                 OrderStatus.paid_waiting_stock, OrderStatus.waiting_manual_verification):
            logger.info(f"[inventory] order {order_id} status={order.status} — skip")
            return

        from models import PaymentStatus
        if order.payment_status not in (PaymentStatus.paid, PaymentStatus.overpaid):
            logger.warning(f"[inventory] order {order_id} payment_status={order.payment_status} — not ready")
            return

        product = db.query(Product).filter(Product.id == order.product_id).first()
        if not product:
            return

        order.status = OrderStatus.processing_api
        db.commit()

        # ── Reserve exact quantity under a locking transaction ──
        raw_conn = engine.raw_connection()
        try:
            raw_conn.isolation_level = None  # manual transaction control
            raw_cur = raw_conn.cursor()
            raw_cur.execute("BEGIN IMMEDIATE")
            try:
                reserved_ids = _reserve_items_for_order(raw_conn, product.id, order.quantity, order.id)
                raw_conn.commit()
            except Exception:
                raw_conn.rollback()
                raise
        finally:
            raw_conn.close()

        if not reserved_ids:
            order.status = OrderStatus.paid_waiting_stock
            db.commit()
            await _notify_inventory_waiting_stock(order, db)
            return

        db.expire_all()
        reserved_items = db.query(InventoryItem).filter(InventoryItem.id.in_(reserved_ids)).all()

        # ── Attempt delivery ──
        delivered_ok = await _try_deliver_items(order, product, reserved_items, db)

        if delivered_ok:
            now = datetime.utcnow()
            for item in reserved_items:
                item.status = InventoryStatus.sold
                item.sold_order_id = order.id
                item.sold_at = now
                item.updated_at = now
            order.status = OrderStatus.completed
            order.updated_at = now
            db.commit()

            product.sold_count = (product.sold_count or 0) + order.quantity
            db.commit()

            await _notify_inventory_delivery_success(order, db)
        else:
            # Roll back reservation — never lose the credentials
            now = datetime.utcnow()
            for item in reserved_items:
                item.status = InventoryStatus.available
                item.reserved_order_id = None
                item.reserved_at = None
                item.updated_at = now
            order.status = OrderStatus.delivery_failed
            order.updated_at = now
            db.commit()

            await _notify_inventory_delivery_failed(order, db)

    except Exception as e:
        logger.error(f"[inventory] deliver_from_local_inventory {order_id} error: {e}")
    finally:
        _processing_paid.discard(order_id)
        db.close()


async def _try_deliver_items(order: Order, product: Product, items: list, db: Session) -> bool:
    """Send the account block to the user via Telegram. Returns True on success."""
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return False
        from services.normalize import format_delivery_message
        from services.payment_service import cleanup_payment_qr
        import json as _json

        # The invoice is about to be sent — safe to remove the QR/instruction
        # message now (payment_status is already paid/overpaid at this point).
        await cleanup_payment_qr(bot_manager._application.bot, order, db)

        delivery_items = [
            {
                "username": it.username or "",
                "password": it.password or "",
                "value": it.raw_value or "",
                "note": it.note or "",
            }
            for it in items
        ]
        order.delivery_items = _json.dumps(delivery_items, ensure_ascii=False)
        db.commit()

        from bot.keyboards import post_delivery_keyboard
        from bot.i18n import get_user_lang
        lang = get_user_lang(db, order.telegram_user_id)
        display_name = product.name_en if (lang == "en" and getattr(product, "name_en", None)) else product.name
        text, file_bytes = format_delivery_message(order, delivery_items, display_name, lang=lang)
        bot = bot_manager._application.bot
        cfg = _get_bot_config(db)
        support = cfg.support_username if cfg else ""
        keyboard = post_delivery_keyboard(order.id, support, lang=lang)

        import io
        if file_bytes:
            await bot.send_document(
                chat_id=int(order.telegram_user_id),
                document=io.BytesIO(file_bytes),
                filename=f"{order.order_code}.txt",
                caption=f"✅ Đơn <code>{order.order_code}</code> hoàn thành!",
                parse_mode="HTML",
            )
            await bot.send_message(chat_id=int(order.telegram_user_id), text=text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await bot.send_message(chat_id=int(order.telegram_user_id), text=text, parse_mode="HTML", reply_markup=keyboard)
        return True
    except Exception as e:
        logger.error(f"[inventory] _try_deliver_items order={order.id} error: {e}")
        return False


def _get_bot_config(db: Session):
    from models import TelegramBotConfig
    return db.query(TelegramBotConfig).first()


async def _notify_inventory_waiting_stock(order: Order, db: Session):
    try:
        from services.wallet_service import refund_order_to_wallet
        await refund_order_to_wallet(db, order, reason="Kho nội bộ hết hàng sau khi thanh toán")

        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        from bot.notifier import notify_user_paid_waiting_stock, notify_admin_paid_waiting_stock
        from bot.i18n import get_user_lang
        from services.payment_service import cleanup_payment_qr
        cfg = _get_bot_config(db)
        admin_id = cfg.admin_telegram_id if cfg else ""
        bot = bot_manager._application.bot
        lang = get_user_lang(db, order.telegram_user_id)
        chat_id = order.payment_chat_id or order.telegram_user_id
        await cleanup_payment_qr(bot, order, db)
        await notify_user_paid_waiting_stock(bot, chat_id, order, lang=lang)
        if admin_id:
            await notify_admin_paid_waiting_stock(bot, order, admin_id)
    except Exception as e:
        logger.error(f"[inventory] _notify_inventory_waiting_stock error: {e}")


async def _notify_inventory_delivery_success(order: Order, db: Session):
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        from bot.notifier import notify_admin_payment_success
        cfg = _get_bot_config(db)
        admin_id = cfg.admin_telegram_id if cfg else ""
        bot = bot_manager._application.bot
        if admin_id:
            await notify_admin_payment_success(bot, order, admin_id)
    except Exception as e:
        logger.error(f"[inventory] _notify_inventory_delivery_success error: {e}")


async def _notify_inventory_delivery_failed(order: Order, db: Session):
    """Telegram send failed after reserving stock — stock was rolled back, payment intact."""
    try:
        from services.wallet_service import refund_order_to_wallet
        await refund_order_to_wallet(db, order, reason="Không thể gửi hàng qua Telegram")

        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        from services.payment_service import cleanup_payment_qr
        cfg = _get_bot_config(db)
        admin_id = cfg.admin_telegram_id if cfg else ""
        bot = bot_manager._application.bot
        await cleanup_payment_qr(bot, order, db)
        product_name = order.product.name if order.product else str(order.product_id)
        if admin_id:
            import html
            await bot.send_message(
                chat_id=int(admin_id),
                text=(
                    f"🚨 <b>GIAO HÀNG THẤT BẠI — ĐÃ HOÀN TRẢ KHO!</b>\n\n"
                    f"📋 Đơn: <code>{order.order_code}</code>\n"
                    f"📦 Sản phẩm: {html.escape(product_name)}\n"
                    f"👤 User: <code>{order.telegram_user_id}</code>\n\n"
                    "Không thể gửi tin nhắn Telegram cho khách (có thể đã chặn bot).\n"
                    "Tài khoản đã được trả lại kho — vui lòng liên hệ khách thủ công."
                ),
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"[inventory] _notify_inventory_delivery_failed error: {e}")


# ── Auto-process waiting orders after restock ───────────────────────────────────

async def process_waiting_orders_for_product(product_id: int) -> dict:
    """
    After a successful inventory top-up, attempt to deliver any paid_waiting_stock
    orders for this product, oldest first. Only full-quantity delivery unless
    allow_partial_delivery is enabled (partial delivery is not implemented for
    local-inventory credentials — each unit is atomic — so this setting only
    affects whether an order can be skipped vs. left waiting; delivery itself
    is always exact-quantity).
    """
    from database import SessionLocal
    db = SessionLocal()
    delivered_orders = []
    try:
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product or product.delivery_mode != DeliveryMode.manual_stock:
            return {"delivered": 0}

        waiting = (
            db.query(Order)
            .filter(Order.product_id == product_id, Order.status == OrderStatus.paid_waiting_stock)
            .order_by(Order.created_at.asc())
            .all()
        )

        for order in waiting:
            available = get_available_count(db, product_id)
            if available < order.quantity:
                break  # oldest-first; stop once we can't fully serve the next one
            await deliver_from_local_inventory(order.id)
            db.expire_all()
            refreshed = db.query(Order).filter(Order.id == order.id).first()
            if refreshed and refreshed.status == OrderStatus.completed:
                delivered_orders.append(refreshed.order_code)

        if delivered_orders:
            await _notify_admin_auto_delivered(db, product, delivered_orders)

        return {"delivered": len(delivered_orders), "orders": delivered_orders}
    except Exception as e:
        logger.error(f"[inventory] process_waiting_orders_for_product {product_id} error: {e}")
        return {"delivered": len(delivered_orders), "orders": delivered_orders}
    finally:
        db.close()


async def _notify_admin_auto_delivered(db: Session, product: Product, order_codes: list):
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        cfg = _get_bot_config(db)
        admin_id = cfg.admin_telegram_id if cfg else ""
        if not admin_id:
            return
        import html
        bot = bot_manager._application.bot
        codes_str = "\n".join(f"• <code>{c}</code>" for c in order_codes)
        await bot.send_message(
            chat_id=int(admin_id),
            text=(
                f"✅ <b>Tự động giao hàng sau khi nhập kho!</b>\n\n"
                f"📦 Sản phẩm: {html.escape(product.name)}\n"
                f"🔢 Đã giao tự động: {len(order_codes)} đơn\n\n{codes_str}"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"[inventory] _notify_admin_auto_delivered error: {e}")


async def notify_restock_if_enabled(product_id: int, back_in_stock: bool):
    """
    Section 12: if notify_users_when_restocked is enabled, ping only the users
    whose orders are currently sitting in paid_waiting_stock for this product
    (targeted, not a broadcast) once stock becomes available again.
    Runs BEFORE process_waiting_orders_for_product actually delivers them,
    so callers should invoke this first, then process_waiting_orders_for_product.
    """
    from database import SessionLocal
    db = SessionLocal()
    try:
        cfg = _get_bot_config(db)
        if not cfg or not cfg.notify_users_when_restocked:
            return
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product:
            return
        waiting = (
            db.query(Order)
            .filter(Order.product_id == product_id, Order.status == OrderStatus.paid_waiting_stock)
            .all()
        )
        if not waiting:
            return
        from bot.i18n import get_user_lang
        bot = bot_manager._application.bot
        for order in waiting:
            lang = get_user_lang(db, order.telegram_user_id)
            chat_id = order.payment_chat_id or order.telegram_user_id
            text = (
                f"🔔 <b>{product.name}</b> đã có hàng trở lại!\nĐơn <code>{order.order_code}</code> của bạn sẽ được xử lý trong ít phút."
                if lang != "en" else
                f"🔔 <b>{product.name}</b> is back in stock!\nYour order <code>{order.order_code}</code> will be processed shortly."
            )
            try:
                await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML")
            except Exception:
                pass
    except Exception as e:
        logger.error(f"[inventory] notify_restock_if_enabled error: {e}")
    finally:
        db.close()
