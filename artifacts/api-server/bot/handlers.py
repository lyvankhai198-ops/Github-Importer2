import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.keyboards import main_menu_keyboard, product_list_keyboard, product_detail_keyboard, confirm_order_keyboard
from services.product_service import get_active_products_for_bot, get_product_detail
from services.order_service import create_order, get_or_create_user
from models import Order, TelegramBotConfig, User
from database import SessionLocal

logger = logging.getLogger(__name__)


def _get_admin_id(db) -> str:
    cfg = db.query(TelegramBotConfig).first()
    return cfg.admin_telegram_id if cfg else ""


def _get_welcome_message(db) -> str:
    cfg = db.query(TelegramBotConfig).first()
    return cfg.welcome_message if cfg and cfg.welcome_message else "Chào mừng bạn đến với cửa hàng!"


def _get_support_username(db) -> str:
    cfg = db.query(TelegramBotConfig).first()
    return cfg.support_username if cfg and cfg.support_username else ""


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        tg_user = update.effective_user
        get_or_create_user(db, str(tg_user.id), tg_user.username, tg_user.first_name, tg_user.last_name)
        admin_id = _get_admin_id(db)
        is_admin = str(tg_user.id) == str(admin_id)
        welcome = _get_welcome_message(db)
        await update.message.reply_text(
            welcome,
            reply_markup=main_menu_keyboard(is_admin=is_admin)
        )
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
            "🛍 *Danh sách sản phẩm:*",
            parse_mode="Markdown",
            reply_markup=product_list_keyboard(products)
        )
    finally:
        db.close()


async def orders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        tg_user = update.effective_user
        orders = db.query(Order).filter(
            Order.telegram_user_id == str(tg_user.id)
        ).order_by(Order.created_at.desc()).limit(10).all()
        if not orders:
            await update.message.reply_text("Bạn chưa có đơn hàng nào.")
            return
        lines = ["📦 *Đơn hàng gần đây:*\n"]
        status_map = {
            "pending_manual": "⏳ Chờ xử lý",
            "processing_api": "🔄 Đang xử lý",
            "completed": "✅ Hoàn thành",
            "failed": "❌ Thất bại",
            "cancelled": "🚫 Đã huỷ",
        }
        for o in orders:
            st = status_map.get(o.status.value if hasattr(o.status, "value") else o.status, o.status)
            lines.append(f"• `{o.order_code}` — {st}\n  💰 {o.total_price:,.0f}đ | {o.created_at.strftime('%d/%m/%Y')}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
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


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "close":
        await query.message.delete()
        return

    if data == "back_products":
        db = SessionLocal()
        try:
            products = get_active_products_for_bot(db)
            await query.message.edit_text(
                "🛍 *Danh sách sản phẩm:*",
                parse_mode="Markdown",
                reply_markup=product_list_keyboard(products)
            )
        finally:
            db.close()
        return

    if data.startswith("product:"):
        product_id = int(data.split(":")[1])
        db = SessionLocal()
        try:
            detail = get_product_detail(db, product_id)
            if not detail:
                await query.message.edit_text("Sản phẩm không tồn tại.")
                return
            p = detail["product"]
            stock = sum((s.last_stock or 0) for s in detail["sources"] if s.is_active)
            stock_text = f"🟢 Còn hàng ({stock})" if stock > 0 else "🔴 Hết hàng"
            text = (
                f"📦 *{p.name}*\n\n"
                f"💰 Giá: {p.sale_price:,.0f}đ\n"
                f"📊 Tình trạng: {stock_text}\n\n"
                f"{p.description or ''}"
            )
            if p.image_path:
                try:
                    await query.message.reply_photo(
                        photo=open(p.image_path, "rb"),
                        caption=text,
                        parse_mode="Markdown",
                        reply_markup=product_detail_keyboard(p.id)
                    )
                    await query.message.delete()
                except Exception:
                    await query.message.edit_text(text, parse_mode="Markdown", reply_markup=product_detail_keyboard(p.id))
            else:
                await query.message.edit_text(text, parse_mode="Markdown", reply_markup=product_detail_keyboard(p.id))
        finally:
            db.close()
        return

    if data.startswith("buy:"):
        product_id = int(data.split(":")[1])
        context.user_data["buying_product_id"] = product_id
        context.user_data["state"] = "waiting_quantity"
        await query.message.reply_text("🔢 Nhập số lượng bạn muốn mua:")
        return

    if data.startswith("confirm_order:"):
        parts = data.split(":")
        product_id = int(parts[1])
        quantity = int(parts[2])
        tg_user = update.effective_user
        db = SessionLocal()
        try:
            order = await create_order(db, str(tg_user.id), product_id, quantity)
            status_val = order.status.value if hasattr(order.status, "value") else order.status
            if status_val == "completed":
                delivery = order.delivery_data or "Đã giao hàng"
                await query.message.edit_text(
                    f"✅ *Đơn hàng thành công!*\n\n"
                    f"Mã đơn: `{order.order_code}`\n"
                    f"Thông tin: {delivery}",
                    parse_mode="Markdown"
                )
            else:
                await query.message.edit_text(
                    f"✅ *Đơn hàng đã đặt!*\n\n"
                    f"Mã đơn: `{order.order_code}`\n"
                    f"Trạng thái: ⏳ Chờ xử lý\n"
                    f"Chúng tôi sẽ liên hệ bạn sớm!",
                    parse_mode="Markdown"
                )
            context.user_data.clear()
        except Exception as e:
            logger.error(f"Order creation error: {e}")
            await query.message.edit_text(f"❌ Lỗi đặt hàng: {str(e)}")
        finally:
            db.close()
        return

    if data == "cancel_order":
        context.user_data.clear()
        await query.message.edit_text("❌ Đã huỷ đặt hàng.")
        return


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
            total = p.sale_price * quantity
            summary = (
                f"📋 *Xác nhận đơn hàng:*\n\n"
                f"📦 Sản phẩm: {p.name}\n"
                f"🔢 Số lượng: {quantity}\n"
                f"💰 Đơn giá: {p.sale_price:,.0f}đ\n"
                f"💵 Tổng tiền: {total:,.0f}đ"
            )
            context.user_data["state"] = "confirming"
            await update.message.reply_text(
                summary,
                parse_mode="Markdown",
                reply_markup=confirm_order_keyboard(product_id, quantity)
            )
        finally:
            db.close()
        return
