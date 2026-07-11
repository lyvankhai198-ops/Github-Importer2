import io
import html
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ContextTypes
from bot.keyboards import (
    main_menu_keyboard, product_list_keyboard, product_detail_keyboard,
    confirm_order_keyboard, post_delivery_keyboard, partial_delivery_keyboard,
)
from services.product_service import get_active_products_for_bot, get_product_detail
from services.order_service import create_order, get_or_create_user, get_order_by_id, get_delivery_items
from services.normalize import format_delivery_message, format_partial_delivery_message
from models import Order, TelegramBotConfig, OrderStatus
from database import SessionLocal

logger = logging.getLogger(__name__)

# Idempotency: track callback queries that are being processed
_processing_callbacks: set = set()


# ── Config helpers ────────────────────────────────────────────────────────────

def _get_config(db) -> TelegramBotConfig:
    return db.query(TelegramBotConfig).first()


def _get_admin_id(db) -> str:
    cfg = _get_config(db)
    return cfg.admin_telegram_id if cfg else ""


def _get_welcome_message(db) -> str:
    cfg = _get_config(db)
    return cfg.welcome_message if cfg and cfg.welcome_message else "Chào mừng bạn đến với cửa hàng!"


def _get_support_username(db) -> str:
    cfg = _get_config(db)
    return cfg.support_username if cfg and cfg.support_username else ""


def _status_label(status_val: str) -> str:
    return {
        "pending_manual": "⏳ Chờ xử lý",
        "processing_api": "🔄 Đang xử lý",
        "completed": "✅ Hoàn thành",
        "partial_delivery": "⚠️ Giao thiếu",
        "failed": "❌ Thất bại",
        "cancelled": "🚫 Đã huỷ",
    }.get(status_val, status_val)


# ── Command handlers ──────────────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        tg_user = update.effective_user
        get_or_create_user(db, str(tg_user.id), tg_user.username, tg_user.first_name, tg_user.last_name)
        admin_id = _get_admin_id(db)
        is_admin = str(tg_user.id) == str(admin_id)
        welcome = _get_welcome_message(db)
        await update.message.reply_text(welcome, reply_markup=main_menu_keyboard(is_admin=is_admin))
    finally:
        db.close()


async def products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        products = get_active_products_for_bot(db)
        if not products:
            await update.message.reply_text("Hiện không có sản phẩm nào.")
            return
        await update.message.reply_text(
            "🛍 <b>Danh sách sản phẩm:</b>",
            parse_mode="HTML",
            reply_markup=product_list_keyboard(products),
        )
    finally:
        db.close()


async def orders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        tg_user = update.effective_user
        orders = (
            db.query(Order)
            .filter(Order.telegram_user_id == str(tg_user.id))
            .order_by(Order.created_at.desc())
            .limit(10)
            .all()
        )
        if not orders:
            await update.message.reply_text("Bạn chưa có đơn hàng nào.")
            return
        lines = ["📦 <b>Đơn hàng gần đây:</b>\n"]
        for o in orders:
            sv = o.status.value if hasattr(o.status, "value") else o.status
            st = _status_label(sv)
            lines.append(
                f"• <code>{o.order_code}</code> — {st}\n"
                f"  💰 {o.total_price:,.0f}đ | {o.created_at.strftime('%d/%m/%Y')}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    finally:
        db.close()


async def support_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        support = _get_support_username(db)
        if support:
            await update.message.reply_text(f"💬 Liên hệ hỗ trợ: @{support}")
        else:
            await update.message.reply_text("💬 Vui lòng liên hệ quản trị viên để được hỗ trợ.")
    finally:
        db.close()


async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🌐 Truy cập trang quản trị tại địa chỉ máy chủ của bạn.")


# ── Callback query handler ────────────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # ── close ──
    if data == "close":
        await query.message.delete()
        return

    # ── home ──
    if data == "home":
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            admin_id = _get_admin_id(db)
            is_admin = str(tg_user.id) == str(admin_id)
            welcome = _get_welcome_message(db)
            await query.message.reply_text(welcome, reply_markup=main_menu_keyboard(is_admin=is_admin))
            await query.message.delete()
        except Exception:
            pass
        finally:
            db.close()
        return

    # ── back to product list ──
    if data == "back_products":
        db = SessionLocal()
        try:
            products = get_active_products_for_bot(db)
            await query.message.edit_text(
                "🛍 <b>Danh sách sản phẩm:</b>",
                parse_mode="HTML",
                reply_markup=product_list_keyboard(products),
            )
        finally:
            db.close()
        return

    # ── product detail ──
    if data.startswith("product:"):
        product_id = int(data.split(":")[1])
        db = SessionLocal()
        try:
            detail = get_product_detail(db, product_id)
            if not detail:
                await query.message.edit_text("Sản phẩm không tồn tại.")
                return
            p = detail["product"]
            sources = detail["sources"]

            # Get stock from sources (prefer api_product for freshness)
            stock = 0
            min_qty = p.min_quantity or 1
            api_src = None
            for src in sources:
                if src.is_active:
                    stock += (src.last_stock or 0)
                    if src.api_product:
                        api_src = src.api_product

            # Freshness check: if data > 2 min old, sync
            if api_src and api_src.last_sync_at:
                age = datetime.utcnow() - api_src.last_sync_at
                if age > timedelta(minutes=2):
                    from services.api_service import sync_api_products
                    await sync_api_products(db, api_src.api_connection_id)
                    db.expire_all()
                    # Re-fetch
                    detail = get_product_detail(db, product_id)
                    if detail:
                        p = detail["product"]
                        sources = detail["sources"]
                        stock = sum((s.last_stock or 0) for s in sources if s.is_active)

            if stock > 10:
                stock_text = f"🟢 Còn hàng ({stock})"
            elif stock > 0:
                stock_text = f"🟡 Còn ít ({stock})"
            else:
                stock_text = "🔴 Hết hàng"

            # Build detail text
            lines = [f"📦 <b>{html.escape(p.name)}</b>\n"]
            lines.append(f"💰 Giá bán: <b>{p.sale_price:,.0f}đ/tài khoản</b>")
            lines.append(f"📊 Tồn kho nguồn: {stock}")
            lines.append(f"🛒 Tối thiểu: {min_qty}")
            if p.duration:
                lines.append(f"⌛ Thời hạn: {html.escape(p.duration)}")
            if p.warranty:
                lines.append(f"🛡 Bảo hành: {html.escape(p.warranty)}")

            # Description: use admin's description, fallback to source
            description = p.description
            if not description and api_src:
                description = api_src.external_description
            if description:
                lines.append(f"\n📝 Mô tả:\n{html.escape(description)}")

            text = "\n".join(lines)

            # Send with image if available
            image_url = p.image_path
            if not image_url and api_src:
                image_url = api_src.external_image_url

            if image_url:
                try:
                    if image_url.startswith("http"):
                        import httpx as _httpx
                        async with _httpx.AsyncClient(timeout=10) as c:
                            resp = await c.get(image_url)
                        if resp.status_code == 200:
                            await query.message.reply_photo(
                                photo=io.BytesIO(resp.content),
                                caption=text,
                                parse_mode="HTML",
                                reply_markup=product_detail_keyboard(p.id),
                            )
                            await query.message.delete()
                            return
                    else:
                        await query.message.reply_photo(
                            photo=open(image_url, "rb"),
                            caption=text,
                            parse_mode="HTML",
                            reply_markup=product_detail_keyboard(p.id),
                        )
                        await query.message.delete()
                        return
                except Exception:
                    pass  # fall through to text

            await query.message.edit_text(text, parse_mode="HTML", reply_markup=product_detail_keyboard(p.id))
        finally:
            db.close()
        return

    # ── buy: enter quantity ──
    if data.startswith("buy:"):
        product_id = int(data.split(":")[1])
        context.user_data["buying_product_id"] = product_id
        context.user_data["state"] = "waiting_quantity"
        context.user_data.pop("processing_order", None)
        await query.message.reply_text("🔢 Nhập số lượng bạn muốn mua:")
        return

    # ── confirm order ──
    if data.startswith("confirm_order:"):
        # Idempotency: only process once
        cb_key = f"{update.effective_user.id}:{data}"
        if cb_key in _processing_callbacks:
            return
        _processing_callbacks.add(cb_key)

        parts = data.split(":")
        product_id = int(parts[1])
        quantity = int(parts[2])
        tg_user = update.effective_user

        db = SessionLocal()
        try:
            await query.message.edit_text("⏳ Đang xử lý đơn hàng...")

            order = await create_order(db, str(tg_user.id), product_id, quantity)
            sv = order.status.value if hasattr(order.status, "value") else order.status
            product_name = order.product.name if order.product else str(product_id)
            support = _get_support_username(db)

            if sv == "completed":
                items = get_delivery_items(order)
                if items:
                    text, file_bytes = format_delivery_message(order, items, product_name)
                    if file_bytes:
                        # >10 accounts → send file
                        await query.message.delete()
                        await context.bot.send_document(
                            chat_id=tg_user.id,
                            document=io.BytesIO(file_bytes),
                            filename=f"{order.order_code}.txt",
                            caption=f"✅ Đơn <code>{order.order_code}</code> hoàn thành!",
                            parse_mode="HTML",
                        )
                        await context.bot.send_message(
                            chat_id=tg_user.id,
                            text=text,
                            parse_mode="HTML",
                            reply_markup=post_delivery_keyboard(order.id, support),
                        )
                    else:
                        await query.message.edit_text(
                            text,
                            parse_mode="HTML",
                            reply_markup=post_delivery_keyboard(order.id, support),
                        )
                else:
                    await query.message.edit_text(
                        f"✅ <b>Đơn hàng thành công!</b>\n\nMã đơn: <code>{order.order_code}</code>\n"
                        "Admin sẽ giao hàng cho bạn sớm.",
                        parse_mode="HTML",
                    )

            elif sv == "partial_delivery":
                items = get_delivery_items(order)
                text = format_partial_delivery_message(order, items, product_name)
                await query.message.edit_text(
                    text,
                    parse_mode="HTML",
                    reply_markup=partial_delivery_keyboard(order.id, support),
                )
                # Notify admin
                admin_id = _get_admin_id(db)
                if admin_id:
                    delivered = len(items)
                    missing = order.quantity - delivered
                    try:
                        await context.bot.send_message(
                            chat_id=int(admin_id),
                            text=(
                                f"⚠️ <b>Giao thiếu hàng!</b>\n\n"
                                f"Đơn: <code>{order.order_code}</code>\n"
                                f"Sản phẩm: {html.escape(product_name)}\n"
                                f"Đặt: {order.quantity} | Giao: {delivered} | Thiếu: {missing}"
                            ),
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass

            elif sv == "failed":
                await query.message.edit_text(
                    "❌ Mua hàng thất bại.\nNguồn hiện chưa thể giao hàng. "
                    "Vui lòng thử lại hoặc liên hệ hỗ trợ.",
                )

            else:
                await query.message.edit_text(
                    f"✅ <b>Đơn hàng đã đặt!</b>\n\n"
                    f"Mã đơn: <code>{order.order_code}</code>\n"
                    f"Trạng thái: ⏳ Chờ xử lý\n"
                    "Chúng tôi sẽ liên hệ bạn sớm!",
                    parse_mode="HTML",
                )

            context.user_data.clear()

        except Exception as e:
            logger.error(f"Order creation error: {e}")
            await query.message.edit_text("❌ Lỗi đặt hàng. Vui lòng thử lại.")
        finally:
            db.close()
            _processing_callbacks.discard(cb_key)
        return

    # ── cancel order ──
    if data == "cancel_order":
        context.user_data.clear()
        await query.message.edit_text("❌ Đã huỷ đặt hàng.")
        return

    # ── view order ──
    if data.startswith("view_order:"):
        order_id = int(data.split(":")[1])
        tg_user = update.effective_user
        db = SessionLocal()
        try:
            order = get_order_by_id(db, order_id)
            if not order or order.telegram_user_id != str(tg_user.id):
                await query.answer("Không tìm thấy đơn hàng.", show_alert=True)
                return
            sv = order.status.value if hasattr(order.status, "value") else order.status
            product_name = order.product.name if order.product else "—"
            ext_code = order.external_order_code or order.external_order_id or "—"
            text = (
                f"📦 <b>Chi tiết đơn hàng</b>\n\n"
                f"Mã đơn: <code>{order.order_code}</code>\n"
                f"Mã nguồn: <code>{ext_code}</code>\n"
                f"Sản phẩm: {html.escape(product_name)}\n"
                f"Số lượng: {order.quantity}\n"
                f"Tổng tiền: {order.total_price:,.0f}đ\n"
                f"Trạng thái: {_status_label(sv)}\n"
                f"Thời gian: {order.created_at.strftime('%d/%m/%Y %H:%M')}"
            )
            support = _get_support_username(db)
            await query.message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=post_delivery_keyboard(order_id, support),
            )
        finally:
            db.close()
        return

    # ── reload / re-download accounts ──
    if data.startswith("reload_order:"):
        order_id = int(data.split(":")[1])
        tg_user = update.effective_user
        db = SessionLocal()
        try:
            order = get_order_by_id(db, order_id)
            admin_id = _get_admin_id(db)
            is_owner = order and order.telegram_user_id == str(tg_user.id)
            is_admin = str(tg_user.id) == str(admin_id)

            if not order or (not is_owner and not is_admin):
                await query.answer("Bạn không có quyền xem đơn hàng này.", show_alert=True)
                return

            items = get_delivery_items(order)
            if not items:
                await query.answer("Chưa có dữ liệu giao hàng.", show_alert=True)
                return

            product_name = order.product.name if order.product else "—"
            text, file_bytes = format_delivery_message(order, items, product_name)
            support = _get_support_username(db)

            if file_bytes:
                await context.bot.send_document(
                    chat_id=tg_user.id,
                    document=io.BytesIO(file_bytes),
                    filename=f"{order.order_code}.txt",
                    caption=f"📥 Tài khoản đơn <code>{order.order_code}</code>",
                    parse_mode="HTML",
                )
            else:
                await context.bot.send_message(
                    chat_id=tg_user.id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=post_delivery_keyboard(order.id, support),
                )
        finally:
            db.close()
        return


# ── Message handler (text input) ──────────────────────────────────────────────

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")

    if state == "waiting_quantity":
        product_id = context.user_data.get("buying_product_id")
        text = update.message.text.strip()
        try:
            quantity = int(text)
            if quantity <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Vui lòng nhập số lượng hợp lệ (số nguyên dương).")
            return

        db = SessionLocal()
        try:
            detail = get_product_detail(db, product_id)
            if not detail:
                await update.message.reply_text("Sản phẩm không tồn tại.")
                context.user_data.clear()
                return

            p = detail["product"]
            sources = detail["sources"]

            # Freshness check: sync if API product data > 2 min old
            for src in sources:
                if src.is_active and src.api_product and src.api_product.last_sync_at:
                    age = datetime.utcnow() - src.api_product.last_sync_at
                    if age > timedelta(minutes=2):
                        from services.api_service import sync_api_products
                        await sync_api_products(db, src.api_product.api_connection_id)
                        db.expire_all()
                        detail = get_product_detail(db, product_id)
                        if detail:
                            p = detail["product"]
                            sources = detail["sources"]
                        break

            # Calculate total available stock
            total_stock = sum((s.last_stock or 0) for s in sources if s.is_active)
            min_qty = p.min_quantity or 1

            # Validate quantity
            if quantity < min_qty:
                await update.message.reply_text(
                    f"❌ Số lượng tối thiểu là <b>{min_qty}</b>.", parse_mode="HTML"
                )
                return
            if total_stock > 0 and quantity > total_stock:
                await update.message.reply_text(
                    f"❌ Tồn kho chỉ còn <b>{total_stock}</b>.", parse_mode="HTML"
                )
                return

            total = p.sale_price * quantity
            summary = (
                f"🛒 <b>XÁC NHẬN ĐƠN HÀNG</b>\n\n"
                f"Sản phẩm: {html.escape(p.name)}\n"
                f"Số lượng: {quantity}\n"
                f"Đơn giá: {p.sale_price:,.0f}đ\n"
                f"Tổng tiền: <b>{total:,.0f}đ</b>\n"
                f"Nguồn giao: Tự động qua API"
            )
            context.user_data["state"] = "confirming"
            await update.message.reply_text(
                summary,
                parse_mode="HTML",
                reply_markup=confirm_order_keyboard(product_id, quantity),
            )
        finally:
            db.close()
        return
