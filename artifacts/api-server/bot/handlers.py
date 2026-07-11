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
                    pass

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
            support = _get_support_username(db)

            # ── SePay flow ──
            from services.payment_service import is_sepay_enabled, create_pending_payment_order, generate_vietqr_url, get_sepay_config
            if is_sepay_enabled(db):
                cfg = get_sepay_config(db)

                # ── Validate bank config BEFORE creating order ──────────────
                missing = []
                if not (cfg and cfg.account_number and cfg.account_number.strip()):
                    missing.append("số tài khoản")
                if not (cfg and cfg.bank_bin and cfg.bank_bin.strip()):
                    missing.append("mã BIN ngân hàng")
                if not (cfg and cfg.account_name and cfg.account_name.strip()):
                    missing.append("tên chủ tài khoản")

                if missing:
                    await query.message.edit_text(
                        "⚠️ Không thể tạo đơn: chưa cấu hình đầy đủ thông tin ngân hàng.\n"
                        "Vui lòng liên hệ hỗ trợ."
                    )
                    # Notify admin with details
                    admin_id = _get_admin_id(db)
                    if admin_id:
                        try:
                            await context.bot.send_message(
                                chat_id=int(admin_id),
                                text=(
                                    "⚠️ <b>Thiếu cấu hình ngân hàng để tạo QR.</b>\n\n"
                                    f"Còn thiếu: {', '.join(missing)}\n"
                                    "Vào Settings → SePay để cập nhật."
                                ),
                                parse_mode="HTML",
                            )
                        except Exception:
                            pass
                    context.user_data.clear()
                    return
                # ────────────────────────────────────────────────────────────

                order = create_pending_payment_order(db, str(tg_user.id), product_id, quantity)
                product_name = order.product.name if order.product else str(product_id)
                expiry_minutes = cfg.payment_timeout_minutes or 15

                # ── Caption per spec ────────────────────────────────────────
                payment_text = (
                    f"💳 <b>THANH TOÁN ĐƠN HÀNG</b>\n\n"
                    f"Mã đơn: <code>{order.order_code}</code>\n"
                    f"Sản phẩm: {html.escape(product_name)}\n"
                    f"Số lượng: {quantity}\n"
                    f"Số tiền: <b>{order.total_price:,.0f}đ</b>\n\n"
                    f"Ngân hàng: {html.escape(cfg.bank_name or '—')}\n"
                    f"Số tài khoản: <code>{cfg.account_number}</code>\n"
                    f"Chủ tài khoản: {html.escape(cfg.account_name)}\n\n"
                    f"Nội dung chuyển khoản:\n"
                    f"<code>{order.payment_code}</code>\n\n"
                    f"⚠️ Vui lòng chuyển đúng số tiền và đúng nội dung.\n"
                    f"Đơn hết hạn sau {expiry_minutes} phút."
                )
                # ────────────────────────────────────────────────────────────

                shop_name = cfg.bank_name or "AI Center"
                qr_url = generate_vietqr_url(
                    cfg.bank_bin,
                    cfg.account_number,
                    order.total_price,
                    order.payment_code,
                    cfg.account_name,
                    shop_name,
                )

                kbd = payment_keyboard(order.id, support)

                # ── Send QR: try URL directly first, fallback to httpx ──────
                sent = False

                # Attempt 1: Telegram loads the image URL directly
                try:
                    await query.message.delete()
                    await context.bot.send_photo(
                        chat_id=tg_user.id,
                        photo=qr_url,
                        caption=payment_text,
                        parse_mode="HTML",
                        reply_markup=kbd,
                    )
                    sent = True
                except Exception as e1:
                    logger.warning(f"[payment] send_photo URL failed ({e1}), trying httpx download")

                # Attempt 2: download with httpx then send as bytes
                if not sent:
                    try:
                        import httpx as _httpx
                        async with _httpx.AsyncClient(timeout=15) as c:
                            r = await c.get(qr_url)
                        if r.status_code == 200:
                            await context.bot.send_photo(
                                chat_id=tg_user.id,
                                photo=io.BytesIO(r.content),
                                caption=payment_text,
                                parse_mode="HTML",
                                reply_markup=kbd,
                            )
                            sent = True
                        else:
                            raise Exception(f"QR HTTP {r.status_code}")
                    except Exception as e2:
                        logger.error(f"[payment] httpx QR download failed ({e2})")
                        admin_id = _get_admin_id(db)
                        if admin_id:
                            try:
                                await context.bot.send_message(
                                    chat_id=int(admin_id),
                                    text=(
                                        f"⚠️ Không tải được QR cho đơn <code>{order.order_code}</code>\n"
                                        f"URL: {qr_url[:200]}\nLỗi: {str(e2)[:200]}"
                                    ),
                                    parse_mode="HTML",
                                )
                            except Exception:
                                pass

                # Fallback: text-only payment info
                if not sent:
                    try:
                        await context.bot.send_message(
                            chat_id=tg_user.id,
                            text=payment_text,
                            parse_mode="HTML",
                            reply_markup=kbd,
                        )
                    except Exception:
                        pass
                # ────────────────────────────────────────────────────────────

                # Notify admin
                admin_id = _get_admin_id(db)
                if admin_id:
                    try:
                        from bot.notifier import notify_admin_new_payment_pending
                        await notify_admin_new_payment_pending(context.bot, order, admin_id)
                    except Exception:
                        pass

                context.user_data.clear()
                return

            # ── Direct API flow (SePay disabled) ──
            order = await create_order(db, str(tg_user.id), product_id, quantity)
            sv = order.status.value if hasattr(order.status, "value") else order.status
            product_name = order.product.name if order.product else str(product_id)

            if sv == "completed":
                items = get_delivery_items(order)
                if items:
                    text, file_bytes = format_delivery_message(order, items, product_name)
                    if file_bytes:
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
                    text, parse_mode="HTML",
                    reply_markup=partial_delivery_keyboard(order.id, support),
                )
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
            await query.message.edit_text(
                f"❌ Đơn hàng <code>{order.order_code}</code> đã bị hủy.",
                parse_mode="HTML",
            )
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

            total = p.sale_price * quantity

            # Show SePay notice if enabled
            from services.payment_service import is_sepay_enabled
            payment_note = "\n🔒 <i>Thanh toán qua chuyển khoản</i>" if is_sepay_enabled(db) else "\nNguồn giao: Tự động qua API"

            summary = (
                f"🛒 <b>XÁC NHẬN ĐƠN HÀNG</b>\n\n"
                f"Sản phẩm: {html.escape(p.name)}\n"
                f"Số lượng: {quantity}\n"
                f"Đơn giá: {p.sale_price:,.0f}đ\n"
                f"Tổng tiền: <b>{total:,.0f}đ</b>"
                f"{payment_note}"
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
