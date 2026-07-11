import io
import html
import logging
from models import Order
from services.order_service import get_delivery_items
from services.normalize import format_delivery_message

logger = logging.getLogger(__name__)


async def notify_admin_new_order(bot, order: Order, admin_telegram_id: str):
    if not admin_telegram_id:
        return
    try:
        product_name = order.product.name if order.product else str(order.product_id)
        text = (
            f"🆕 <b>Đơn hàng mới cần xử lý!</b>\n\n"
            f"📋 Mã đơn: <code>{order.order_code}</code>\n"
            f"👤 User: <code>{order.telegram_user_id}</code>\n"
            f"📦 Sản phẩm: {html.escape(product_name)}\n"
            f"🔢 Số lượng: {order.quantity}\n"
            f"💰 Tổng tiền: {order.total_price:,.0f}đ\n"
            f"📅 Thời gian: {order.created_at.strftime('%d/%m/%Y %H:%M')}"
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_new_order error: {e}")


async def notify_user_delivery(bot, chat_id: str, order: Order, support_username: str = ""):
    """Gửi thông báo giao hàng đẹp cho user — không gửi raw JSON."""
    try:
        from bot.keyboards import post_delivery_keyboard
        product_name = order.product.name if order.product else str(order.product_id)
        items = get_delivery_items(order)
        if not items:
            await bot.send_message(
                chat_id=int(chat_id),
                text=(
                    f"✅ <b>Đơn hàng đã hoàn thành!</b>\n\n"
                    f"Mã đơn: <code>{order.order_code}</code>\n"
                    "Admin sẽ giao hàng cho bạn sớm."
                ),
                parse_mode="HTML",
            )
            return

        text, file_bytes = format_delivery_message(order, items, product_name)
        keyboard = post_delivery_keyboard(order.id, support_username)

        if file_bytes:
            await bot.send_document(
                chat_id=int(chat_id),
                document=io.BytesIO(file_bytes),
                filename=f"{order.order_code}.txt",
                caption=f"✅ Đơn <code>{order.order_code}</code> hoàn thành!",
                parse_mode="HTML",
            )
            await bot.send_message(
                chat_id=int(chat_id),
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        else:
            await bot.send_message(
                chat_id=int(chat_id),
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
    except Exception as e:
        logger.error(f"notify_user_delivery error: {e}")


async def notify_admin_partial_delivery(bot, order: Order, admin_telegram_id: str, delivered: int):
    """Báo admin khi giao thiếu hàng."""
    if not admin_telegram_id:
        return
    try:
        product_name = order.product.name if order.product else str(order.product_id)
        missing = order.quantity - delivered
        text = (
            f"⚠️ <b>CẢNH BÁO: Giao thiếu hàng!</b>\n\n"
            f"📋 Đơn: <code>{order.order_code}</code>\n"
            f"📦 Sản phẩm: {html.escape(product_name)}\n"
            f"👤 User: <code>{order.telegram_user_id}</code>\n"
            f"Đặt: {order.quantity} | Giao được: {delivered} | Thiếu: {missing}\n\n"
            "Vui lòng xử lý thủ công phần còn thiếu."
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_partial_delivery error: {e}")


async def notify_admin_api_error(bot, api_name: str, error: str, admin_telegram_id: str):
    if not admin_telegram_id:
        return
    try:
        text = (
            f"⚠️ <b>Lỗi API!</b>\n\n"
            f"🔗 API: {html.escape(api_name)}\n"
            f"❌ Lỗi: {html.escape(error[:300])}"
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_api_error error: {e}")
