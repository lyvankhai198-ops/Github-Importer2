import logging
from models import Order

logger = logging.getLogger(__name__)


async def notify_admin_new_order(bot, order: Order, admin_telegram_id: str):
    if not admin_telegram_id:
        return
    try:
        text = (
            f"🆕 *Đơn hàng mới cần xử lý!*\n\n"
            f"📋 Mã đơn: `{order.order_code}`\n"
            f"👤 User: `{order.telegram_user_id}`\n"
            f"📦 Sản phẩm: {order.product.name if order.product else order.product_id}\n"
            f"🔢 Số lượng: {order.quantity}\n"
            f"💰 Tổng tiền: {order.total_price:,.0f}đ\n"
            f"📅 Thời gian: {order.created_at.strftime('%d/%m/%Y %H:%M')}"
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"notify_admin_new_order error: {e}")


async def notify_user_order_complete(bot, chat_id: str, order: Order, delivery_data: str):
    try:
        text = (
            f"✅ *Đơn hàng của bạn đã hoàn thành!*\n\n"
            f"📋 Mã đơn: `{order.order_code}`\n"
            f"📦 Sản phẩm: {order.product.name if order.product else order.product_id}\n\n"
            f"📦 *Thông tin giao hàng:*\n"
            f"`{delivery_data}`"
        )
        await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"notify_user_order_complete error: {e}")


async def notify_admin_api_error(bot, api_name: str, error: str, admin_telegram_id: str):
    if not admin_telegram_id:
        return
    try:
        text = (
            f"⚠️ *Lỗi API!*\n\n"
            f"🔗 API: {api_name}\n"
            f"❌ Lỗi: {error}"
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"notify_admin_api_error error: {e}")
