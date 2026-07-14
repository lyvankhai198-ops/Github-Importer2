import io
import html
import logging
from models import Order
from services.order_service import get_delivery_items
from services.normalize import format_delivery_message, format_vnd

logger = logging.getLogger(__name__)


def _get_lang(db, tg_id: str) -> str:
    from bot.i18n import get_user_lang
    return get_user_lang(db, tg_id)


# ── Delivery notifications ─────────────────────────────────────────────────────

async def notify_user_rank_upgrade(bot, telegram_user_id: str, rank_emoji: str, rank_name: str, lang: str = "vi"):
    """Sent once, right after a user's spend crosses into a new rank
    threshold (see services/rank_service.py). Never raises — a failed
    congrats DM must not block the order flow that triggered it."""
    try:
        from bot.i18n import t
        text = t(lang, "rank_upgraded", rank_emoji=rank_emoji, rank_name=rank_name)
        await bot.send_message(chat_id=int(telegram_user_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_user_rank_upgrade error: {e}")


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
            f"💰 Tổng tiền: {format_vnd(order.total_price)}đ\n"
            f"📅 Thời gian: {order.created_at.strftime('%d/%m/%Y %H:%M')}"
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_new_order error: {e}")


async def notify_user_delivery(bot, chat_id: str, order: Order, support_username: str = ""):
    """Gửi thông báo giao hàng đẹp cho user — không gửi raw JSON."""
    try:
        from database import SessionLocal
        from bot.keyboards import post_delivery_keyboard
        from bot.i18n import get_user_lang
        db = SessionLocal()
        try:
            lang = get_user_lang(db, str(chat_id))
        finally:
            db.close()

        if order.product and lang == "en" and getattr(order.product, "name_en", None):
            product_name = order.product.name_en
        else:
            product_name = order.product.name if order.product else str(order.product_id)
        items = get_delivery_items(order)
        if not items:
            await bot.send_message(
                chat_id=int(chat_id),
                text=(
                    f"✅ <b>Đơn hàng đã hoàn thành!</b>\n\n"
                    f"Mã đơn: <code>{order.order_code}</code>\n"
                    "Admin sẽ giao hàng cho bạn sớm."
                ) if lang == "vi" else (
                    f"✅ <b>Order completed!</b>\n\n"
                    f"Order: <code>{order.order_code}</code>\n"
                    "Admin will deliver your items shortly."
                ),
                parse_mode="HTML",
            )
            return

        text, file_bytes = format_delivery_message(order, items, product_name, lang=lang)
        keyboard = post_delivery_keyboard(order.id, support_username, lang=lang)

        if file_bytes:
            await bot.send_document(
                chat_id=int(chat_id),
                document=io.BytesIO(file_bytes),
                filename=f"{order.order_code}.txt",
                caption=f"✅ Đơn <code>{order.order_code}</code> hoàn thành!" if lang == "vi"
                        else f"✅ Order <code>{order.order_code}</code> completed!",
                parse_mode="HTML",
            )
            await bot.send_message(
                chat_id=int(chat_id), text=text, parse_mode="HTML", reply_markup=keyboard,
            )
        else:
            await bot.send_message(
                chat_id=int(chat_id), text=text, parse_mode="HTML", reply_markup=keyboard,
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
            f"💰 Cần thanh toán: <b>{format_vnd(order.total_price)}đ</b>\n"
            f"🔑 Mã TT: <code>{order.payment_code or '—'}</code>\n"
            f"⏰ Hết hạn: {expires}"
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_new_payment_pending error: {e}")


async def notify_admin_payment_partial(bot, order: Order, admin_telegram_id: str,
                                        paid: float, expected: float):
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
            f"✅ Đã nhận: {format_vnd(paid)}đ\n"
            f"❌ Còn thiếu: {format_vnd(remaining)}đ\n"
            f"💰 Tổng cần: {format_vnd(expected)}đ"
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_payment_partial error: {e}")


async def notify_admin_payment_received(bot, order: Order, admin_telegram_id: str):
    if not admin_telegram_id:
        return
    try:
        product_name = order.product.name if order.product else str(order.product_id)
        paid_at = order.paid_at.strftime("%H:%M %d/%m/%Y") if order.paid_at else "—"
        method = (order.payment_method or "bank_transfer").upper()
        text = (
            f"💳 <b>Đã nhận thanh toán đủ!</b>\n\n"
            f"📋 Đơn: <code>{order.order_code}</code>\n"
            f"📦 Sản phẩm: {html.escape(product_name)}\n"
            f"💰 Số tiền: {format_vnd((order.paid_amount or 0))}đ\n"
            f"💳 Phương thức: {method}\n"
            f"⏰ Lúc: {paid_at}\n"
            f"🔄 Đang lấy hàng từ nguồn..."
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_payment_received error: {e}")


async def notify_admin_payment_overpaid(bot, order: Order, admin_telegram_id: str):
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
            f"✅ Đã nhận: {format_vnd((order.paid_amount or 0))}đ\n"
            f"💰 Cần trả: {format_vnd((order.expected_amount or order.total_price))}đ\n"
            f"⬆️ Thừa: {format_vnd(surplus)}đ\n\n"
            "Đơn đang được xử lý tự động. Cần hoàn tiền thừa."
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_payment_overpaid error: {e}")


async def notify_admin_late_payment(bot, order: Order, admin_telegram_id: str):
    if not admin_telegram_id:
        return
    try:
        product_name = order.product.name if order.product else str(order.product_id)
        text = (
            f"⚠️ <b>Thanh toán trễ hạn!</b>\n\n"
            f"📋 Đơn: <code>{order.order_code}</code>\n"
            f"📦 Sản phẩm: {html.escape(product_name)}\n"
            f"👤 User: <code>{order.telegram_user_id}</code>\n"
            f"💰 Số tiền nhận: {format_vnd((order.paid_amount or 0))}đ\n\n"
            "Đơn đã hết hạn — cần xử lý thủ công."
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_late_payment error: {e}")


async def notify_admin_api_failed_after_payment(bot, order: Order, admin_telegram_id: str,
                                                  reason: str = ""):
    if not admin_telegram_id:
        return
    try:
        product_name = order.product.name if order.product else str(order.product_id)
        text = (
            f"🚨 <b>ĐÃ NHẬN TIỀN — API NGUỒN LỖI!</b>\n\n"
            f"📋 Đơn: <code>{order.order_code}</code>\n"
            f"📦 Sản phẩm: {html.escape(product_name)}\n"
            f"👤 User: <code>{order.telegram_user_id}</code>\n"
            f"💰 Đã nhận: {format_vnd((order.paid_amount or 0))}đ\n"
            + (f"❌ Lỗi: {html.escape(reason[:200])}\n" if reason else "") +
            "\n⚠️ Khách đang chờ — cần giao hàng thủ công NGAY!"
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_api_failed_after_payment error: {e}")


async def notify_admin_payment_success(bot, order: Order, admin_telegram_id: str):
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
            f"💰 Doanh thu: {format_vnd(order.total_price)}đ"
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_payment_success error: {e}")


# ── New: paid_waiting_stock ────────────────────────────────────────────────────

async def notify_user_paid_waiting_stock(bot, chat_id: str, order: Order, lang: str = "vi"):
    """User: we got their money but source ran out of stock unexpectedly."""
    try:
        if lang == "en":
            text = (
                f"✅ Payment received.\n\n"
                f"⚠️ Unfortunately, the product has just run out of stock at the source.\n"
                f"Order <code>{order.order_code}</code> is queued for manual processing.\n\n"
                "Admin will deliver your items or arrange a refund."
            )
        else:
            text = (
                f"✅ Đã nhận thanh toán.\n\n"
                f"⚠️ Sản phẩm vừa hết hàng tại nguồn.\n"
                f"Đơn <code>{order.order_code}</code> đã chuyển sang xử lý thủ công.\n\n"
                "Admin sẽ giao hàng hoặc hoàn tiền sớm nhất."
            )
        await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_user_paid_waiting_stock error: {e}")


async def notify_admin_paid_waiting_stock(bot, order: Order, admin_telegram_id: str):
    """Admin: payment OK but stock ran out after payment — needs manual action."""
    if not admin_telegram_id:
        return
    try:
        product_name = order.product.name if order.product else str(order.product_id)
        await bot.send_message(
            chat_id=int(admin_telegram_id),
            text=(
                f"⚠️ <b>ĐÃ NHẬN TIỀN — NGUỒN HẾT HÀNG!</b>\n\n"
                f"📋 Đơn: <code>{order.order_code}</code>\n"
                f"📦 Sản phẩm: {html.escape(product_name)}\n"
                f"👤 User: <code>{order.telegram_user_id}</code>\n"
                f"💰 Đã nhận: {format_vnd((order.paid_amount or 0))}đ\n\n"
                "Cần giao thủ công, đổi nguồn hoặc hoàn tiền NGAY."
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"notify_admin_paid_waiting_stock error: {e}")


# ── New: Binance manual proof ──────────────────────────────────────────────────

async def notify_admin_binance_manual_proof(bot, order: Order, admin_telegram_id: str,
                                             proof_file_id: str = "", note: str = ""):
    """Admin: user claims to have paid via Binance Pay Manual — sent proof."""
    if not admin_telegram_id:
        return
    try:
        product_name = order.product.name if order.product else str(order.product_id)
        text = (
            f"🟡 <b>Binance Pay — Bằng chứng thanh toán!</b>\n\n"
            f"📋 Đơn: <code>{order.order_code}</code>\n"
            f"📦 Sản phẩm: {html.escape(product_name)}\n"
            f"👤 User: <code>{order.telegram_user_id}</code>\n"
            f"💰 Cần: <b>{order.expected_crypto_amount or 0:.4f} USDT</b>\n"
            + (f"📝 Ghi chú: {html.escape(note[:200])}\n" if note else "")
        )
        if proof_file_id:
            await bot.send_photo(
                chat_id=int(admin_telegram_id), photo=proof_file_id,
                caption=text, parse_mode="HTML",
            )
        else:
            await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_binance_manual_proof error: {e}")


# ── New: crypto late payment ───────────────────────────────────────────────────

async def notify_user_late_payment(bot, chat_id: str, order: Order, lang: str = "vi"):
    """User: crypto payment received after order expired."""
    try:
        if lang == "en":
            text = (
                f"⚠️ <b>Late payment received</b>\n\n"
                f"Order <code>{order.order_code}</code> had already expired.\n\n"
                "Your transaction has been recorded.\n"
                "Please contact support for assistance."
            )
        else:
            text = (
                f"⚠️ <b>Thanh toán nhận được sau hạn</b>\n\n"
                f"Đơn <code>{order.order_code}</code> đã hết thời gian thanh toán.\n\n"
                "Hệ thống đã ghi nhận giao dịch của bạn.\n"
                "Vui lòng liên hệ bộ phận hỗ trợ để được xử lý."
            )
        await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_user_late_payment error: {e}")


async def notify_user_payment_partial(bot, chat_id: str, order: Order,
                                       paid: float, expected: float, lang: str = "vi"):
    """User: partial bank transfer received."""
    try:
        remaining = expected - paid
        if lang == "en":
            text = (
                f"⚠️ <b>Incomplete payment</b>\n\n"
                f"Order: <code>{order.order_code}</code>\n"
                f"✅ Received: <b>{format_vnd(paid)} VND</b>\n"
                f"❌ Still needed: <b>{format_vnd(remaining)} VND</b>\n\n"
                "Please transfer the remaining amount with the same transfer note."
            )
        else:
            text = (
                f"⚠️ <b>Thanh toán chưa đủ</b>\n\n"
                f"Mã đơn: <code>{order.order_code}</code>\n"
                f"✅ Đã nhận: <b>{format_vnd(paid)}đ</b>\n"
                f"❌ Còn thiếu: <b>{format_vnd(remaining)}đ</b>\n\n"
                "Vui lòng chuyển thêm đúng số tiền còn thiếu với cùng nội dung chuyển khoản."
            )
        from bot.keyboards import payment_keyboard
        await bot.send_message(
            chat_id=int(chat_id), text=text, parse_mode="HTML",
            reply_markup=payment_keyboard(order.id, lang=lang),
        )
    except Exception as e:
        logger.error(f"notify_user_payment_partial error: {e}")


# ── Wallet ───────────────────────────────────────────────────────────────────

async def notify_user_wallet_refund(bot, chat_id: str, order: Order, lang: str = "vi"):
    """User: a wallet-paid order failed to fulfill and was auto-refunded."""
    try:
        from bot.i18n import t
        text = t(lang, "wallet_refund_notice", code=order.order_code, amount=format_vnd(order.total_price))
        await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_user_wallet_refund error: {e}")


# ── Order issue reports ─────────────────────────────────────────────────────

async def notify_admin_new_issue(bot, order: Order, issue, admin_telegram_id: str, admin_keyboard=None):
    """Admin: a shopper reported a problem with a delivered order — full
    detail + any attached media + the action keyboard (view/reply/refund/
    reject/resolve), sent immediately."""
    if not admin_telegram_id:
        return
    try:
        product_name = order.product.name if order.product else str(order.product_id)
        refund_str = (
            f"{format_vnd(issue.calculated_refund_amount)}đ"
            if issue.calculated_refund_currency and issue.calculated_refund_currency.value == "VND"
            else f"{issue.calculated_refund_amount:.4f} USDT" if issue.calculated_refund_amount is not None
            else "—"
        )
        text = (
            f"⚠️ <b>BÁO LỖI ĐƠN HÀNG MỚI!</b>\n\n"
            f"🆔 Issue: <code>#{issue.id}</code>\n"
            f"📋 Mã đơn: <code>{order.order_code}</code>\n"
            f"👤 User: <code>{order.telegram_user_id}</code>\n"
            f"📦 Sản phẩm: {html.escape(product_name)}\n"
            f"💰 Hoàn tiền tối đa (ước tính): {refund_str}\n\n"
            f"📝 Nội dung:\n{html.escape(issue.issue_text) if issue.issue_text else '(không có văn bản, xem media)'}"
        )
        if issue.media_type == "photo" and issue.telegram_file_id:
            await bot.send_photo(chat_id=int(admin_telegram_id), photo=issue.telegram_file_id,
                                  caption=text, parse_mode="HTML", reply_markup=admin_keyboard)
        elif issue.media_type == "video" and issue.telegram_file_id:
            await bot.send_video(chat_id=int(admin_telegram_id), video=issue.telegram_file_id,
                                  caption=text, parse_mode="HTML", reply_markup=admin_keyboard)
        elif issue.media_type == "document" and issue.telegram_file_id:
            await bot.send_document(chat_id=int(admin_telegram_id), document=issue.telegram_file_id,
                                     caption=text, parse_mode="HTML", reply_markup=admin_keyboard)
        else:
            await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML",
                                    reply_markup=admin_keyboard)
    except Exception as e:
        logger.error(f"notify_admin_new_issue error: {e}")


async def notify_admin_wallet_deposit_request(bot, deposit, admin_telegram_id: str):
    """Admin: shopper submitted a new wallet deposit request awaiting confirmation."""
    if not admin_telegram_id:
        return
    try:
        currency = deposit.currency.value if hasattr(deposit.currency, "value") else str(deposit.currency)
        amount_str = format_vnd(deposit.amount) + "đ" if currency == "VND" else f"{deposit.amount:.2f} USDT"
        text = (
            f"💼 <b>YÊU CẦU NẠP TIỀN MỚI!</b>\n\n"
            f"👤 User: <code>{deposit.telegram_user_id}</code>\n"
            f"💰 Số tiền: <b>{amount_str}</b>\n"
            f"🔑 Mã tham chiếu: <code>{deposit.reference_code}</code>\n"
            f"💳 Phương thức: {deposit.method or '—'}\n\n"
            "Vui lòng kiểm tra và xác nhận trên trang quản trị (Ví / Nạp tiền)."
        )
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_wallet_deposit_request error: {e}")


async def notify_user_wallet_deposit_confirmed(bot, chat_id: str, deposit, lang: str = "vi", new_balance: float = None):
    """
    User: deposit auto-credited. Also flips the original QR message to a
    "paid" state (buttons removed) so it can no longer be checked/cancelled —
    per the ĐỢT 2 spec, a credited QR must become unusable.
    """
    try:
        from bot.i18n import t
        currency = deposit.currency.value if hasattr(deposit.currency, "value") else str(deposit.currency)
        amount_str = format_vnd(deposit.amount) + " VND" if currency == "VND" else f"{deposit.amount:.2f} USDT"
        balance_str = (
            (format_vnd(new_balance) + " VND" if currency == "VND" else f"{new_balance:.2f} USDT")
            if new_balance is not None else "—"
        )
        time_str = (deposit.credited_at or deposit.confirmed_at).strftime("%H:%M %d/%m/%Y") \
            if (deposit.credited_at or deposit.confirmed_at) else ""
        text = t(
            lang, "wallet_deposit_confirmed_detail",
            ref=deposit.reference_code, amount=amount_str, balance=balance_str, time=time_str,
        )

        # Invalidate the original QR/instruction message — it must no longer
        # be usable once the deposit is credited.
        if deposit.chat_id and deposit.deposit_message_id:
            try:
                paid_caption = f"✅ {t(lang, 'wallet_deposit_check_credited', ref=deposit.reference_code)}"
                await bot.edit_message_caption(
                    chat_id=int(deposit.chat_id), message_id=deposit.deposit_message_id,
                    caption=paid_caption, parse_mode="HTML", reply_markup=None,
                )
            except Exception:
                try:
                    await bot.edit_message_text(
                        chat_id=int(deposit.chat_id), message_id=deposit.deposit_message_id,
                        text=paid_caption, parse_mode="HTML", reply_markup=None,
                    )
                except Exception:
                    pass

        await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_user_wallet_deposit_confirmed error: {e}")


async def notify_user_wallet_deposit_rejected(bot, chat_id: str, deposit, lang: str = "vi"):
    try:
        from bot.i18n import t
        note = deposit.admin_note or ""
        text = t(lang, "wallet_deposit_rejected_user", ref=deposit.reference_code, note=note)
        await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_user_wallet_deposit_rejected error: {e}")


async def notify_user_wallet_deposit_expired(bot, chat_id: str, deposit, lang: str = "vi"):
    """User: deposit window passed with nothing received — no manual action taken."""
    try:
        from bot.i18n import t
        text = t(lang, "wallet_deposit_expired_user", ref=deposit.reference_code)
        await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_user_wallet_deposit_expired error: {e}")


async def notify_user_wallet_admin_adjustment(bot, chat_id: str, currency: str, amount: float,
                                               note: str, is_credit: bool, lang: str = "vi"):
    try:
        from bot.i18n import t
        amount_str = format_vnd(amount) + " VND" if currency == "VND" else f"{amount:.2f} USDT"
        key = "wallet_admin_credit_notice" if is_credit else "wallet_admin_debit_notice"
        text = t(lang, key, amount=amount_str, note=note or "—")
        await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_user_wallet_admin_adjustment error: {e}")


async def notify_user_api_failed_after_payment(bot, chat_id: str, order: Order, lang: str = "vi"):
    """
    User: payment received but API failed.
    IMPORTANT: NEVER say 'chưa thanh toán'.
    """
    try:
        if lang == "en":
            text = (
                f"✅ Payment received.\n\n"
                f"⚠️ The source is currently experiencing issues.\n"
                f"Order <code>{order.order_code}</code> has been queued for manual processing.\n\n"
                "Admin will deliver your items as soon as possible."
            )
        else:
            text = (
                f"✅ Đã nhận thanh toán.\n\n"
                f"⚠️ Nguồn hàng đang gặp lỗi.\n"
                f"Đơn <code>{order.order_code}</code> đã được chuyển sang xử lý thủ công.\n\n"
                "Admin sẽ gửi hàng sớm nhất."
            )
        await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_user_api_failed_after_payment error: {e}")


# ── Customer programmatic API ────────────────────────────────────────────────

async def notify_admin_api_order_result(bot, order: Order, admin_telegram_id: str, success: bool):
    if not admin_telegram_id:
        return
    try:
        from bot.i18n import t
        amount = f"{format_vnd(order.total_price)}đ" if order.payment_currency == "VND" else f"{order.total_price} USDT"
        if success:
            text = t("vi", "api_admin_order_success", order_code=order.order_code,
                      client_id=order.api_client_id, amount=amount)
        else:
            text = t("vi", "api_admin_order_failed", order_code=order.order_code,
                      client_id=order.api_client_id, status=order.status.value)
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_api_order_result error: {e}")


async def notify_admin_api_client_lockout(bot, client, admin_telegram_id: str):
    if not admin_telegram_id:
        return
    try:
        from bot.i18n import t
        text = t("vi", "api_admin_client_locked", client_id=client.id, tg_id=client.telegram_user_id)
        await bot.send_message(chat_id=int(admin_telegram_id), text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"notify_admin_api_client_lockout error: {e}")
