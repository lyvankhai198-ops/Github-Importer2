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


async def _broadcast_message_with_buy_button(db: Session, texts: dict, product_id: int, photo=None) -> dict:
    """
    `texts` maps language_code ("vi"/"en") -> fully-rendered message text.
    Each user is sent the text matching their own User.language_code
    (falling back to "vi"), so a mixed-language user base each get the
    template in their own language.
    """
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    from telegram.error import Forbidden
    from services.bot_service import bot_manager
    from bot.i18n import t

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
    kbds = {
        lang: InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "btn_buy_now"), callback_data=f"product:{product_id}")]])
        for lang in texts
    }

    for i in range(0, total, batch_size):
        batch = users[i:i + batch_size]
        for user in batch:
            lang = user.language_code if user.language_code in texts else "vi"
            text = texts.get(lang) or texts.get("vi") or next(iter(texts.values()))
            kbd = kbds.get(lang) or kbds.get("vi") or next(iter(kbds.values()))
            try:
                if photo:
                    await bot.send_photo(chat_id=int(user.telegram_id), photo=photo, caption=text, parse_mode="HTML", reply_markup=kbd)
                else:
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


def _display_name(product, lang: str) -> str:
    if lang == "en" and getattr(product, "name_en", None):
        return product.name_en
    return product.name


def _icon_html(product) -> str:
    """Telegram icon for this product — see services/telegram_emoji.render_icon_html."""
    from services.telegram_emoji import render_icon_html
    return render_icon_html(product.telegram_icon, getattr(product, "telegram_custom_emoji_id", None))


def _new_product_lines(product, stock_info: dict | None, lang: str) -> list[str]:
    from services.normalize import format_vnd, format_usdt
    from bot.i18n import t
    icon = _icon_html(product)
    price = format_usdt(product.price_usdt) if lang == "en" else f"{format_vnd(product.sale_price)}đ"
    lines = [
        t(lang, "notify_new_product_title"),
        "",
        f"{icon} <b>{html.escape(_display_name(product, lang))}</b>",
        t(lang, "notify_price_line", price=price),
    ]
    if stock_info and stock_info["status"] != "unavailable":
        lines.append(t(lang, "notify_current_stock_line", stock=stock_info["stock"]))
    desc = product.description_en if (lang == "en" and product.description_en) else product.description
    if desc:
        short_desc = desc.strip()
        if len(short_desc) > 200:
            short_desc = short_desc[:197] + "..."
        if short_desc:
            lines.append("")
            lines.append(f"📝 {html.escape(short_desc)}")
    return lines


def _restock_lines(product, added_qty: int, new_total: int, lang: str) -> list[str]:
    from services.normalize import format_vnd, format_usdt
    from bot.i18n import t
    icon = _icon_html(product)
    price = format_usdt(product.price_usdt) if lang == "en" else f"{format_vnd(product.sale_price)}đ"
    return [
        t(lang, "notify_restock_title"),
        "",
        f"{icon} <b>{html.escape(_display_name(product, lang))}</b>",
        t(lang, "notify_added_line", qty=added_qty),
        t(lang, "notify_current_stock_line", stock=new_total),
        t(lang, "notify_price_line", price=price),
    ]


async def _send_with_photo_fallback(db: Session, product, lines_by_lang: dict) -> dict:
    """
    Send the announcement as a photo+caption if the product has a valid
    image, else as plain text. A broken/missing image must never fail the
    whole announcement — falls back to text automatically.
    `lines_by_lang` maps language_code -> list[str] of message lines.
    """
    texts = {lang: "\n".join(lines) for lang, lines in lines_by_lang.items()}
    image_path = getattr(product, "image_path", None)
    if not image_path:
        return await _broadcast_message_with_buy_button(db, texts, product.id)

    photo_source = None
    try:
        if image_path.startswith("/uploads/"):
            from config import UPLOADS_DIR
            fpath = UPLOADS_DIR / image_path.split("/uploads/", 1)[1]
            if fpath.exists():
                photo_source = fpath.read_bytes()
        else:
            photo_source = image_path  # external URL
    except Exception as e:
        logger.error(f"[product_notify] failed to load product image, falling back to text: {e}")
        photo_source = None

    if not photo_source:
        return await _broadcast_message_with_buy_button(db, texts, product.id)

    return await _broadcast_message_with_buy_button(db, texts, product.id, photo=photo_source)


async def notify_new_product_broadcast(product) -> dict:
    """
    Broadcast a "🆕 SẢN PHẨM MỚI" announcement to all active users when a
    genuinely new Product becomes visible (admin manual add, or the first
    time an existing product actually has stock to sell). Gated on
    TelegramBotConfig.notify_new_products, and deduplicated so the same
    product is only ever introduced once, via notification_events.

    If the product currently has no stock to sell (a stock-gated product
    just created with an empty inventory / no synced source yet), the
    announcement is deferred — it will fire automatically as a "new
    product" message the first time stock actually arrives, via
    notify_product_stock_event below, instead of announcing something
    nobody can buy yet.
    """
    from database import SessionLocal
    from services.notification_events import claim_event, has_new_product_event
    db = SessionLocal()
    try:
        if has_new_product_event(db, product.id):
            return {"skipped": True, "reason": "already_announced"}

        from services.product_service import get_product_stock_status
        info = get_product_stock_status(product.id, db)
        if info["status"] in ("out_of_stock", "unavailable"):
            return {"skipped": True, "reason": "no_stock_yet"}

        claimed = claim_event(
            db, f"new_product:{product.id}", "new_product",
            product_id=product.id, current_stock=info.get("stock"),
        )
        if not claimed:
            return {"skipped": True, "reason": "duplicate"}

        cfg = _get_bot_config(db)
        if not cfg or not getattr(cfg, "notify_new_products", True):
            return {"skipped": True, "reason": "disabled"}

        lines_by_lang = {
            "vi": _new_product_lines(product, info, "vi"),
            "en": _new_product_lines(product, info, "en"),
        }
        return await _send_with_photo_fallback(db, product, lines_by_lang)
    finally:
        db.close()


async def notify_product_stock_event(product_id: int, previous_stock: int, current_stock: int, source_id: int | None = None) -> dict:
    """
    Single entry point for ANY genuine stock increase on an existing
    product — whether from a manual inventory import or an API sync.
    Decides whether this is really the product's first-ever appearance
    (never announced before -> "🆕 SẢN PHẨM MỚI") or a routine top-up on an
    already-announced product (-> "🔄 ĐÃ BỔ SUNG SẢN PHẨM"), and
    deduplicates via notification_events so the same increase is never
    announced twice (e.g. a scheduler re-sync reporting the same total).

    Never called for decreases (customer purchases) or unchanged syncs —
    callers must only invoke this when current_stock > previous_stock.
    """
    added_qty = current_stock - (previous_stock or 0)
    if added_qty <= 0:
        return {"skipped": True, "reason": "no_increase"}

    from database import SessionLocal
    from models import Product
    from services.notification_events import claim_event, has_new_product_event
    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product or not product.is_active:
            return {"skipped": True, "reason": "product_inactive"}

        is_new = not has_new_product_event(db, product_id)
        event_type = "new_product" if is_new else "restock"
        event_key = f"new_product:{product_id}" if is_new else f"restock:{product_id}:{current_stock}"

        claimed = claim_event(
            db, event_key, event_type, product_id=product_id, source_id=source_id,
            previous_stock=previous_stock, current_stock=current_stock, added_quantity=added_qty,
        )
        if not claimed:
            return {"skipped": True, "reason": "duplicate"}

        cfg_flag = "notify_new_products" if is_new else "notify_restock"
        cfg = _get_bot_config(db)
        if not cfg or not getattr(cfg, cfg_flag, True):
            return {"skipped": True, "reason": "disabled"}

        if is_new:
            from services.product_service import get_product_stock_status
            info = get_product_stock_status(product_id, db)
            lines_by_lang = {
                "vi": _new_product_lines(product, info, "vi"),
                "en": _new_product_lines(product, info, "en"),
            }
        else:
            lines_by_lang = {
                "vi": _restock_lines(product, added_qty, current_stock, "vi"),
                "en": _restock_lines(product, added_qty, current_stock, "en"),
            }

        return await _send_with_photo_fallback(db, product, lines_by_lang)
    finally:
        db.close()


async def notify_restock_broadcast(product_id: int, added_qty: int, new_total: int) -> dict:
    """
    Backward-compatible wrapper around notify_product_stock_event for
    existing call sites that already know the added quantity / new total.
    Kept so callers don't need to separately track previous_stock.
    """
    previous_stock = new_total - added_qty
    return await notify_product_stock_event(product_id, previous_stock, new_total)
