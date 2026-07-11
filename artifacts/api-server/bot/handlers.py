import io
import html
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ContextTypes
from bot.keyboards import (
    main_menu_keyboard, product_list_keyboard, product_detail_keyboard,
    confirm_order_keyboard, post_delivery_keyboard, partial_delivery_keyboard,
    payment_keyboard,
)
from services.product_service import get_active_products_for_bot, get_product_detail
from services.order_service import create_order, get_or_create_user, get_order_by_id, get_delivery_items
from services.normalize import format_delivery_message, format_partial_delivery_message
from services.payment_service import (
    create_pending_payment_order, generate_vietqr_url, is_sepay_enabled, get_sepay_config,
)
from models import Order, TelegramBotConfig, OrderStatus, PaymentStatus
from database import SessionLocal

logger = logging.getLogger(__name__)

# Idempotency: track callback queries currently being processed
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
        "pending_payment": "💳 Chờ thanh toán",
        "processing_api": "🔄 Đang xử lý",
        "completed": "✅ Hoàn thành",
        "partial_delivery": "⚠️ Giao thiếu",
        "failed": "❌ Thất bại",
        "api_failed": "🚨 Lỗi sau thanh toán",
        "payment_expired": "⏰ Hết hạn TT",
        "cancelled": "🚫 Đã huỷ",
    }.get(status_val, status_val)


def _payment_status_label(ps: str) -> str:
    return {
        "pending": "⏳ Chờ thanh toán",
        "partial": "⚠️ Thanh toán thiếu",
        "paid": "✅ Đã thanh toán đủ",
        "overpaid": "💰 Thanh toán thừa",
        "expired": "⏰ Hết hạn",
        "failed": "❌ Thất bại",
    }.get(ps or "", ps or "—")


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


# ── Shared order creation helper ──────────────────────────────────────────────

async def _do_create_order(context, db, tg_user, product_id: int, quantity: int, processing_msg):
    """
    Create a pending_payment SePay order, send VietQR to the user.
    Called from both the confirm_order: callback and message_handler (waiting_quantity).
    """
    from models import SepayConfig
    cfg = db.query(TelegramBotConfig).first()
    support = cfg.support_username if cfg else ""
    admin_id = cfg.admin_telegram_id if cfg else ""
    shop_name = getattr(cfg, "shop_name", "") or "" if cfg else ""

    sepay = db.query(SepayConfig).first()

    if not sepay or not sepay.is_enabled:
        try:
            await processing_msg.edit_text("❌ Hệ thống thanh toán chưa được cấu hình.")
        except Exception:
            pass
        return

    if not sepay.account_number or not sepay.bank_bin or not sepay.account_name:
        if admin_id:
            try:
                await context.bot.send_message(
                    chat_id=int(admin_id),
                    text="⚠️ Thiếu cấu hình ngân hàng để tạo QR (account_number / bank_bin / account_name).",
                )
            except Exception:
                pass
        try:
            await processing_msg.edit_text(
                "❌ Hệ thống thanh toán chưa được cấu hình đầy đủ. Vui lòng liên hệ hỗ trợ."
            )
        except Exception:
            pass
        return

    # Create order
    order = create_pending_payment_order(db, str(tg_user.id), product_id, quantity)

    # Persist context message IDs on the order
    order.payment_chat_id = tg_user.id
    order.product_message_id = context.user_data.get("product_message_id")
    order.quantity_prompt_message_id = context.user_data.get("quantity_prompt_message_id")
    order.payment_message_type = "photo"
    db.commit()

    product_name = order.product.name if order.product else str(product_id)
    expiry_dt = order.payment_expires_at
    expiry_str = expiry_dt.strftime("%H:%M %d/%m/%Y") if expiry_dt else "—"
    timeout = sepay.payment_timeout_minutes or 15

    qr_url = generate_vietqr_url(
        bank_bin=sepay.bank_bin,
        account_number=sepay.account_number,
        amount=order.total_price,
        payment_code=order.payment_code,
        account_name=sepay.account_name,
        shop_name=shop_name,
    )

    caption = (
        f"💳 <b>THANH TOÁN ĐƠN HÀNG</b>\n\n"
        f"Mã đơn: <code>{order.order_code}</code>\n"
        f"Sản phẩm: {html.escape(product_name)}\n"
        f"Số lượng: {order.quantity}\n"
        f"Số tiền: <b>{order.total_price:,.0f}đ</b>\n\n"
        f"🏦 Ngân hàng: <b>{html.escape(sepay.bank_bin)}</b>\n"
        f"Số tài khoản: <code>{html.escape(sepay.account_number)}</code>\n"
        f"Chủ TK: {html.escape(sepay.account_name)}\n"
        f"Nội dung CK: <code>{html.escape(order.payment_code)}</code>\n\n"
        f"⏰ Hết hạn: {expiry_str} ({timeout} phút)"
    )

    kbd = payment_keyboard(order.id, support)

    # Delete processing placeholder
    try:
        await processing_msg.delete()
    except Exception:
        pass

    # Try sending QR as photo — URL direct first, then download fallback
    sent_msg = None
    try:
        sent_msg = await context.bot.send_photo(
            chat_id=tg_user.id,
            photo=qr_url,
            caption=caption,
            parse_mode="HTML",
            reply_markup=kbd,
        )
        order.payment_message_type = "photo"
    except Exception:
        pass

    if not sent_msg:
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=15) as c:
                resp = await c.get(qr_url)
            if resp.status_code == 200:
                sent_msg = await context.bot.send_photo(
                    chat_id=tg_user.id,
                    photo=io.BytesIO(resp.content),
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=kbd,
                )
                order.payment_message_type = "photo"
        except Exception:
            pass

    if not sent_msg:
        # Final fallback: text-only with "Tạo lại QR" button
        text_only = caption + f'\n\n🔗 <a href="{qr_url}">Mở QR VietQR</a>'
        try:
            sent_msg = await context.bot.send_message(
                chat_id=tg_user.id,
                text=text_only,
                parse_mode="HTML",
                reply_markup=payment_keyboard(order.id, support, show_regen_qr=True),
                disable_web_page_preview=True,
            )
            order.payment_message_type = "text"
        except Exception as e:
            logger.error(f"[order] could not send payment message for {order.order_code}: {e}")
            if admin_id:
                try:
                    await context.bot.send_message(
                        chat_id=int(admin_id),
                        text=f"⚠️ Không thể gửi QR cho đơn {order.order_code} (user {tg_user.id}).",
                    )
                except Exception:
                    pass

    if sent_msg:
        order.payment_message_id = sent_msg.message_id
        db.commit()


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

            stock = 0
            min_qty = p.min_quantity or 1
            api_src = None
            for src in sources:
                if src.is_active:
                    stock += (src.last_stock or 0)
                    if src.api_product:
                        api_src = src.api_product

            # Freshness check
            if api_src and api_src.last_sync_at:
                age = datetime.utcnow() - api_src.last_sync_at
                if age > timedelta(minutes=2):
                    from services.api_service import sync_api_products
                    await sync_api_products(db, api_src.api_connection_id)
                    db.expire_all()
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

            lines = [f"📦 <b>{html.escape(p.name)}</b>\n"]
            lines.append(f"💰 Giá bán: <b>{p.sale_price:,.0f}đ/tài khoản</b>")
            lines.append(f"📊 Tồn kho nguồn: {stock}")
            lines.append(f"🛒 Tối thiểu: {min_qty}")
            if p.duration:
                lines.append(f"⌛ Thời hạn: {html.escape(p.duration)}")
            if p.warranty:
                lines.append(f"🛡 Bảo hành: {html.escape(p.warranty)}")
            description = p.description or (api_src.external_description if api_src else None)
            if description:
                lines.append(f"\n📝 Mô tả:\n{html.escape(description)}")
            text = "\n".join(lines)

            image_url = p.image_path or (api_src.external_image_url if api_src else None)
            if image_url:
                try:
                    if image_url.startswith("http"):
                        import httpx as _httpx
                        async with _httpx.AsyncClient(timeout=10) as c:
                            resp = await c.get(image_url)
                        if resp.status_code == 200:
                            sent = await query.message.reply_photo(
                                photo=io.BytesIO(resp.content),
                                caption=text,
                                parse_mode="HTML",
                                reply_markup=product_detail_keyboard(p.id),
                            )
                            context.user_data["product_message_id"] = sent.message_id
                            await query.message.delete()
                            return
                    else:
                        sent = await query.message.reply_photo(
                            photo=open(image_url, "rb"),
                            caption=text,
                            parse_mode="HTML",
                            reply_markup=product_detail_keyboard(p.id),
                        )
                        context.user_data["product_message_id"] = sent.message_id
                        await query.message.delete()
                        return
                except Exception:
                    pass

            sent = await query.message.edit_text(text, parse_mode="HTML", reply_markup=product_detail_keyboard(p.id))
            context.user_data["product_message_id"] = query.message.message_id
        finally:
            db.close()
        return

    # ── buy: enter quantity ──
    if data.startswith("buy:"):
        product_id = int(data.split(":")[1])
        context.user_data["buying_product_id"] = product_id
        context.user_data["state"] = "waiting_quantity"
        context.user_data.pop("processing_order", None)
        prompt_msg = await query.message.reply_text("🔢 Nhập số lượng bạn muốn mua:")
        context.user_data["quantity_prompt_message_id"] = prompt_msg.message_id
        return

    # ── confirm order ──
    if data.startswith("confirm_order:"):
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
            await _do_create_order(context, db, tg_user, product_id, quantity, query.message)
            context.user_data.clear()
        except Exception as e:
            logger.error(f"Order creation error: {e}")
            try:
                await query.message.edit_text("❌ Lỗi đặt hàng. Vui lòng thử lại.")
            except Exception:
                pass
        finally:
            db.close()
            _processing_callbacks.discard(cb_key)
        return

    # ── cancel order (pre-payment) ──
    if data == "cancel_order":
        context.user_data.clear()
        await query.message.edit_text("❌ Đã huỷ đặt hàng.")
        return

    # ── cancel pending_payment order ──
    if data.startswith("cancel_pending:"):
        order_id = int(data.split(":")[1])
        tg_user = update.effective_user
        db = SessionLocal()
        try:
            order = get_order_by_id(db, order_id)
            if not order or order.telegram_user_id != str(tg_user.id):
                await query.answer("Không tìm thấy đơn hàng.", show_alert=True)
                return

            ps = order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status or "")
            if ps in ("paid", "overpaid"):
                await query.answer(
                    "Đơn đã thanh toán — không thể hủy. Liên hệ hỗ trợ.", show_alert=True
                )
                return

            order.status = OrderStatus.cancelled
            order.updated_at = datetime.utcnow()
            db.commit()

            # Delete all stored bot messages (product detail, quantity prompt, QR)
            from services.payment_service import safe_delete_message as _safe_del
            chat_id = order.payment_chat_id or order.telegram_user_id
            await _safe_del(context.bot, chat_id, order.product_message_id)
            await _safe_del(context.bot, chat_id, order.quantity_prompt_message_id)
            await _safe_del(context.bot, chat_id, order.payment_message_id)

            try:
                await context.bot.send_message(chat_id=int(chat_id), text="❌ Đã hủy đơn hàng.")
            except Exception:
                pass
        finally:
            db.close()
        return

    # ── regenerate QR ──
    if data.startswith("regen_qr:"):
        order_id = int(data.split(":")[1])
        tg_user = update.effective_user
        db = SessionLocal()
        try:
            order = get_order_by_id(db, order_id)
            if not order or order.telegram_user_id != str(tg_user.id):
                await query.answer("Không tìm thấy đơn hàng.", show_alert=True)
                return

            sv = order.status.value if hasattr(order.status, "value") else str(order.status)
            if sv != "pending_payment":
                await query.answer("Đơn hàng không còn ở trạng thái chờ thanh toán.", show_alert=True)
                return

            from models import SepayConfig
            from services.payment_service import generate_vietqr_url as _gen_qr, safe_delete_message as _safe_del
            cfg = db.query(TelegramBotConfig).first()
            support = cfg.support_username if cfg else ""
            shop_name = getattr(cfg, "shop_name", "") or "" if cfg else ""
            sepay = db.query(SepayConfig).first()

            if not sepay or not sepay.account_number or not sepay.bank_bin:
                await query.answer("Cấu hình ngân hàng chưa đầy đủ.", show_alert=True)
                return

            # Delete old QR message
            chat_id = order.payment_chat_id or order.telegram_user_id
            await _safe_del(context.bot, chat_id, order.payment_message_id)
            order.payment_message_id = None
            db.commit()

            product_name = order.product.name if order.product else "—"
            timeout = sepay.payment_timeout_minutes or 15
            expiry_dt = order.payment_expires_at
            expiry_str = expiry_dt.strftime("%H:%M %d/%m/%Y") if expiry_dt else "—"

            qr_url = _gen_qr(
                bank_bin=sepay.bank_bin,
                account_number=sepay.account_number,
                amount=order.total_price,
                payment_code=order.payment_code,
                account_name=sepay.account_name,
                shop_name=shop_name,
            )
            caption = (
                f"💳 <b>THANH TOÁN ĐƠN HÀNG</b>\n\n"
                f"Mã đơn: <code>{order.order_code}</code>\n"
                f"Sản phẩm: {html.escape(product_name)}\n"
                f"Số lượng: {order.quantity}\n"
                f"Số tiền: <b>{order.total_price:,.0f}đ</b>\n\n"
                f"🏦 Ngân hàng: <b>{html.escape(sepay.bank_bin)}</b>\n"
                f"Số tài khoản: <code>{html.escape(sepay.account_number)}</code>\n"
                f"Chủ TK: {html.escape(sepay.account_name)}\n"
                f"Nội dung CK: <code>{html.escape(order.payment_code)}</code>\n\n"
                f"⏰ Hết hạn: {expiry_str} ({timeout} phút)"
            )
            kbd = payment_keyboard(order.id, support)
            sent_msg = None
            try:
                sent_msg = await context.bot.send_photo(
                    chat_id=int(chat_id), photo=qr_url, caption=caption,
                    parse_mode="HTML", reply_markup=kbd,
                )
                order.payment_message_type = "photo"
            except Exception:
                pass
            if not sent_msg:
                try:
                    sent_msg = await context.bot.send_message(
                        chat_id=int(chat_id),
                        text=caption + f'\n\n🔗 <a href="{qr_url}">Mở QR VietQR</a>',
                        parse_mode="HTML",
                        reply_markup=payment_keyboard(order.id, support, show_regen_qr=True),
                        disable_web_page_preview=True,
                    )
                    order.payment_message_type = "text"
                except Exception as e:
                    logger.error(f"[regen_qr] could not send QR for {order.order_code}: {e}")
                    await query.answer("Không thể tạo QR. Vui lòng thử lại.", show_alert=True)
                    return
            if sent_msg:
                order.payment_message_id = sent_msg.message_id
                db.commit()
            await query.answer("✅ Đã tạo lại QR.")
        finally:
            db.close()
        return

    # ── check payment status ──
    if data.startswith("check_payment:"):
        order_id = int(data.split(":")[1])
        tg_user = update.effective_user
        db = SessionLocal()
        try:
            order = get_order_by_id(db, order_id)
            if not order or order.telegram_user_id != str(tg_user.id):
                await query.answer("Không tìm thấy đơn hàng.", show_alert=True)
                return

            sv = order.status.value if hasattr(order.status, "value") else str(order.status)
            ps = order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status or "pending")

            if sv == "completed":
                await query.answer("✅ Đơn đã hoàn thành.", show_alert=True)
                return

            if ps == "pending":
                await query.answer("⏳ Chưa nhận được thanh toán.", show_alert=True)
            elif ps == "partial":
                paid = order.paid_amount or 0
                expected = order.expected_amount or order.total_price
                remaining = expected - paid
                await query.answer(
                    f"⚠️ Đã nhận {paid:,.0f}đ.\nCòn thiếu {remaining:,.0f}đ.",
                    show_alert=True,
                )
            elif ps in ("paid", "overpaid"):
                await query.answer(
                    "✅ Thanh toán thành công.\nĐơn đang được lấy hàng tự động.",
                    show_alert=True,
                )
            elif sv == "payment_expired":
                await query.answer("⏰ Đơn hàng đã hết hạn thanh toán.", show_alert=True)
            else:
                await query.answer(f"Trạng thái: {_payment_status_label(ps)}", show_alert=True)
        finally:
            db.close()
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

            total_stock = sum((s.last_stock or 0) for s in sources if s.is_active)
            min_qty = p.min_quantity or 1

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

            # Skip confirmation — go directly to payment/order creation
            tg_user = update.effective_user

            # Delete user's quantity message (best-effort)
            try:
                await update.message.delete()
            except Exception:
                pass

            # Send a placeholder we can edit/delete inside _do_create_order
            processing_msg = await context.bot.send_message(
                chat_id=tg_user.id,
                text="⏳ Đang xử lý đơn hàng...",
            )

            context.user_data["state"] = "processing"
            try:
                await _do_create_order(context, db, tg_user, product_id, quantity, processing_msg)
            except Exception as e:
                logger.error(f"Order creation error (message_handler): {e}")
                try:
                    await processing_msg.edit_text("❌ Lỗi đặt hàng. Vui lòng thử lại.")
                except Exception:
                    pass
            context.user_data.clear()
        finally:
            db.close()
        return
