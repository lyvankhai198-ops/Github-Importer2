"""
Admin -> all bot users broadcast (dashboard "📢 Thông báo Bot" page).
Sends a title + content message, optionally with an image, to every
non-banned bot user, and reports back sent/failed counts.
"""
import asyncio
import logging
import html

from sqlalchemy.orm import Session
from models import User, TelegramBotConfig

logger = logging.getLogger(__name__)


async def send_broadcast(db: Session, title: str, content: str, image_path: str | None = None) -> dict:
    from telegram.error import Forbidden
    from services.bot_service import bot_manager
    if not bot_manager.is_running():
        return {"sent": 0, "failed": 0, "blocked": 0, "total": 0, "error": "Bot chưa chạy — vui lòng bật bot trước khi gửi thông báo."}

    # Only active users: not admin-banned, and not already known to have
    # blocked the bot in Telegram (is_blocked, set automatically below).
    users = db.query(User).filter(User.is_banned == False, User.is_blocked == False).all()
    total = len(users)
    sent = 0
    failed = 0
    blocked = 0

    bot = bot_manager._application.bot
    text = f"📢 <b>{html.escape(title)}</b>\n\n{html.escape(content)}"

    photo_source = None
    if image_path:
        if image_path.startswith("/uploads/"):
            from config import UPLOADS_DIR
            fpath = UPLOADS_DIR / image_path.split("/uploads/", 1)[1]
            if fpath.exists():
                photo_source = fpath.read_bytes()
        else:
            photo_source = image_path  # external URL

    for user in users:
        try:
            if photo_source:
                await bot.send_photo(chat_id=int(user.telegram_id), photo=photo_source, caption=text, parse_mode="HTML")
            else:
                await bot.send_message(chat_id=int(user.telegram_id), text=text, parse_mode="HTML")
            sent += 1
        except Forbidden:
            # User blocked the bot (or deleted their account) — stop
            # broadcasting to them going forward until they unblock it.
            failed += 1
            blocked += 1
            user.is_blocked = True
            db.commit()
            logger.warning(f"[broadcast] user {user.telegram_id} has blocked the bot — marked is_blocked")
        except Exception as e:
            failed += 1
            logger.error(f"[broadcast] send failed for user {user.telegram_id}: {e}")

    logger.info(f"BROADCAST_SENT: total={total} sent={sent} failed={failed} blocked={blocked}")
    return {"sent": sent, "failed": failed, "blocked": blocked, "total": total, "error": None}


# ── Auto "new product" / "restock" announcements ─────────────────────────────
# Distinct from send_broadcast above (admin-authored, on-demand): these fire
# automatically on real product events, are gated on their own settings
# toggles, and always include a "🛒 Mua ngay" button that re-checks stock via
# the normal "product:{id}" callback — never creates an order directly.

def _get_bot_config(db: Session):
    return db.query(TelegramBotConfig).first()


async def _broadcast_message_with_buy_button(db: Session, text: str, product_id: int) -> dict:
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    from telegram.error import Forbidden
    from services.bot_service import bot_manager

    if not bot_manager.is_running():
        return {"sent": 0, "failed": 0, "blocked": 0, "total": 0, "skipped": True}

    cfg = _get_bot_config(db)
    batch_size = max(1, (cfg.broadcast_batch_size if cfg and cfg.broadcast_batch_size else 25))
    delay_ms = max(0, (cfg.broadcast_delay_ms if cfg and cfg.broadcast_delay_ms is not None else 300))

    users = db.query(User).filter(User.is_banned == False, User.is_blocked == False).all()
    total = len(users)
    sent = 0
    failed = 0
    blocked = 0

    bot = bot_manager._application.bot
    kbd = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Mua ngay", callback_data=f"product:{product_id}")]])

    for i in range(0, total, batch_size):
        batch = users[i:i + batch_size]
        for user in batch:
            try:
                await bot.send_message(chat_id=int(user.telegram_id), text=text, parse_mode="HTML", reply_markup=kbd)
                sent += 1
            except Forbidden:
                # A user who has blocked the bot must not stop the batch —
                # mark them inactive and keep going.
                failed += 1
                blocked += 1
                user.is_blocked = True
                db.commit()
            except Exception as e:
                failed += 1
                logger.error(f"[product_notify] send failed for user {user.telegram_id}: {e}")
        if i + batch_size < total and delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

    logger.info(f"PRODUCT_NOTIFY_SENT: product_id={product_id} total={total} sent={sent} failed={failed} blocked={blocked}")
    return {"sent": sent, "failed": failed, "blocked": blocked, "total": total, "skipped": False}


async def notify_new_product_broadcast(product) -> dict:
    """
    Broadcast a "🆕 SẢN PHẨM MỚI" announcement to all active users when a
    genuinely new Product becomes visible (admin manual add, or admin
    creating a product from a freshly-synced API source item). Gated on
    TelegramBotConfig.notify_new_products.
    """
    from database import SessionLocal
    db = SessionLocal()
    try:
        cfg = _get_bot_config(db)
        if not cfg or not getattr(cfg, "notify_new_products", True):
            return {"skipped": True}
        from services.product_service import get_product_stock_status
        from services.normalize import format_vnd

        info = get_product_stock_status(product.id, db)
        icon = product.telegram_icon or "📦"
        lines = [
            "🆕 <b>SẢN PHẨM MỚI</b>",
            "",
            f"{icon} <b>{html.escape(product.name)}</b>",
            f"💰 Giá: {format_vnd(product.sale_price)}đ",
        ]
        if info["status"] != "unavailable":
            lines.append(f"📦 Tồn kho: {info['stock']}")
        return await _broadcast_message_with_buy_button(db, "\n".join(lines), product.id)
    finally:
        db.close()


async def notify_restock_broadcast(product_id: int, added_qty: int, new_total: int) -> dict:
    """
    Broadcast a "🔄 ĐÃ BỔ SUNG HÀNG" announcement to all active users when a
    product's total stock genuinely increases (never on decreases from
    purchases, never on an unchanged sync). Gated on
    TelegramBotConfig.notify_restock.
    """
    from database import SessionLocal
    from models import Product
    db = SessionLocal()
    try:
        cfg = _get_bot_config(db)
        if not cfg or not getattr(cfg, "notify_restock", True):
            return {"skipped": True}
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product or not product.is_active:
            return {"skipped": True}
        from services.normalize import format_vnd

        icon = product.telegram_icon or "📦"
        lines = [
            "🔄 <b>ĐÃ BỔ SUNG HÀNG</b>",
            "",
            f"{icon} <b>{html.escape(product.name)}</b>",
            f"➕ Số lượng vừa thêm: {added_qty}",
            f"📦 Tồn kho hiện tại: {new_total}",
            f"💰 Giá: {format_vnd(product.sale_price)}đ",
        ]
        return await _broadcast_message_with_buy_button(db, "\n".join(lines), product.id)
    finally:
        db.close()
