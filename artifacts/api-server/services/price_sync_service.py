"""
Auto price-adjustment ("giữ nguyên phần chênh lệch") when a linked
supplier/source price changes.

Core idea: each Product stores its own price_margin = sale_price -
source_price as a persisted snapshot (not derived live). When the source
price changes (detected during an API sync, or a manual "re-check price"
action), and auto_adjust_price is on, the sale price is recomputed as
new_source_price + price_margin so the admin's markup survives supplier
price moves untouched. If auto_adjust_price is off, the source price is
still recorded (so history/notifications stay accurate) but sale_price is
left alone.

A per-product `require_admin_approval_above_percent` guard can hold back
unusually large increases (e.g. a supplier API glitch) for manual admin
approval instead of auto-applying them.

All VND amounts are handled as Python floats rounded to whole VND via
round(x) at the point of storage/comparison — this project stores VND as
Float elsewhere (see models.py), so this service follows the same
convention rather than introducing Decimal in one isolated place.
"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from models import Product, ProductPriceHistory, TelegramBotConfig

logger = logging.getLogger(__name__)

# Two float source prices are treated as "unchanged" if they differ by less
# than this many VND — avoids float-rounding noise re-triggering a sync.
_PRICE_EPSILON = 0.5


def _round_vnd(value: float | None) -> float | None:
    if value is None:
        return None
    return float(round(value))


def _get_bot_config(db: Session):
    return db.query(TelegramBotConfig).first()


def compute_margin(sale_price: float, source_price: float) -> float:
    return _round_vnd((sale_price or 0.0) - (source_price or 0.0))


def _clamp_sale_price(product: Product, sale_price: float) -> float:
    if product.min_sale_price is not None and sale_price < product.min_sale_price:
        sale_price = product.min_sale_price
    if product.max_sale_price is not None and sale_price > product.max_sale_price:
        sale_price = product.max_sale_price
    return _round_vnd(sale_price)


def record_price_history(
    db: Session,
    product: Product,
    old_source_price,
    new_source_price,
    old_sale_price,
    new_sale_price,
    change_type: str,
    source_connection_id: int | None = None,
    changed_by: str | None = None,
):
    entry = ProductPriceHistory(
        product_id=product.id,
        source_connection_id=source_connection_id,
        old_source_price=old_source_price,
        new_source_price=new_source_price,
        old_sale_price=old_sale_price,
        new_sale_price=new_sale_price,
        margin=product.price_margin,
        change_type=change_type,
        changed_by=changed_by,
        created_at=datetime.utcnow(),
    )
    db.add(entry)


def apply_admin_price_edit(db: Session, product: Product, new_source_price: float, new_sale_price: float, changed_by: str | None = None) -> bool:
    """
    Apply admin-submitted source_price/sale_price from the product add/edit
    form. Always recomputes price_margin from the submitted values (matches
    spec §2: "Nếu admin sửa giá bán thủ công: cập nhật lại price_margin").
    Returns True if either value actually changed (used to decide whether to
    log a product_price_history row).
    """
    new_source_price = _round_vnd(new_source_price) or 0.0
    new_sale_price = _round_vnd(new_sale_price) or 0.0
    old_source_price = product.source_price
    old_sale_price = product.sale_price
    changed = (
        _round_vnd(old_source_price) != new_source_price
        or _round_vnd(old_sale_price) != new_sale_price
    )
    product.source_price = new_source_price
    product.sale_price = new_sale_price
    product.price_margin = compute_margin(new_sale_price, new_source_price)
    if changed:
        record_price_history(
            db, product, old_source_price, new_source_price,
            old_sale_price, new_sale_price, "admin_edit", changed_by=changed_by,
        )
    return changed


async def handle_source_price_change(
    db: Session,
    product: Product,
    new_source_price: float,
    source_connection_id: int | None = None,
    exchange_rate: float | None = None,
) -> dict:
    """
    Called on every sync tick for a product's PRIMARY active source (see
    services/api_service.py). Compares against the previously known
    product.source_price and, if it moved, either auto-adjusts sale_price
    (preserving price_margin) or parks the change for admin approval when it
    exceeds require_admin_approval_above_percent.

    Returns a dict describing what happened: {"action": "noop"|"applied"|
    "pending_approval"|"disabled", ...}. Never raises — callers should still
    wrap in try/except since it does network-free but DB + notification work.
    """
    from services.normalize import compute_price_usdt
    from services.notification_events import claim_event

    if new_source_price is None:
        return {"action": "noop", "reason": "no_source_price"}
    new_source_price = _round_vnd(new_source_price)
    old_source_price = _round_vnd(product.source_price)

    if old_source_price is not None and abs(new_source_price - old_source_price) < _PRICE_EPSILON:
        return {"action": "noop", "reason": "unchanged"}

    # First time we ever see a source price for this product: just record it
    # and (if auto_adjust_price is already on with no margin yet) initialize
    # the margin from the CURRENT sale price, per spec §2 — never silently
    # change sale_price on this first observation.
    if old_source_price is None:
        product.source_price = new_source_price
        if product.auto_adjust_price and product.price_margin is None:
            product.price_margin = compute_margin(product.sale_price, new_source_price)
        product.last_price_updated_at = datetime.utcnow()
        db.commit()
        return {"action": "initialized", "new_source_price": new_source_price}

    percent_change = None
    if old_source_price > 0:
        percent_change = (new_source_price - old_source_price) / old_source_price * 100.0

    threshold = product.require_admin_approval_above_percent
    if threshold is not None and percent_change is not None and percent_change > threshold:
        event_key = f"price_pending:{product.id}:{round(new_source_price)}"
        claimed = claim_event(db, event_key, "price_pending_approval", product_id=product.id, source_id=source_connection_id)
        product.price_pending_approval = True
        product.pending_new_source_price = new_source_price
        db.commit()
        if claimed:
            await notify_admin_price_surge_pending(db, product, old_source_price, new_source_price, percent_change)
        return {"action": "pending_approval", "new_source_price": new_source_price, "percent_change": percent_change}

    return await _apply_source_price_change(
        db, product, old_source_price, new_source_price,
        source_connection_id=source_connection_id, exchange_rate=exchange_rate, change_type="source_sync",
    )


async def _apply_source_price_change(
    db: Session,
    product: Product,
    old_source_price: float,
    new_source_price: float,
    source_connection_id: int | None = None,
    exchange_rate: float | None = None,
    change_type: str = "source_sync",
) -> dict:
    from services.normalize import compute_price_usdt

    old_sale_price = product.sale_price
    product.last_source_price = old_source_price
    product.last_sale_price = old_sale_price
    product.source_price = new_source_price
    product.last_price_updated_at = datetime.utcnow()

    new_sale_price = old_sale_price
    sale_price_changed = False
    if product.auto_adjust_price:
        margin = product.price_margin if product.price_margin is not None else compute_margin(old_sale_price, old_source_price)
        product.price_margin = margin
        computed = new_source_price + margin
        clamped = _clamp_sale_price(product, computed)
        if _round_vnd(clamped) != _round_vnd(old_sale_price):
            product.sale_price = clamped
            if exchange_rate:
                product.price_usdt = compute_price_usdt(clamped, exchange_rate)
            new_sale_price = clamped
            sale_price_changed = True

    record_price_history(
        db, product, old_source_price, new_source_price, old_sale_price, new_sale_price,
        change_type=("auto_adjust" if (change_type == "source_sync" and product.auto_adjust_price) else change_type),
        source_connection_id=source_connection_id,
    )
    db.commit()

    await notify_admin_source_price_changed(db, product, old_source_price, new_source_price, old_sale_price, new_sale_price)

    if sale_price_changed:
        cfg = _get_bot_config(db)
        if cfg and getattr(cfg, "notify_users_on_price_change", False):
            try:
                await notify_users_price_changed(db, product, new_sale_price)
            except Exception as e:
                logger.error(f"[price_sync] user price-change notify failed for product {product.id}: {e}")

    return {
        "action": "applied",
        "old_source_price": old_source_price,
        "new_source_price": new_source_price,
        "old_sale_price": old_sale_price,
        "new_sale_price": new_sale_price,
        "sale_price_changed": sale_price_changed,
    }


async def approve_pending_price(db: Session, product: Product, exchange_rate: float | None = None, changed_by: str | None = None) -> dict:
    """Admin approves a parked price surge: apply it now via the normal
    auto-adjust path (still respects auto_adjust_price + clamps)."""
    if not product.price_pending_approval or product.pending_new_source_price is None:
        return {"action": "noop", "reason": "no_pending_price"}
    old_source_price = _round_vnd(product.source_price)
    new_source_price = _round_vnd(product.pending_new_source_price)
    product.price_pending_approval = False
    product.pending_new_source_price = None
    result = await _apply_source_price_change(
        db, product, old_source_price, new_source_price,
        exchange_rate=exchange_rate, change_type="manual_override",
    )
    return result


def reject_pending_price(db: Session, product: Product) -> dict:
    """Admin rejects a parked price surge: dismiss it without changing
    source_price/sale_price. Kept out of history — nothing was applied."""
    if not product.price_pending_approval:
        return {"action": "noop"}
    product.price_pending_approval = False
    product.pending_new_source_price = None
    db.commit()
    return {"action": "rejected"}


# ── Admin notifications ──────────────────────────────────────────────────────

def _fmt(v) -> str:
    from services.normalize import format_vnd
    if v is None:
        return "—"
    return f"{format_vnd(v)}đ"


async def notify_admin_source_price_changed(db: Session, product: Product, old_source_price, new_source_price, old_sale_price, new_sale_price):
    from services.bot_service import bot_manager
    cfg = _get_bot_config(db)
    admin_id = cfg.admin_telegram_id if cfg else None
    if not admin_id or not bot_manager.is_running():
        return
    if product.auto_adjust_price:
        text = (
            "⚠️ GIÁ NGUỒN ĐÃ THAY ĐỔI\n\n"
            f"📦 Sản phẩm: {product.name}\n"
            f"🏦 Giá nguồn cũ: {_fmt(old_source_price)}\n"
            f"🏦 Giá nguồn mới: {_fmt(new_source_price)}\n"
            f"📈 Chênh lệch giữ nguyên: {_fmt(product.price_margin)}\n"
            f"💰 Giá bán cũ: {_fmt(old_sale_price)}\n"
            f"💰 Giá bán mới: {_fmt(new_sale_price)}"
        )
    else:
        text = (
            "⚠️ GIÁ NGUỒN ĐÃ THAY ĐỔI\n\n"
            f"📦 Sản phẩm: {product.name}\n"
            f"🏦 Giá nguồn cũ: {_fmt(old_source_price)}\n"
            f"🏦 Giá nguồn mới: {_fmt(new_source_price)}\n"
            f"💰 Giá bán hiện tại vẫn giữ nguyên: {_fmt(old_sale_price)}\n"
            "⛔ Tự động điều chỉnh giá đang tắt."
        )
    try:
        await bot_manager.send_message(admin_id, text)
    except Exception as e:
        logger.error(f"[price_sync] admin notify failed for product {product.id}: {e}")


async def notify_admin_price_surge_pending(db: Session, product: Product, old_source_price, new_source_price, percent_change: float):
    from services.bot_service import bot_manager
    cfg = _get_bot_config(db)
    admin_id = cfg.admin_telegram_id if cfg else None
    if not admin_id or not bot_manager.is_running():
        return
    text = (
        "🚨 GIÁ NGUỒN TĂNG BẤT THƯỜNG — CẦN DUYỆT\n\n"
        f"📦 Sản phẩm: {product.name}\n"
        f"🏦 Giá nguồn cũ: {_fmt(old_source_price)}\n"
        f"🏦 Giá nguồn mới: {_fmt(new_source_price)} (+{percent_change:.0f}%)\n"
        "⛔ Vượt ngưỡng cho phép — chưa tự áp dụng.\n"
        "Vào trang sản phẩm trên web admin để duyệt hoặc từ chối."
    )
    try:
        await bot_manager.send_message(admin_id, text)
    except Exception as e:
        logger.error(f"[price_sync] admin pending-approval notify failed for product {product.id}: {e}")


async def notify_users_price_changed(db: Session, product: Product, new_sale_price: float):
    """Optional customer-facing broadcast, gated on
    TelegramBotConfig.notify_users_on_price_change. Reuses the same
    "text + 🛒 Mua ngay" broadcast helper used for new-product/restock
    announcements."""
    from services.broadcast_service import _broadcast_message_with_buy_button
    from services.normalize import format_vnd
    icon = product.telegram_icon or "📦"
    text = (
        "💰 GIÁ SẢN PHẨM ĐÃ CẬP NHẬT\n\n"
        f"{icon} {product.name}\n"
        f"Giá mới: {format_vnd(new_sale_price)}đ"
    )
    await _broadcast_message_with_buy_button(db, {"vi": text, "en": text}, product.id)
