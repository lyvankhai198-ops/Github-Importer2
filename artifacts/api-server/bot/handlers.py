import io
import html
import json
import logging
from datetime import datetime, timedelta
from telegram import Update, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.ext import ContextTypes
from bot.keyboards import (
    main_menu_keyboard, product_list_keyboard, product_detail_keyboard,
    out_of_stock_keyboard, payment_keyboard, post_delivery_keyboard,
    partial_delivery_keyboard, language_keyboard, payment_method_keyboard,
    binance_manual_keyboard, binance_merchant_keyboard, crypto_payment_keyboard,
    confirm_order_keyboard,
)
from bot.i18n import t, get_user_lang
from services.product_service import (
    get_active_products_for_bot, get_product_detail, get_product_stock_status,
)
from services.order_service import create_order, get_or_create_user, get_order_by_id, get_delivery_items
from services.normalize import format_delivery_message, format_partial_delivery_message, format_vnd
from services.payment_service import (
    generate_vietqr_url, is_sepay_enabled, get_sepay_config,
    get_enabled_payment_methods, safe_delete_message as _safe_del,
    create_pending_payment_order, create_crypto_payment_order, create_binance_order,
    generate_payment_code,
)
from models import Order, TelegramBotConfig, OrderStatus, PaymentStatus, User
from database import SessionLocal

logger = logging.getLogger(__name__)

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

def _product_display_name(product, lang: str) -> str:
    """English name if available and lang=en, else the Vietnamese name."""
    if not product:
        return "—"
    if lang == "en" and getattr(product, "name_en", None):
        return product.name_en
    return product.name


def _get_lang(db, tg_user_id) -> str:
    return get_user_lang(db, str(tg_user_id))

def _get_products_per_page(db) -> int:
    cfg = _get_config(db)
    val = getattr(cfg, "products_per_page", None)
    return int(val) if val and val > 0 else 15

def _get_show_out_of_stock(db) -> bool:
    cfg = _get_config(db)
    val = getattr(cfg, "show_out_of_stock", None)
    return val if val is not None else True


async def _set_bot_commands(bot, lang: str = "vi", chat_id: int = None):
    """Set Telegram Menu commands for default scope or a specific chat."""
    commands_vi = [
        BotCommand("menu",     "Thông tin tài khoản"),
        BotCommand("product",  "Danh sách sản phẩm"),
        BotCommand("orders",   "Đơn hàng của tôi"),
        BotCommand("language", "Đổi ngôn ngữ"),
        BotCommand("support",  "Hỗ trợ"),
        BotCommand("myid",     "Lấy Telegram ID"),
    ]
    commands_en = [
        BotCommand("menu",     "Account information"),
        BotCommand("product",  "Product list"),
        BotCommand("orders",   "My orders"),
        BotCommand("language", "Change language"),
        BotCommand("support",  "Support"),
        BotCommand("myid",     "Get Telegram ID"),
    ]
    commands = commands_en if lang == "en" else commands_vi
    try:
        if chat_id:
            await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=chat_id))
        else:
            await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    except Exception as e:
        logger.warning(f"set_my_commands failed: {e}")


def _status_label(status_val: str, lang: str = "vi") -> str:
    labels_vi = {
        "pending_manual": "⏳ Chờ xử lý",
        "pending_payment": "💳 Chờ thanh toán",
        "processing_api": "🔄 Đang xử lý",
        "completed": "✅ Hoàn thành",
        "partial_delivery": "⚠️ Giao thiếu",
        "failed": "❌ Thất bại",
        "api_failed": "🚨 Lỗi sau thanh toán",
        "payment_expired": "⏰ Hết hạn TT",
        "cancelled": "🚫 Đã huỷ",
        "paid_waiting_stock": "⏳ Chờ hàng",
        "waiting_manual_verification": "⏳ Chờ xác nhận",
    }
    labels_en = {
        "pending_manual": "⏳ Pending",
        "pending_payment": "💳 Awaiting payment",
        "processing_api": "🔄 Processing",
        "completed": "✅ Completed",
        "partial_delivery": "⚠️ Partial delivery",
        "failed": "❌ Failed",
        "api_failed": "🚨 API error after payment",
        "payment_expired": "⏰ Expired",
        "cancelled": "🚫 Cancelled",
        "paid_waiting_stock": "⏳ Waiting for stock",
        "waiting_manual_verification": "⏳ Awaiting admin approval",
    }
    labels = labels_en if lang == "en" else labels_vi
    return labels.get(status_val, status_val)

def _payment_status_label(ps: str, lang: str = "vi") -> str:
    labels_vi = {
        "pending": "⏳ Chờ thanh toán",
        "partial": "⚠️ Thanh toán thiếu",
        "paid": "✅ Đã thanh toán đủ",
        "overpaid": "💰 Thanh toán thừa",
        "expired": "⏰ Hết hạn",
        "failed": "❌ Thất bại",
        "detected": "🔍 Đã phát hiện giao dịch",
        "confirming": "⏳ Đang xác nhận",
    }
    labels_en = {
        "pending": "⏳ Awaiting payment",
        "partial": "⚠️ Partial payment",
        "paid": "✅ Paid",
        "overpaid": "💰 Overpaid",
        "expired": "⏰ Expired",
        "failed": "❌ Failed",
        "detected": "🔍 Transaction detected",
        "confirming": "⏳ Confirming",
    }
    labels = labels_en if lang == "en" else labels_vi
    return labels.get(ps or "", ps or "—")


# ── Command handlers ──────────────────────────────────────────────────────────

async def _require_language_selected(update: Update, db) -> bool:
    """
    Gate helper for entry points other than /start (menu buttons, /menu,
    /orders, /support, ...). A brand-new user could in theory hit one of
    these before ever sending /start; if so, show the forced language
    picker and return False so the caller stops processing.
    """
    tg_user = update.effective_user
    user = get_or_create_user(db, str(tg_user.id), tg_user.username, tg_user.first_name, tg_user.last_name)
    if not user or not getattr(user, "language_selected", False):
        await update.message.reply_text(
            t("vi", "choose_lang"),
            reply_markup=language_keyboard(),
        )
        return False
    return True


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        tg_user = update.effective_user
        user = get_or_create_user(db, str(tg_user.id), tg_user.username, tg_user.first_name, tg_user.last_name)
        lang = get_user_lang(db, str(tg_user.id))

        # Brand-new users: force the language picker before anything else.
        # (language_code always defaults to "vi" at the DB level, so we gate
        # on the explicit language_selected flag instead.)
        if not user or not getattr(user, "language_selected", False):
            await update.message.reply_text(
                t("vi", "choose_lang"),
                reply_markup=language_keyboard(),
            )
            return

        admin_id = _get_admin_id(db)
        is_admin = str(tg_user.id) == str(admin_id)
        welcome = _get_welcome_message(db)
        await update.message.reply_text(
            welcome,
            reply_markup=main_menu_keyboard(lang=lang, is_admin=is_admin),
        )
    finally:
        db.close()


async def language_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for 🌐 Ngôn ngữ / Language menu button."""
    await update.message.reply_text(
        t("vi", "choose_lang"),
        reply_markup=language_keyboard(),
    )


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /menu command — shows account info + main menu."""
    db = SessionLocal()
    try:
        if not await _require_language_selected(update, db):
            return
        tg_user = update.effective_user
        user = get_or_create_user(db, str(tg_user.id), tg_user.username, tg_user.first_name, tg_user.last_name)
        lang = _get_lang(db, tg_user.id)
        admin_id = _get_admin_id(db)
        is_admin = str(tg_user.id) == str(admin_id)
        total_orders = getattr(user, "total_orders", 0) or 0
        is_banned = getattr(user, "is_banned", False)
        status_key = "user_status_banned" if is_banned else "user_status_active"
        lang_display = "Tiếng Việt" if lang == "vi" else "English"
        username_str = f"@{tg_user.username}" if tg_user.username else "—"
        text = t(lang, "menu_account_info",
                 tg_id=tg_user.id,
                 username=username_str,
                 language=lang_display,
                 total_orders=total_orders,
                 status=t(lang, status_key))
        await update.message.reply_text(
            text, parse_mode="HTML",
            reply_markup=main_menu_keyboard(lang=lang, is_admin=is_admin),
        )
    finally:
        db.close()


async def myid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /myid command — returns user's Telegram ID."""
    db = SessionLocal()
    try:
        lang = _get_lang(db, update.effective_user.id)
    finally:
        db.close()
    await update.message.reply_text(
        t(lang, "myid_response", tg_id=update.effective_user.id),
        parse_mode="HTML",
    )


async def products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        if not await _require_language_selected(update, db):
            return
        lang = _get_lang(db, update.effective_user.id)

        # Auto-sync every active API source before rendering — no manual
        # "Refresh" tap required. Skipped entirely if there's no active API
        # connection at all (nothing to sync).
        from models import ApiConnection
        from services.api_service import sync_active_supplier_products

        sync_failed = []
        status_msg = None
        if db.query(ApiConnection).filter(ApiConnection.is_active == True).first():
            status_msg = await update.message.reply_text(t(lang, "products_syncing"))
            try:
                sync_result = await sync_active_supplier_products(db)
                sync_failed = sync_result.get("failed", [])
                db.expire_all()  # pick up commits made by the parallel sync sessions
            except Exception as e:
                logger.error(f"[products_handler] sync_active_supplier_products failed: {e}")

        show_oos = _get_show_out_of_stock(db)
        per_page = _get_products_per_page(db)
        products = get_active_products_for_bot(db, show_out_of_stock=show_oos)

        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass

        if not products:
            await update.message.reply_text(t(lang, "product_list_empty"))
            return

        context.user_data["last_products_page"] = 0
        title = t(lang, "product_list_title")
        if sync_failed:
            title += "\n" + t(lang, "products_sync_partial_warning")
        await update.message.reply_text(
            title,
            parse_mode="HTML",
            reply_markup=product_list_keyboard(products, lang=lang, page=0, per_page=per_page),
        )
    finally:
        db.close()


async def orders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        if not await _require_language_selected(update, db):
            return
        lang = _get_lang(db, update.effective_user.id)
        tg_user = update.effective_user
        orders = (
            db.query(Order)
            .filter(Order.telegram_user_id == str(tg_user.id))
            .order_by(Order.created_at.desc())
            .limit(10)
            .all()
        )
        if not orders:
            await update.message.reply_text(t(lang, "orders_empty"))
            return
        lines = [t(lang, "orders_title")]
        for o in orders:
            sv = o.status.value if hasattr(o.status, "value") else o.status
            st = _status_label(sv, lang)
            if lang == "vi":
                price_str = f"{format_vnd(o.total_price)}đ"
            else:
                from services.normalize import format_usdt
                usdt_total = (o.product.price_usdt * o.quantity) if o.product else None
                if usdt_total is None:
                    from services.exchange_rate_service import get_exchange_config
                    from services.normalize import compute_price_usdt
                    rate = float(get_exchange_config(db).get("fixed_rate") or 26500.0)
                    usdt_total = compute_price_usdt(o.total_price, rate)
                price_str = f"{format_usdt(usdt_total)} USDT"
            lines.append(
                f"• <code>{o.order_code}</code> — {st}\n"
                f"  💰 {price_str} | {o.created_at.strftime('%d/%m/%Y')}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    finally:
        db.close()


async def support_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        if not await _require_language_selected(update, db):
            return
        lang = _get_lang(db, update.effective_user.id)
        support = _get_support_username(db)
        if support:
            await update.message.reply_text(t(lang, "support_contact", username=support))
        else:
            await update.message.reply_text(t(lang, "support_contact_admin"))
    finally:
        db.close()


async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🌐 Truy cập trang quản trị tại địa chỉ máy chủ của bạn.")


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /cancel (also triggered by "❌ Hủy bỏ" / "❌ Cancel" free text) — universal
    escape hatch: clears any in-progress flow (e.g. waiting_quantity) and
    returns the user to the main menu, from anywhere in the bot.
    """
    context.user_data.clear()
    db = SessionLocal()
    try:
        tg_user = update.effective_user
        lang = _get_lang(db, tg_user.id)
        admin_id = _get_admin_id(db)
        is_admin = str(tg_user.id) == str(admin_id)
        await update.message.reply_text(
            t(lang, "cancelled_returned_home"),
            reply_markup=main_menu_keyboard(lang=lang, is_admin=is_admin),
        )
    finally:
        db.close()


async def back_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Persistent "⬅️ Quay lại / Back" reply-keyboard button. Never disappears.
    If the shopper is mid quantity-entry, this cancels that step (instead of
    /cancel) and returns them to the product list page they were browsing.
    Otherwise it just re-shows the main menu.
    """
    state = context.user_data.get("state")
    db = SessionLocal()
    try:
        lang = _get_lang(db, update.effective_user.id)
        admin_id = _get_admin_id(db)
        is_admin = str(update.effective_user.id) == str(admin_id)

        if state == "waiting_quantity":
            prompt_id = context.user_data.get("quantity_prompt_message_id")
            if prompt_id:
                await _safe_del(context.bot, update.effective_chat.id, prompt_id)
            context.user_data.pop("state", None)
            context.user_data.pop("buying_product_id", None)
            context.user_data.pop("quantity_prompt_message_id", None)
            context.user_data.pop("processing_order", None)

            show_oos = _get_show_out_of_stock(db)
            per_page = _get_products_per_page(db)
            products = get_active_products_for_bot(db, show_out_of_stock=show_oos)
            page = context.user_data.get("last_products_page", 0)
            if products:
                await update.message.reply_text(
                    t(lang, "product_list_title"),
                    parse_mode="HTML",
                    reply_markup=product_list_keyboard(products, lang=lang, page=page, per_page=per_page),
                )
            else:
                await update.message.reply_text(
                    t(lang, "product_list_empty"),
                    reply_markup=main_menu_keyboard(lang=lang, is_admin=is_admin),
                )
            return

        welcome = _get_welcome_message(db)
        await update.message.reply_text(
            welcome,
            reply_markup=main_menu_keyboard(lang=lang, is_admin=is_admin),
        )
    finally:
        db.close()


# ── Payment setup helpers ─────────────────────────────────────────────────────

async def _setup_sepay_payment(context, db, tg_user, order, lang: str, processing_msg=None):
    """Set up SePay bank transfer for an existing order. Sends QR to user."""
    cfg = db.query(TelegramBotConfig).first()
    support = cfg.support_username if cfg else ""
    admin_id = cfg.admin_telegram_id if cfg else ""
    shop_name = getattr(cfg, "shop_name", "") or "" if cfg else ""
    sepay = db.query(__import__("models", fromlist=["SepayConfig"]).SepayConfig).first()

    if not sepay or not sepay.is_enabled:
        if processing_msg:
            try:
                await processing_msg.edit_text(t(lang, "payment_not_configured"))
            except Exception:
                pass
        return False

    if not sepay.account_number or not sepay.bank_bin or not sepay.account_name:
        if processing_msg:
            try:
                await processing_msg.edit_text(t(lang, "payment_not_configured"))
            except Exception:
                pass
        return False

    # Generate payment code if not already set
    if not order.payment_code:
        prefix = sepay.payment_prefix or "AIC"
        order.payment_code = generate_payment_code(order.order_code, prefix)

    order.payment_method = "bank_transfer"
    order.payment_currency = "VND"
    db.commit()

    product_name = _product_display_name(order.product, lang) if order.product else str(order.product_id)
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

    caption_lines = [
        t(lang, "sepay_payment_title"),
        "",
        t(lang, "sepay_order_code", code=order.order_code),
        t(lang, "sepay_product", name=html.escape(product_name)),
        t(lang, "sepay_qty", qty=order.quantity),
        t(lang, "sepay_amount", amount=f"{format_vnd(order.total_price)}"),
        "",
        t(lang, "sepay_bank", bank=html.escape(sepay.bank_bin)),
        t(lang, "sepay_account_number", acc=html.escape(sepay.account_number)),
        t(lang, "sepay_account_name", name=html.escape(sepay.account_name)),
        t(lang, "sepay_content", code=html.escape(order.payment_code)),
        "",
        t(lang, "sepay_expiry", time=expiry_str, min=timeout),
    ]
    caption = "\n".join(caption_lines)
    kbd = payment_keyboard(order.id, support, lang=lang)

    if processing_msg:
        try:
            await processing_msg.delete()
        except Exception:
            pass

    sent_msg = None
    try:
        sent_msg = await context.bot.send_photo(
            chat_id=tg_user.id, photo=qr_url, caption=caption,
            parse_mode="HTML", reply_markup=kbd,
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
                    chat_id=tg_user.id, photo=io.BytesIO(resp.content),
                    caption=caption, parse_mode="HTML", reply_markup=kbd,
                )
                order.payment_message_type = "photo"
        except Exception:
            pass

    if not sent_msg:
        text_only = caption + f'\n\n🔗 <a href="{qr_url}">Mở QR VietQR</a>'
        try:
            sent_msg = await context.bot.send_message(
                chat_id=tg_user.id, text=text_only, parse_mode="HTML",
                reply_markup=payment_keyboard(order.id, support, lang=lang, show_regen_qr=True),
                disable_web_page_preview=True,
            )
            order.payment_message_type = "text"
        except Exception as e:
            logger.error(f"[order] could not send payment message for {order.order_code}: {e}")
            return False

    if sent_msg:
        order.payment_message_id = sent_msg.message_id
        order.payment_chat_id = tg_user.id
        db.commit()
    return True


async def _setup_binance_payment(context, db, tg_user, order, lang: str, processing_msg=None):
    """Set up Binance Pay for an existing order."""
    from services.binance_service import get_binance_config, create_binance_merchant_order
    from services.exchange_rate_service import calculate_crypto_amount, generate_unique_crypto_amount

    cfg_bot = db.query(TelegramBotConfig).first()
    support = cfg_bot.support_username if cfg_bot else ""
    admin_id = cfg_bot.admin_telegram_id if cfg_bot else ""

    bnb_cfg = get_binance_config(db)
    if not bnb_cfg:
        if processing_msg:
            try:
                await processing_msg.edit_text(t(lang, "payment_method_disabled"))
            except Exception:
                pass
        return False

    base_usdt, rate = await calculate_crypto_amount(db, order.total_price)
    unique_usdt = generate_unique_crypto_amount(db, base_usdt, "BINANCE")
    mode = bnb_cfg.get("mode", "manual")
    timeout = int(bnb_cfg.get("timeout_minutes") or 30)

    order.payment_method = "binance_pay"
    order.payment_currency = "USDT"
    order.exchange_rate = rate
    order.expected_crypto_amount = unique_usdt
    order.payment_network = "BINANCE"
    order.payment_chat_id = tg_user.id

    if processing_msg:
        try:
            await processing_msg.delete()
        except Exception:
            pass

    product_name = _product_display_name(order.product, lang) if order.product else str(order.product_id)

    if mode == "manual":
        pay_id = bnb_cfg.get("pay_id") or "—"
        recipient = bnb_cfg.get("recipient_name") or "—"
        order.status = OrderStatus.waiting_manual_verification
        db.commit()

        text = "\n".join([
            t(lang, "binance_manual_title"),
            "",
            t(lang, "binance_pay_id", pay_id=pay_id),
            t(lang, "binance_recipient", name=recipient),
            t(lang, "binance_amount", amount=f"{unique_usdt:.4f}"),
            t(lang, "binance_order_code", code=order.order_code),
            "",
            t(lang, "binance_instruction"),
        ])
        qr_path = bnb_cfg.get("qr_image_path") or ""
        kbd = binance_manual_keyboard(order.id, support, lang=lang)

        sent_msg = None
        if qr_path:
            try:
                sent_msg = await context.bot.send_photo(
                    chat_id=tg_user.id, photo=open(qr_path, "rb"),
                    caption=text, parse_mode="HTML", reply_markup=kbd,
                )
                order.payment_message_type = "photo"
            except Exception:
                pass
        if not sent_msg:
            try:
                sent_msg = await context.bot.send_message(
                    chat_id=tg_user.id, text=text, parse_mode="HTML",
                    reply_markup=kbd,
                )
                order.payment_message_type = "text"
            except Exception as e:
                logger.error(f"[binance_manual] send error: {e}")
                return False

        # Notify admin about manual verification needed
        if admin_id:
            try:
                await context.bot.send_message(
                    chat_id=int(admin_id),
                    text=(
                        f"🟡 <b>Binance Pay – chờ xác nhận thủ công!</b>\n\n"
                        f"📋 Đơn: <code>{order.order_code}</code>\n"
                        f"👤 User: <code>{tg_user.id}</code>\n"
                        f"📦 Sản phẩm: {html.escape(product_name)}\n"
                        f"💰 Số tiền: <b>{unique_usdt:.4f} USDT</b>\n\n"
                        f"Dùng lệnh admin để xác nhận hoặc từ chối."
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass

    else:  # Merchant API
        api_key = bnb_cfg.get("api_key") or ""
        secret_key = bnb_cfg.get("secret_key") or ""
        result = await create_binance_merchant_order(
            api_key=api_key, secret_key=secret_key,
            merchant_trade_no=order.order_code,
            amount_usdt=unique_usdt,
            description=product_name,
            timeout_minutes=timeout,
        )
        if not result.get("success"):
            msg = result.get("message", "Binance API error")
            logger.error(f"[binance_merchant] create order failed: {msg}")
            if processing_msg:
                try:
                    await processing_msg.edit_text(t(lang, "order_error"))
                except Exception:
                    pass
            return False

        data = result.get("data", {})
        prepay_id = data.get("prepayId") or ""
        checkout_url = data.get("checkoutUrl") or data.get("universalUrl") or ""
        order.payment_txid = prepay_id
        order.payment_address = checkout_url
        db.commit()

        text = "\n".join([
            f"🟡 <b>BINANCE PAY</b>",
            "",
            f"Amount: <b>{unique_usdt:.4f} USDT</b>",
            f"Order: <code>{order.order_code}</code>",
            "",
            "Open Binance Pay to complete payment.",
        ])
        kbd = binance_merchant_keyboard(order.id, checkout_url, support, lang=lang)
        try:
            sent_msg = await context.bot.send_message(
                chat_id=tg_user.id, text=text, parse_mode="HTML", reply_markup=kbd,
            )
            order.payment_message_type = "text"
            order.payment_message_id = sent_msg.message_id
            db.commit()
        except Exception as e:
            logger.error(f"[binance_merchant] send message error: {e}")
            return False

    if sent_msg if mode == "manual" else True:
        if mode == "manual" and 'sent_msg' in dir() and sent_msg:
            order.payment_message_id = sent_msg.message_id
        db.commit()
    return True


async def _setup_crypto_payment(context, db, tg_user, order, lang: str,
                                 method: str, processing_msg=None):
    """Set up BEP20 or TRC20 USDT payment for an existing order."""
    from models import PaymentMethod
    from crypto import decrypt
    from services.exchange_rate_service import calculate_crypto_amount, generate_unique_crypto_amount

    cfg_bot = db.query(TelegramBotConfig).first()
    support = cfg_bot.support_username if cfg_bot else ""

    pm = db.query(PaymentMethod).filter(
        PaymentMethod.method_code == method,
        PaymentMethod.is_active == True,
    ).first()
    if not pm or not pm.config_encrypted:
        if processing_msg:
            try:
                await processing_msg.edit_text(t(lang, "payment_method_disabled"))
            except Exception:
                pass
        return False

    try:
        pm_cfg = json.loads(decrypt(pm.config_encrypted) or "{}")
    except Exception:
        pm_cfg = {}

    wallet = (pm_cfg.get("wallet_address") or "").strip()
    if not wallet:
        if processing_msg:
            try:
                await processing_msg.edit_text(t(lang, "payment_method_disabled"))
            except Exception:
                pass
        return False

    network = {"usdt_bep20": "BEP20", "usdt_trc20": "TRC20", "usdt_erc20": "ERC20"}[method]
    required_conf = int(pm_cfg.get("required_confirmations") or (20 if network == "TRC20" else 12))
    timeout = int(pm_cfg.get("timeout_minutes") or 60)

    base_usdt, rate = await calculate_crypto_amount(db, order.total_price)
    unique_usdt = generate_unique_crypto_amount(db, base_usdt, network)

    order.payment_method = method
    order.payment_currency = "USDT"
    order.exchange_rate = rate
    order.expected_crypto_amount = unique_usdt
    order.payment_address = wallet
    order.payment_network = network
    order.required_confirmations = required_conf
    order.confirmations = 0
    order.payment_chat_id = tg_user.id
    db.commit()

    if method == "usdt_bep20":
        title = t(lang, "usdt_bep20_title")
        network_line = t(lang, "usdt_bep20_network")
        token_line = t(lang, "usdt_bep20_token")
        addr_line = t(lang, "usdt_bep20_address", address=wallet)
        amount_line = t(lang, "usdt_bep20_amount", amount=f"{unique_usdt:.4f}")
        warning_line = t(lang, "usdt_bep20_warning")
        order_line = t(lang, "usdt_bep20_order", code=order.order_code)
    elif method == "usdt_trc20":
        title = t(lang, "usdt_trc20_title")
        network_line = t(lang, "usdt_trc20_network")
        token_line = t(lang, "usdt_trc20_token")
        addr_line = t(lang, "usdt_trc20_address", address=wallet)
        amount_line = t(lang, "usdt_trc20_amount", amount=f"{unique_usdt:.4f}")
        warning_line = t(lang, "usdt_trc20_warning")
        order_line = t(lang, "usdt_trc20_order", code=order.order_code)
    else:
        title = t(lang, "usdt_erc20_title")
        network_line = t(lang, "usdt_erc20_network")
        token_line = t(lang, "usdt_erc20_token")
        addr_line = t(lang, "usdt_erc20_address", address=wallet)
        amount_line = t(lang, "usdt_erc20_amount", amount=f"{unique_usdt:.4f}")
        warning_line = t(lang, "usdt_erc20_warning")
        order_line = t(lang, "usdt_erc20_order", code=order.order_code)

    text = "\n".join([title, "", network_line, token_line, "", addr_line, "", amount_line, "", warning_line, "", order_line])
    kbd = crypto_payment_keyboard(order.id, support, lang=lang)

    if processing_msg:
        try:
            await processing_msg.delete()
        except Exception:
            pass

    try:
        sent_msg = await context.bot.send_message(
            chat_id=tg_user.id, text=text, parse_mode="HTML", reply_markup=kbd,
        )
        order.payment_message_id = sent_msg.message_id
        order.payment_message_type = "text"
        db.commit()
    except Exception as e:
        logger.error(f"[crypto] send payment message error: {e}")
        return False

    return True


async def _do_create_order(context, db, tg_user, product_id: int, quantity: int, processing_msg):
    """
    Create a pending_payment order (no method yet) and show payment method selection.
    """
    lang = _get_lang(db, tg_user.id)
    product = db.query(__import__("models", fromlist=["Product"]).Product).filter(
        __import__("models", fromlist=["Product"]).Product.id == product_id
    ).first()
    if not product:
        try:
            await processing_msg.edit_text(t(lang, "product_not_found"))
        except Exception:
            pass
        return

    # Final stock check before creating order
    stock_info = get_product_stock_status(product_id, db)
    from models import DeliveryMode
    if product.delivery_mode in (DeliveryMode.api_auto, DeliveryMode.manual_stock):
        if stock_info["status"] == "out_of_stock":
            try:
                await processing_msg.edit_text(t(lang, "product_out_of_stock_recheck"))
            except Exception:
                pass
            return
        if stock_info["status"] != "unavailable" and stock_info["stock"] > 0 and quantity > stock_info["stock"]:
            try:
                await processing_msg.edit_text(
                    t(lang, "qty_exceeds_stock", stock=stock_info["stock"], qty=quantity)
                )
            except Exception:
                pass
            return

    # Get enabled payment methods
    enabled_methods = get_enabled_payment_methods(db)
    if not enabled_methods:
        try:
            await processing_msg.edit_text(t(lang, "payment_not_configured"))
        except Exception:
            pass
        return

    # Create order (method to be set after user selects)
    cfg_bot = db.query(TelegramBotConfig).first()
    sepay = db.query(__import__("models", fromlist=["SepayConfig"]).SepayConfig).first()
    prefix = (sepay.payment_prefix or "AIC") if sepay else "AIC"
    timeout = (sepay.payment_timeout_minutes or 15) if sepay else 15

    import uuid
    from datetime import timedelta
    order_code = "ORD-" + uuid.uuid4().hex[:8].upper()
    total = product.sale_price * quantity

    from models import Order, OrderStatus, PaymentStatus
    order = Order(
        order_code=order_code,
        telegram_user_id=str(tg_user.id),
        product_id=product_id,
        quantity=quantity,
        origin_products_page=context.user_data.get("last_products_page", 0),
        unit_price=product.sale_price,
        total_price=total,
        expected_amount=total,
        paid_amount=0.0,
        status=OrderStatus.pending_payment,
        payment_status=PaymentStatus.pending,
        payment_expires_at=datetime.utcnow() + timedelta(minutes=timeout),
        payment_chat_id=tg_user.id,
        product_message_id=context.user_data.get("product_message_id"),
        quantity_prompt_message_id=context.user_data.get("quantity_prompt_message_id"),
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    user = db.query(User).filter(User.telegram_id == str(tg_user.id)).first()
    if user:
        user.last_active_at = datetime.utcnow()
        db.commit()

    # Build payment method selection message
    from services.normalize import format_usdt
    product_name = _product_display_name(product, lang)
    total_str = format_usdt(product.price_usdt * quantity) if lang == "en" else format_vnd(total)
    text = t(lang, "choose_payment_title",
             order_code=order.order_code,
             product=html.escape(product_name),
             qty=quantity,
             total=total_str)
    kbd = payment_method_keyboard(order.id, enabled_methods, lang=lang)

    try:
        await processing_msg.delete()
    except Exception:
        pass

    try:
        sent = await context.bot.send_message(
            chat_id=tg_user.id, text=text, parse_mode="HTML", reply_markup=kbd,
        )
        order.payment_message_id = sent.message_id
        order.payment_message_type = "text"
        db.commit()
    except Exception as e:
        logger.error(f"[order] could not send payment method selection: {e}")


# ── Callback query handler ────────────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    # ── oos: out-of-stock product clicked → popup only, no new message ──
    if data.startswith("oos:"):
        db = SessionLocal()
        try:
            lang = _get_lang(db, update.effective_user.id)
            support = _get_support_username(db)
        finally:
            db.close()
        if support:
            popup_text = t(lang, "oos_popup", support=support.lstrip("@"))
        else:
            popup_text = t(lang, "oos_popup_no_support")
        await query.answer(text=popup_text[:200], show_alert=True)
        return

    # ── notify_restock: shopper opts into the per-product "back in stock" waiting list ──
    if data.startswith("notify_restock:"):
        product_id = int(data.split(":")[1])
        db = SessionLocal()
        try:
            lang = _get_lang(db, update.effective_user.id)
            from models import RestockSubscription
            existing = db.query(RestockSubscription).filter(
                RestockSubscription.product_id == product_id,
                RestockSubscription.telegram_user_id == str(update.effective_user.id),
            ).first()
            if existing:
                popup_text = t(lang, "notify_restock_already")
            else:
                db.add(RestockSubscription(
                    product_id=product_id,
                    telegram_user_id=str(update.effective_user.id),
                ))
                db.commit()
                popup_text = t(lang, "notify_restock_subscribed")
        except Exception:
            db.rollback()
            popup_text = t(lang, "notify_restock_already")
        finally:
            db.close()
        await query.answer(text=popup_text[:200], show_alert=True)
        return

    # ── noop (pagination page indicator button) ──
    if data == "noop":
        await query.answer()
        return

    await query.answer()

    # ── products_page ──
    if data.startswith("products_page:"):
        page = int(data.split(":")[1])
        db = SessionLocal()
        try:
            lang = _get_lang(db, update.effective_user.id)
            show_oos = _get_show_out_of_stock(db)
            per_page = _get_products_per_page(db)
            products = get_active_products_for_bot(db, show_out_of_stock=show_oos)
            context.user_data["last_products_page"] = page
            await query.message.edit_text(
                t(lang, "product_list_title"),
                parse_mode="HTML",
                reply_markup=product_list_keyboard(products, lang=lang, page=page, per_page=per_page),
            )
        except Exception:
            pass
        finally:
            db.close()
        return

    # ── refresh_products ──
    if data.startswith("refresh_products:"):
        page = int(data.split(":")[1])
        db = SessionLocal()
        try:
            lang = _get_lang(db, update.effective_user.id)
            # Sync all active API connections
            from services.api_service import sync_api_products
            from models import ApiConnection
            connections = db.query(ApiConnection).filter(ApiConnection.is_active == True).all()
            for conn in connections:
                try:
                    await sync_api_products(db, conn.id)
                except Exception:
                    pass
            db.expire_all()
            show_oos = _get_show_out_of_stock(db)
            per_page = _get_products_per_page(db)
            products = get_active_products_for_bot(db, show_out_of_stock=show_oos)
            context.user_data["last_products_page"] = page
            await query.message.edit_text(
                t(lang, "product_list_title"),
                parse_mode="HTML",
                reply_markup=product_list_keyboard(products, lang=lang, page=page, per_page=per_page),
            )
            await query.answer(t(lang, "product_list_refreshed"), show_alert=False)
        except Exception as e:
            logger.warning(f"[refresh_products] error: {e}")
        finally:
            db.close()
        return

    # ── set_lang ──
    if data.startswith("set_lang:"):
        lang_code = data.split(":")[1]
        if lang_code not in ("vi", "en"):
            return
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            user = db.query(User).filter(User.telegram_id == str(tg_user.id)).first()
            if user:
                user.language_code = lang_code
                user.language_selected = True
                db.commit()
            admin_id = _get_admin_id(db)
            is_admin = str(tg_user.id) == str(admin_id)
            await query.message.reply_text(
                t(lang_code, "lang_changed"),
                reply_markup=main_menu_keyboard(lang=lang_code, is_admin=is_admin),
            )
            try:
                await query.message.delete()
            except Exception:
                pass
            # Update Telegram Menu commands for this chat in the chosen language
            await _set_bot_commands(context.bot, lang_code, chat_id=int(tg_user.id))
        finally:
            db.close()
        return

    # ── close ──
    if data == "close":
        await query.message.delete()
        return

    # ── home ──
    if data == "home":
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)
            admin_id = _get_admin_id(db)
            is_admin = str(tg_user.id) == str(admin_id)
            welcome = _get_welcome_message(db)
            await query.message.reply_text(
                welcome,
                reply_markup=main_menu_keyboard(lang=lang, is_admin=is_admin),
            )
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
            lang = _get_lang(db, update.effective_user.id)
            show_oos = _get_show_out_of_stock(db)
            per_page = _get_products_per_page(db)
            products = get_active_products_for_bot(db, show_out_of_stock=show_oos)
            page = context.user_data.get("last_products_page", 0)
            await query.message.edit_text(
                t(lang, "product_list_title"),
                parse_mode="HTML",
                reply_markup=product_list_keyboard(products, lang=lang, page=page, per_page=per_page),
            )
        finally:
            db.close()
        return

    # ── product detail ──
    if data.startswith("product:"):
        product_id = int(data.split(":")[1])
        db = SessionLocal()
        try:
            lang = _get_lang(db, update.effective_user.id)
            detail = get_product_detail(db, product_id)
            if not detail:
                await query.message.edit_text(t(lang, "product_not_found"))
                return
            p = detail["product"]
            sources = detail["sources"]

            # Freshness check — re-sync if stale (>60s)
            from models import DeliveryMode
            if p.delivery_mode == DeliveryMode.api_auto:
                for src in sources:
                    if src.api_product and src.api_product.last_sync_at:
                        age = datetime.utcnow() - src.api_product.last_sync_at
                        if age > timedelta(seconds=60):
                            from services.api_service import sync_api_products
                            await sync_api_products(db, src.api_product.api_connection_id)
                            db.expire_all()
                            detail = get_product_detail(db, product_id)
                            if detail:
                                p = detail["product"]
                                sources = detail["sources"]
                            break

            stock_info = get_product_stock_status(product_id, db)
            stock = stock_info["stock"]
            status = stock_info["status"]

            # Determine stock text
            if p.delivery_mode != DeliveryMode.manual_stock and p.delivery_mode != DeliveryMode.api_auto:
                # manual_admin (and legacy "manual") — no local inventory tracked
                stock_text = f"🟡 {t(lang, 'product_list_accept_order')}"
            elif status == "unavailable":
                stock_text = t(lang, "product_unavailable")
            elif status == "out_of_stock":
                stock_text = t(lang, "product_out_of_stock")
            elif stock > 10:
                stock_text = t(lang, "product_in_stock", count=stock)
            else:
                stock_text = t(lang, "product_low_stock", count=stock)

            min_qty = p.min_quantity or 1
            api_src = None
            for src in sources:
                if src.is_active and src.api_product:
                    api_src = src.api_product
                    break

            from services.normalize import format_usdt, translate_shorthand_to_en

            # Name: use English name if lang=en and available, else fall back
            # to the Vietnamese name (never show a bare product code).
            display_name = p.name
            if lang == "en":
                if getattr(p, "name_en", None):
                    display_name = p.name_en
                else:
                    # Defensive fallback — see bot/keyboards.py for why.
                    display_name = translate_shorthand_to_en(p.name)

            detail_icon = (getattr(p, "telegram_icon", None) or "").strip() or "📦"
            if status in ("out_of_stock", "unavailable"):
                detail_icon = "❌"
            lines = [f"{detail_icon} <b>{html.escape(display_name)}</b>\n"]
            if lang == "en":
                lines.append(t(lang, "product_price", price=format_usdt(p.price_usdt)))
            else:
                lines.append(t(lang, "product_price", price=f"{format_vnd(p.sale_price)}"))
            lines.append(stock_text)
            lines.append(t(lang, "product_min_qty", qty=min_qty))
            if p.duration:
                dur = translate_shorthand_to_en(p.duration) if lang == "en" else p.duration
                lines.append(t(lang, "product_duration", val=html.escape(dur)))
            if p.warranty:
                warr = translate_shorthand_to_en(p.warranty) if lang == "en" else p.warranty
                lines.append(t(lang, "product_warranty", val=html.escape(warr)))

            # Description: use EN if lang=en and available
            desc = None
            if lang == "en" and getattr(p, "description_en", None):
                desc = p.description_en
            else:
                desc = p.description or (api_src.external_description if api_src else None)
            if desc:
                if lang == "en":
                    desc = translate_shorthand_to_en(desc)
                lines.append(t(lang, "product_description", desc=html.escape(desc)))

            text_msg = "\n".join(lines)
            image_url = p.image_path or (api_src.external_image_url if api_src else None)

            # Out-of-stock: show blocking keyboard
            if status == "out_of_stock":
                kb = out_of_stock_keyboard(p.id, lang=lang)
                full_text = f"{text_msg}\n\n{t(lang, 'out_of_stock_title')}\n{t(lang, 'out_of_stock_body')}"
                if image_url and image_url.startswith("http"):
                    try:
                        import httpx
                        async with httpx.AsyncClient(timeout=10) as c:
                            resp = await c.get(image_url)
                        if resp.status_code == 200:
                            sent = await query.message.reply_photo(
                                photo=io.BytesIO(resp.content),
                                caption=full_text[:1024], parse_mode="HTML", reply_markup=kb,
                            )
                            context.user_data["product_message_id"] = sent.message_id
                            await query.message.delete()
                            return
                    except Exception:
                        pass
                await query.message.edit_text(full_text, parse_mode="HTML", reply_markup=kb)
                context.user_data["product_message_id"] = query.message.message_id
                return

            # Normal detail — show buy button
            kb = product_detail_keyboard(p.id, lang=lang)
            if image_url:
                try:
                    if image_url.startswith("http"):
                        import httpx
                        async with httpx.AsyncClient(timeout=10) as c:
                            resp = await c.get(image_url)
                        if resp.status_code == 200:
                            sent = await query.message.reply_photo(
                                photo=io.BytesIO(resp.content),
                                caption=text_msg, parse_mode="HTML", reply_markup=kb,
                            )
                            context.user_data["product_message_id"] = sent.message_id
                            await query.message.delete()
                            return
                    else:
                        sent = await query.message.reply_photo(
                            photo=open(image_url, "rb"),
                            caption=text_msg, parse_mode="HTML", reply_markup=kb,
                        )
                        context.user_data["product_message_id"] = sent.message_id
                        await query.message.delete()
                        return
                except Exception:
                    pass

            sent = await query.message.edit_text(text_msg, parse_mode="HTML", reply_markup=kb)
            context.user_data["product_message_id"] = query.message.message_id
        finally:
            db.close()
        return

    # ── buy: enter quantity ──
    if data.startswith("buy:"):
        product_id = int(data.split(":")[1])
        db = SessionLocal()
        try:
            lang = _get_lang(db, update.effective_user.id)
            # Server-side stock check before allowing buy
            stock_info = get_product_stock_status(product_id, db)
            from models import DeliveryMode, Product as Prod
            product = db.query(Prod).filter(Prod.id == product_id).first()
            if product and product.delivery_mode in (DeliveryMode.api_auto, DeliveryMode.manual_stock):
                if stock_info["status"] == "out_of_stock":
                    await query.answer(t(lang, "product_out_of_stock_recheck"), show_alert=True)
                    return
        finally:
            db.close()

        context.user_data["buying_product_id"] = product_id
        context.user_data["state"] = "waiting_quantity"
        context.user_data.pop("processing_order", None)
        db2 = SessionLocal()
        try:
            lang = _get_lang(db2, update.effective_user.id)
        finally:
            db2.close()
        prompt_msg = await query.message.reply_text(t(lang, "enter_quantity"))
        context.user_data["quantity_prompt_message_id"] = prompt_msg.message_id
        return

    # ── pay_method: select payment method for existing order ──
    if data.startswith("pay_method:"):
        parts = data.split(":")
        order_id = int(parts[1])
        method = parts[2]
        tg_user = update.effective_user

        cb_key = f"{tg_user.id}:{data}"
        if cb_key in _processing_callbacks:
            return
        _processing_callbacks.add(cb_key)

        db = SessionLocal()
        try:
            lang = _get_lang(db, tg_user.id)
            order = get_order_by_id(db, order_id)
            if not order or order.telegram_user_id != str(tg_user.id):
                await query.answer(t(lang, "order_not_found"), show_alert=True)
                return

            sv = order.status.value if hasattr(order.status, "value") else str(order.status)
            if sv != "pending_payment":
                await query.answer(t(lang, "order_not_found"), show_alert=True)
                return

            # Delete the payment method selection message
            try:
                await query.message.delete()
            except Exception:
                pass

            processing_msg = await context.bot.send_message(
                chat_id=tg_user.id,
                text=t(lang, "processing_order"),
            )

            if method == "bank_transfer":
                await _setup_sepay_payment(context, db, tg_user, order, lang, processing_msg)
            elif method == "binance_pay":
                await _setup_binance_payment(context, db, tg_user, order, lang, processing_msg)
            elif method in ("usdt_bep20", "usdt_trc20", "usdt_erc20"):
                await _setup_crypto_payment(context, db, tg_user, order, lang, method, processing_msg)
            else:
                try:
                    await processing_msg.edit_text(t(lang, "payment_method_disabled"))
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"[pay_method] error: {e}")
        finally:
            db.close()
            _processing_callbacks.discard(cb_key)
        return

    # ── cancel_order (pre-payment method selection, legacy) ──
    if data == "cancel_order":
        db = SessionLocal()
        try:
            lang = _get_lang(db, update.effective_user.id)
            page = context.user_data.get("last_products_page", 0)
            show_oos = _get_show_out_of_stock(db)
            per_page = _get_products_per_page(db)
            products = get_active_products_for_bot(db, show_out_of_stock=show_oos)
        finally:
            db.close()
        context.user_data.pop("state", None)
        context.user_data.pop("buying_product_id", None)
        context.user_data.pop("quantity_prompt_message_id", None)
        context.user_data.pop("processing_order", None)
        if products:
            await query.message.edit_text(
                f"{t(lang, 'order_cancelled')}\n\n{t(lang, 'product_list_title')}",
                parse_mode="HTML",
                reply_markup=product_list_keyboard(products, lang=lang, page=page, per_page=per_page),
            )
        else:
            await query.message.edit_text(t(lang, "order_cancelled"))
        return

    # ── cancel_pending ──
    if data.startswith("cancel_pending:"):
        order_id = int(data.split(":")[1])
        tg_user = update.effective_user
        db = SessionLocal()
        try:
            lang = _get_lang(db, tg_user.id)
            order = get_order_by_id(db, order_id)
            if not order or order.telegram_user_id != str(tg_user.id):
                await query.answer(t(lang, "order_not_found"), show_alert=True)
                return

            ps = order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status or "")
            if ps in ("paid", "overpaid"):
                await query.answer(t(lang, "order_cancel_paid"), show_alert=True)
                return

            order.status = OrderStatus.cancelled
            order.updated_at = datetime.utcnow()
            page = order.origin_products_page or 0
            db.commit()

            chat_id = order.payment_chat_id or order.telegram_user_id
            # QR/instruction message is only ever deleted here because the order was
            # never paid — a paid order is blocked above and its QR is cleaned up by
            # the delivery flow instead, never by this cancel path.
            await _safe_del(context.bot, chat_id, order.product_message_id)
            await _safe_del(context.bot, chat_id, order.quantity_prompt_message_id)
            await _safe_del(context.bot, chat_id, order.payment_message_id)

            # Clear any leftover quantity-input state for this chat so the shopper
            # isn't stuck mid-flow after cancelling.
            context.user_data.pop("state", None)
            context.user_data.pop("buying_product_id", None)
            context.user_data.pop("quantity_prompt_message_id", None)
            context.user_data.pop("processing_order", None)

            show_oos = _get_show_out_of_stock(db)
            per_page = _get_products_per_page(db)
            products = get_active_products_for_bot(db, show_out_of_stock=show_oos)
            try:
                if products:
                    context.user_data["last_products_page"] = page
                    await context.bot.send_message(
                        chat_id=int(chat_id),
                        text=f"{t(lang, 'order_cancel_success')}\n\n{t(lang, 'product_list_title')}",
                        parse_mode="HTML",
                        reply_markup=product_list_keyboard(products, lang=lang, page=page, per_page=per_page),
                    )
                else:
                    await context.bot.send_message(
                        chat_id=int(chat_id), text=t(lang, "order_cancel_success")
                    )
            except Exception:
                pass
        finally:
            db.close()
        return

    # ── regen_qr ──
    if data.startswith("regen_qr:"):
        order_id = int(data.split(":")[1])
        tg_user = update.effective_user
        db = SessionLocal()
        try:
            lang = _get_lang(db, tg_user.id)
            order = get_order_by_id(db, order_id)
            if not order or order.telegram_user_id != str(tg_user.id):
                await query.answer(t(lang, "order_not_found"), show_alert=True)
                return

            sv = order.status.value if hasattr(order.status, "value") else str(order.status)
            if sv != "pending_payment":
                await query.answer("Đơn không còn ở trạng thái chờ thanh toán.", show_alert=True)
                return

            from models import SepayConfig
            cfg_bot = db.query(TelegramBotConfig).first()
            support = cfg_bot.support_username if cfg_bot else ""
            shop_name = getattr(cfg_bot, "shop_name", "") or "" if cfg_bot else ""
            sepay = db.query(SepayConfig).first()
            if not sepay or not sepay.account_number or not sepay.bank_bin:
                await query.answer("Cấu hình ngân hàng chưa đầy đủ.", show_alert=True)
                return

            chat_id = order.payment_chat_id or order.telegram_user_id
            await _safe_del(context.bot, chat_id, order.payment_message_id)
            order.payment_message_id = None
            db.commit()

            product_name = _product_display_name(order.product, lang) if order.product else "—"
            timeout = sepay.payment_timeout_minutes or 15
            expiry_str = order.payment_expires_at.strftime("%H:%M %d/%m/%Y") if order.payment_expires_at else "—"

            qr_url = generate_vietqr_url(
                bank_bin=sepay.bank_bin, account_number=sepay.account_number,
                amount=order.total_price, payment_code=order.payment_code,
                account_name=sepay.account_name, shop_name=shop_name,
            )
            caption_lines = [
                t(lang, "sepay_payment_title"), "",
                t(lang, "sepay_order_code", code=order.order_code),
                t(lang, "sepay_product", name=html.escape(product_name)),
                t(lang, "sepay_qty", qty=order.quantity),
                t(lang, "sepay_amount", amount=f"{format_vnd(order.total_price)}"),
                "", t(lang, "sepay_bank", bank=html.escape(sepay.bank_bin)),
                t(lang, "sepay_account_number", acc=html.escape(sepay.account_number)),
                t(lang, "sepay_account_name", name=html.escape(sepay.account_name)),
                t(lang, "sepay_content", code=html.escape(order.payment_code or "")),
                "", t(lang, "sepay_expiry", time=expiry_str, min=timeout),
            ]
            caption = "\n".join(caption_lines)
            kbd = payment_keyboard(order.id, support, lang=lang)

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
                        reply_markup=payment_keyboard(order.id, support, lang=lang, show_regen_qr=True),
                        disable_web_page_preview=True,
                    )
                    order.payment_message_type = "text"
                except Exception as e:
                    logger.error(f"[regen_qr] send failed: {e}")
                    await query.answer("Không thể tạo QR. Vui lòng thử lại.", show_alert=True)
                    return

            if sent_msg:
                order.payment_message_id = sent_msg.message_id
                db.commit()
            await query.answer("✅ Đã tạo lại QR.")
        finally:
            db.close()
        return

    # ── check_payment ──
    if data.startswith("check_payment:"):
        order_id = int(data.split(":")[1])
        tg_user = update.effective_user
        db = SessionLocal()
        try:
            lang = _get_lang(db, tg_user.id)
            order = get_order_by_id(db, order_id)
            if not order or order.telegram_user_id != str(tg_user.id):
                await query.answer(t(lang, "order_not_found"), show_alert=True)
                return

            sv = order.status.value if hasattr(order.status, "value") else str(order.status)
            ps = order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status or "pending")

            if sv == "completed":
                await query.answer("✅ Đơn đã hoàn thành." if lang == "vi" else "✅ Order completed.", show_alert=True)
                return

            if ps == "pending":
                await query.answer(t(lang, "payment_not_received"), show_alert=True)
            elif ps == "partial":
                paid = order.paid_amount or 0
                expected = order.expected_amount or order.total_price
                remaining = expected - paid
                await query.answer(
                    t(lang, "payment_partial", paid=f"{format_vnd(paid)}", remaining=f"{format_vnd(remaining)}"),
                    show_alert=True,
                )
            elif ps in ("paid", "overpaid"):
                await query.answer(t(lang, "payment_done_processing"), show_alert=True)
            elif ps in ("detected", "confirming"):
                confs = order.confirmations or 0
                req = order.required_confirmations or 12
                await query.answer(
                    t(lang, "crypto_detected", current=confs, required=req),
                    show_alert=True,
                )
            elif sv == "payment_expired":
                await query.answer(t(lang, "payment_expired_msg"), show_alert=True)
            else:
                await query.answer(_payment_status_label(ps, lang), show_alert=True)
        finally:
            db.close()
        return

    # ── copy_addr / copy_amt / copy_payid: tap-to-see-and-copy for payment info ──
    if data.startswith("copy_addr:") or data.startswith("copy_amt:") or data.startswith("copy_payid:"):
        kind, order_id_str = data.split(":", 1)
        order_id = int(order_id_str)
        tg_user = update.effective_user
        db = SessionLocal()
        try:
            lang = _get_lang(db, tg_user.id)
            order = get_order_by_id(db, order_id)
            if not order or order.telegram_user_id != str(tg_user.id):
                await query.answer(t(lang, "order_not_found"), show_alert=True)
                return
            if kind == "copy_addr":
                value = order.payment_address or "—"
                await query.answer(t(lang, "copy_address_alert", value=value), show_alert=True)
            elif kind == "copy_amt":
                amount = order.expected_crypto_amount
                value = f"{amount:.4f} USDT" if amount else f"{format_vnd(order.total_price)}đ"
                await query.answer(t(lang, "copy_amount_alert", value=value), show_alert=True)
            else:  # copy_payid
                from services.binance_service import get_binance_config
                bnb_cfg = get_binance_config(db) or {}
                value = bnb_cfg.get("pay_id") or "—"
                await query.answer(t(lang, "copy_payid_alert", value=value), show_alert=True)
        finally:
            db.close()
        return

    # ── verify_txid: shopper claims to have paid a crypto order, wants to submit TXID ──
    if data.startswith("verify_txid:"):
        order_id = int(data.split(":")[1])
        tg_user = update.effective_user
        db = SessionLocal()
        try:
            lang = _get_lang(db, tg_user.id)
            order = get_order_by_id(db, order_id)
            if not order or order.telegram_user_id != str(tg_user.id):
                await query.answer(t(lang, "order_not_found"), show_alert=True)
                return
            sv = order.status.value if hasattr(order.status, "value") else str(order.status)
            if sv != "pending_payment" or order.payment_network not in ("BEP20", "TRC20", "ERC20"):
                await query.answer(t(lang, "order_not_found"), show_alert=True)
                return
            context.user_data["state"] = "waiting_txid"
            context.user_data["txid_order_id"] = order_id
            await query.answer()
            await context.bot.send_message(chat_id=tg_user.id, text=t(lang, "waiting_txid_prompt"))
        finally:
            db.close()
        return

    # ── view_order ──
    if data.startswith("view_order:"):
        order_id = int(data.split(":")[1])
        tg_user = update.effective_user
        db = SessionLocal()
        try:
            lang = _get_lang(db, tg_user.id)
            order = get_order_by_id(db, order_id)
            if not order or order.telegram_user_id != str(tg_user.id):
                await query.answer(t(lang, "order_not_found"), show_alert=True)
                return
            sv = order.status.value if hasattr(order.status, "value") else order.status
            product_name = _product_display_name(order.product, lang) if order.product else "—"
            ext_code = order.external_order_code or order.external_order_id or "—"
            text = (
                f"📦 <b>Chi tiết đơn hàng</b>\n\n"
                f"Mã đơn: <code>{order.order_code}</code>\n"
                f"Mã nguồn: <code>{ext_code}</code>\n"
                f"Sản phẩm: {html.escape(product_name)}\n"
                f"Số lượng: {order.quantity}\n"
                f"Tổng tiền: {format_vnd(order.total_price)}đ\n"
                f"Trạng thái: {_status_label(sv, lang)}\n"
                f"Thời gian: {order.created_at.strftime('%d/%m/%Y %H:%M')}"
            )
            support = _get_support_username(db)
            await query.message.reply_text(
                text, parse_mode="HTML",
                reply_markup=post_delivery_keyboard(order_id, support, lang=lang),
            )
        finally:
            db.close()
        return

    # ── reload_order ──
    if data.startswith("reload_order:"):
        order_id = int(data.split(":")[1])
        tg_user = update.effective_user
        db = SessionLocal()
        try:
            lang = _get_lang(db, tg_user.id)
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

            product_name = _product_display_name(order.product, lang) if order.product else "—"
            text, file_bytes = format_delivery_message(order, items, product_name, lang=lang)
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
                    chat_id=tg_user.id, text=text, parse_mode="HTML",
                    reply_markup=post_delivery_keyboard(order.id, support, lang=lang),
                )
        finally:
            db.close()
        return


# ── Message handler ────────────────────────────────────────────────────────────

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    db = SessionLocal()
    try:
        lang = _get_lang(db, update.effective_user.id)
    finally:
        db.close()

    # ── Language menu button ──
    text_in = update.message.text or ""
    if text_in in ("🌐 Ngôn ngữ", "🌐 Language"):
        await update.message.reply_text(t("vi", "choose_lang"), reply_markup=language_keyboard())
        return

    if state == "waiting_txid":
        order_id = context.user_data.get("txid_order_id")
        txid = (update.message.text or "").strip()
        context.user_data.pop("state", None)
        context.user_data.pop("txid_order_id", None)

        if not order_id:
            return

        checking_msg = await update.message.reply_text(t(lang, "txid_checking"))
        db = SessionLocal()
        try:
            order = get_order_by_id(db, order_id)
            if not order or order.telegram_user_id != str(update.effective_user.id):
                await checking_msg.edit_text(t(lang, "order_not_found"))
                return

            from services.crypto_monitor import verify_txid_for_order
            result = await verify_txid_for_order(db, order, txid)

            if result.get("ok"):
                try:
                    await checking_msg.edit_text(t(lang, "txid_ok_confirmed"))
                except Exception:
                    pass
                return

            reason = result.get("reason", "generic")
            key = f"txid_fail_{reason}"
            try:
                if reason == "insufficient_confirmations":
                    msg = t(lang, key, confirmations=result.get("confirmations", 0), required=result.get("required", 0))
                else:
                    msg = t(lang, key)
            except Exception:
                msg = t(lang, "txid_fail_generic")

            from bot.keyboards import crypto_payment_keyboard
            support = _get_support_username(db)
            try:
                await checking_msg.edit_text(msg)
            except Exception:
                pass
            # Let the shopper retry with a different TXID immediately, without
            # needing to tap "Verify TXID" again.
            if reason not in ("already_paid", "order_not_pending", "unsupported_network"):
                context.user_data["state"] = "waiting_txid"
                context.user_data["txid_order_id"] = order_id
        finally:
            db.close()
        return

    if state == "waiting_quantity":
        product_id = context.user_data.get("buying_product_id")
        text = update.message.text.strip()
        try:
            quantity = int(text)
            if quantity <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(t(lang, "qty_invalid"))
            return

        db = SessionLocal()
        try:
            detail = get_product_detail(db, product_id)
            if not detail:
                await update.message.reply_text(t(lang, "product_not_found"))
                context.user_data.clear()
                return

            p = detail["product"]
            sources = detail["sources"]
            from models import DeliveryMode

            # Re-sync if stale (>60s)
            if p.delivery_mode == DeliveryMode.api_auto:
                for src in sources:
                    if src.api_product and src.api_product.last_sync_at:
                        age = datetime.utcnow() - src.api_product.last_sync_at
                        if age > timedelta(seconds=60):
                            from services.api_service import sync_api_products
                            await sync_api_products(db, src.api_product.api_connection_id)
                            db.expire_all()
                            detail = get_product_detail(db, product_id)
                            if detail:
                                p = detail["product"]
                                sources = detail["sources"]
                            break

            stock_info = get_product_stock_status(product_id, db)
            total_stock = stock_info["stock"]
            min_qty = p.min_quantity or 1

            if quantity < min_qty:
                await update.message.reply_text(
                    t(lang, "qty_below_min", min=min_qty), parse_mode="HTML"
                )
                return

            if p.delivery_mode in (DeliveryMode.api_auto, DeliveryMode.manual_stock):
                if stock_info["status"] == "out_of_stock":
                    await update.message.reply_text(t(lang, "product_out_of_stock_recheck"))
                    context.user_data.clear()
                    return
                if total_stock > 0 and quantity > total_stock:
                    await update.message.reply_text(
                        t(lang, "qty_exceeds_stock", stock=total_stock, qty=quantity),
                        parse_mode="HTML",
                    )
                    return

            tg_user = update.effective_user
            try:
                await update.message.delete()
            except Exception:
                pass

            processing_msg = await context.bot.send_message(
                chat_id=tg_user.id,
                text=t(lang, "processing_order"),
            )

            context.user_data["state"] = "processing"
            try:
                await _do_create_order(context, db, tg_user, product_id, quantity, processing_msg)
            except Exception as e:
                logger.error(f"Order creation error: {e}")
                try:
                    await processing_msg.edit_text(t(lang, "order_error"))
                except Exception:
                    pass
            context.user_data.clear()
        finally:
            db.close()
        return
