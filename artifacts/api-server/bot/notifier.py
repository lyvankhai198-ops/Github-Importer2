import io
import html
import logging
from models import Order
from services.order_service import get_delivery_items
from services.normalize import format_delivery_message

logger = logging.getLogger(__name__)


# ── Existing delivery notifications ───────────────────────────────────────────

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


# ── Payment notifications ──────────────────────────────────────────────────────

async def notify_admin_new_payment_pending(bot, order: Order, admin_telegram_id: str,
                                            is_manual: bool = False):
    """Admin: new order waiting for payment (or manual delivery after payment)."""
    if not admin_telegram_id:
        return
    try:
        product_name = order.product.name if order.product else str(order.product_id)
        expires = order.payment_expires_at.strftime("%H:%M %d/%m/%Y") if order.payment_expires_at else "—"
        label = "chờ giao thủ công" if is_manual else "chờ thanh toán"
        text = (
            f"🆕 <b>Đơn mới — {label}!</b>\n\n"
            f"📋 Đơn: <code>{order.order_code}</code>\n"
            f"👤 User: <code>{order.telegram_user_id}</code>\n"
            f"📦 Sản phẩm: {html.escape(product_name)}\n"
            f"🔢 Số lượng: {order.quantity}\n"
            f"💰 Cần thanh toán: <b>{order.total_price:,.0f}đ</b>\n"
            f"🔑 Mã TT: <code>{order.payment_code or '—'}</code>\n"
            f"⏰ Hết hạn: {expires}"
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_new_payment_pending error: {e}")


async def notify_admin_payment_partial(bot, order: Order, admin_telegram_id: str,
                                        paid: float, expected: float):
    """Admin: customer paid but amount is insufficient."""
    if not admin_telegram_id:
        return
    try:
        remaining = expected - paid
        product_name = order.product.name if order.product else str(order.product_id)
        text = (
            f"⚠️ <b>Thanh toán thiếu!</b>\n\n"
            f"📋 Đơn: <code>{order.order_code}</code>\n"
            f"📦 Sản phẩm: {html.escape(product_name)}\n"
            f"👤 User: <code>{order.telegram_user_id}</code>\n"
            f"✅ Đã nhận: {paid:,.0f}đ\n"
            f"❌ Còn thiếu: {remaining:,.0f}đ\n"
            f"💰 Tổng cần: {expected:,.0f}đ"
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_payment_partial error: {e}")


async def notify_admin_payment_received(bot, order: Order, admin_telegram_id: str):
    """Admin: payment confirmed — order processing started."""
    if not admin_telegram_id:
        return
    try:
        product_name = order.product.name if order.product else str(order.product_id)
        paid_at = order.paid_at.strftime("%H:%M %d/%m/%Y") if order.paid_at else "—"
        text = (
            f"💳 <b>Đã nhận thanh toán đủ!</b>\n\n"
            f"📋 Đơn: <code>{order.order_code}</code>\n"
            f"📦 Sản phẩm: {html.escape(product_name)}\n"
            f"💰 Số tiền: {(order.paid_amount or 0):,.0f}đ\n"
            f"⏰ Lúc: {paid_at}\n"
            f"🔄 Đang lấy hàng từ nguồn..."
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_payment_received error: {e}")


async def notify_admin_payment_overpaid(bot, order: Order, admin_telegram_id: str):
    """Admin: customer overpaid."""
    if not admin_telegram_id:
        return
    try:
        product_name = order.product.name if order.product else str(order.product_id)
        surplus = (order.paid_amount or 0) - (order.expected_amount or order.total_price)
        text = (
            f"💰 <b>Thanh toán thừa!</b>\n\n"
            f"📋 Đơn: <code>{order.order_code}</code>\n"
            f"📦 Sản phẩm: {html.escape(product_name)}\n"
            f"👤 User: <code>{order.telegram_user_id}</code>\n"
            f"✅ Đã nhận: {(order.paid_amount or 0):,.0f}đ\n"
            f"💰 Cần trả: {(order.expected_amount or order.total_price):,.0f}đ\n"
            f"⬆️ Thừa: {surplus:,.0f}đ\n\n"
            "Đơn đang được xử lý tự động. Cần hoàn tiền thừa."
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_payment_overpaid error: {e}")


async def notify_admin_late_payment(bot, order: Order, admin_telegram_id: str):
    """Admin: payment received after order expired."""
    if not admin_telegram_id:
        return
    try:
        product_name = order.product.name if order.product else str(order.product_id)
        text = (
            f"⚠️ <b>Thanh toán trễ hạn!</b>\n\n"
            f"📋 Đơn: <code>{order.order_code}</code>\n"
            f"📦 Sản phẩm: {html.escape(product_name)}\n"
            f"👤 User: <code>{order.telegram_user_id}</code>\n"
            f"💰 Số tiền nhận: {(order.paid_amount or 0):,.0f}đ\n\n"
            "Đơn đã hết hạn — cần xử lý thủ công."
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_late_payment error: {e}")


async def notify_admin_api_failed_after_payment(bot, order: Order, admin_telegram_id: str,
                                                  reason: str = ""):
    """Admin: payment OK but API source failed — needs manual handling."""
    if not admin_telegram_id:
        return
    try:
        product_name = order.product.name if order.product else str(order.product_id)
        text = (
            f"🚨 <b>ĐÃ NHẬN TIỀN — API NGUỒN LỖI!</b>\n\n"
            f"📋 Đơn: <code>{order.order_code}</code>\n"
            f"📦 Sản phẩm: {html.escape(product_name)}\n"
            f"👤 User: <code>{order.telegram_user_id}</code>\n"
            f"💰 Đã nhận: {(order.paid_amount or 0):,.0f}đ\n"
            + (f"❌ Lỗi: {html.escape(reason[:200])}\n" if reason else "") +
            "\n⚠️ Khách đang chờ — cần giao hàng thủ công NGAY!"
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_api_failed_after_payment error: {e}")


async def notify_admin_payment_success(bot, order: Order, admin_telegram_id: str):
    """Admin: order fully delivered after payment."""
    if not admin_telegram_id:
        return
    try:
        product_name = order.product.name if order.product else str(order.product_id)
        text = (
            f"✅ <b>Giao hàng thành công!</b>\n\n"
            f"📋 Đơn: <code>{order.order_code}</code>\n"
            f"📦 Sản phẩm: {html.escape(product_name)}\n"
            f"👤 User: <code>{order.telegram_user_id}</code>\n"
            f"🔢 Số lượng: {order.quantity}\n"
            f"💰 Doanh thu: {order.total_price:,.0f}đ"
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_payment_success error: {e}")


async def notify_user_payment_partial(bot, chat_id: str, order: Order,
                                       paid: float, expected: float):
    """User: partial payment received, how much still needed."""
    try:
        remaining = expected - paid
        text = (
            f"⚠️ <b>Thanh toán chưa đủ</b>\n\n"
            f"Mã đơn: <code>{order.order_code}</code>\n"
            f"✅ Đã nhận: <b>{paid:,.0f}đ</b>\n"
            f"❌ Còn thiếu: <b>{remaining:,.0f}đ</b>\n\n"
            "Vui lòng chuyển thêm đúng số tiền còn thiếu với cùng nội dung chuyển khoản."
        )
        from bot.keyboards import payment_keyboard
        await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode="HTML",
            reply_markup=payment_keyboard(order.id),
        )
    except Exception as e:
        logger.error(f"notify_user_payment_partial error: {e}")


async def notify_user_api_failed_after_payment(bot, chat_id: str, order: Order):
    """
    User: payment received but API failed.
    IMPORTANT: NEVER say 'chưa thanh toán'.
    """
    try:
        text = (
            f"✅ Hệ thống đã nhận thanh toán của bạn.\n\n"
            f"⚠️ Nguồn hàng đang gặp sự cố kỹ thuật.\n"
            f"Đơn <code>{order.order_code}</code> đang được admin xử lý thủ công.\n\n"
            "Admin sẽ giao hàng cho bạn sớm nhất có thể. "
            "Nếu cần hỗ trợ, hãy liên hệ chúng tôi."
        )
        await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_user_api_failed_after_payment error: {e}")


async def notify_user_late_payment(bot, chat_id: str, order: Order):
    """User: paid after order expired."""
    try:
        text = (
            f"⚠️ <b>Thanh toán nhận được sau hạn</b>\n\n"
            f"Đơn <code>{order.order_code}</code> đã hết thời gian thanh toán.\n\n"
            "Hệ thống đã ghi nhận giao dịch của bạn.\n"
            "Vui lòng liên hệ bộ phận hỗ trợ để được xử lý."
        )
        await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_user_late_payment error: {e}")
