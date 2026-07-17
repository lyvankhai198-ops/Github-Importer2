import io
import html
import json
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.ext import ContextTypes
from bot.keyboards import (
    main_menu_keyboard, product_list_keyboard, product_detail_keyboard,
    out_of_stock_keyboard, payment_keyboard, post_delivery_keyboard,
    partial_delivery_keyboard, language_keyboard, payment_method_keyboard,
    binance_keyboard, crypto_payment_keyboard,
    confirm_order_keyboard,
    wallet_menu_keyboard, wallet_deposit_currency_keyboard, wallet_deposit_method_keyboard,
    wallet_insufficient_balance_keyboard, wallet_deposit_qr_keyboard,
    api_menu_keyboard, api_back_keyboard, api_confirm_keyboard, account_info_keyboard,
    order_search_list_keyboard, order_detail_keyboard, admin_issue_keyboard,
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
    generate_payment_code, process_paid_order,
)
from services import wallet_service
from services import api_client_service
from services import api_key_service
from models import (
    Order, TelegramBotConfig, OrderStatus, PaymentStatus, User, Product,
    WalletCurrency, WalletTxType, WalletDeposit, WalletDepositStatus,
    ApiClient, ApiClientStatus, ApiRequestLog, OrderIssue, IssueStatus,
)
from services.order_search import find_orders
from services import refund_service
from services.warranty import get_order_warranty_days
from services.wallet_service import AlreadyProcessedError
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
        BotCommand("wallet",   "Ví của tôi"),
        BotCommand("language", "Đổi ngôn ngữ"),
        BotCommand("support",  "Hỗ trợ"),
        BotCommand("myid",     "Lấy Telegram ID"),
    ]
    commands_en = [
        BotCommand("menu",     "Account information"),
        BotCommand("product",  "Product list"),
        BotCommand("orders",   "My orders"),
        BotCommand("wallet",   "My wallet"),
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


async def _cleanup_flow_state(context: ContextTypes.DEFAULT_TYPE, chat_id):
    """
    Delete any leftover prompt message from an abandoned in-progress flow
    (e.g. "Enter the quantity you want to buy:") and clear user_data. Every
    command/button that acts as a navigation reset (/start, /menu, /product,
    /cancel, 🏠 Home, etc.) must call this instead of a bare
    context.user_data.clear() — otherwise switching screens mid-flow leaves
    the old prompt message orphaned in the chat.
    """
    qty_prompt_id = context.user_data.get("quantity_prompt_message_id")
    if qty_prompt_id:
        await _safe_del(context.bot, chat_id, qty_prompt_id)
    context.user_data.clear()


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

        # /start is also a hard reset: cancel any in-progress input flow or
        # temp navigation state left over from before, same as 🏠 Trang chủ.
        await _cleanup_flow_state(context, update.effective_chat.id)
        admin_id = _get_admin_id(db)
        is_admin = str(tg_user.id) == str(admin_id)
        welcome = _get_welcome_message(db)
        await update.message.reply_text(
            welcome,
            reply_markup=main_menu_keyboard(lang=lang, is_admin=is_admin),
        )
        # Returning users see the latest (synced) products immediately — no
        # extra tap required.
        await _send_product_list(update.message, db, context, lang)
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
        # /menu is also a hard reset: cancel any in-progress input flow or
        # temp navigation state, same as 🏠 Trang chủ.
        await _cleanup_flow_state(context, update.effective_chat.id)
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
        # Admin: hiển thị số dư ví chợ đồng bộ với trang web
        if is_admin:
            from models import AdminUser
            from services.market_wallet_service import get_balance as _mw_get_balance
            from services.normalize import format_vnd as _fmt_vnd
            _cfg_admin = db.query(AdminUser).execution_options(skip_tenant_filter=True).first()
            if _cfg_admin:
                _mw_bal = _mw_get_balance(_cfg_admin)
                text += "\n" + t(lang, "admin_market_wallet_balance", amount=_fmt_vnd(_mw_bal))
        await update.message.reply_text(
            text, parse_mode="HTML",
            reply_markup=main_menu_keyboard(lang=lang, is_admin=is_admin),
        )
        # Same as /start: land the user straight on the synced product list,
        # not a extra menu they have to tap Products again from.
        await _send_product_list(update.message, db, context, lang)
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


async def _try_edit_message(msg, text: str, reply_markup, parse_mode="HTML") -> bool:
    """
    Best-effort "update the current message in place" used by 🏠 Trang chủ /
    🏠 Home so re-rendering the product list doesn't spam a new message.
    Tries edit_text (plain/text messages) then edit_caption (photo messages,
    e.g. a product-detail view with an image) and reports success/failure so
    the caller can fall back to sending a fresh message only if both fail.
    "Message is not modified" (user re-opened an already-identical screen)
    counts as success — nothing needs to change on screen.
    """
    try:
        await msg.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        return True
    except Exception as e:
        if "not modified" in str(e).lower():
            return True
    try:
        await msg.edit_caption(caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
        return True
    except Exception as e:
        if "not modified" in str(e).lower():
            return True
    return False


async def _trigger_background_sync():
    """
    Fire-and-forget refresh of every active API source, used so opening the
    product list never makes the shopper wait on a live supplier HTTP call
    (previously ~1-8s per source, felt slow). Runs on its own DB session/task
    so it can't block or fail the render that triggered it; still capped by
    sync_active_supplier_products' own 30s cache lock, so rapid taps don't
    hammer the supplier API.
    """
    from services.api_service import sync_active_supplier_products
    sess = SessionLocal()
    try:
        await sync_active_supplier_products(sess)
    except Exception as e:
        logger.error(f"[_trigger_background_sync] failed: {e}")
    finally:
        sess.close()


# Kept comfortably under the 10-minute cutoff in get_product_stock_status
# (past that, api_auto products are shown as unavailable/"Out of stock").
# If a connection hasn't synced within this window — e.g. the shopper opens
# the bot after being away long enough that the periodic scheduler's last
# tick predates it, or the process just restarted — every api_auto product
# would flash a false "Out of stock" for one render before the background
# sync catches up. In that case we wait for one sync instead, so the very
# first list shown is already accurate.
_STALE_SYNC_THRESHOLD_MINUTES = 8


def _has_stale_sync(db) -> bool:
    from models import ApiConnection
    connections = db.query(ApiConnection).filter(ApiConnection.is_active == True).all()
    if not connections:
        return False
    now = datetime.utcnow()
    for c in connections:
        if c.last_sync_at is None or (now - c.last_sync_at) > timedelta(minutes=_STALE_SYNC_THRESHOLD_MINUTES):
            return True
    return False


async def _send_product_list(message_target, db, context, lang: str, edit_target=None):
    """
    Shared "render latest page-0 product list" flow, used by /products, the
    🛍 Products button, /start, /menu, and — per the 🏠 Trang chủ / 🏠 Home
    requirement — the home button itself.

    Renders immediately from whatever is already in the DB (fast — no live
    supplier HTTP call in the request path) and kicks off a background
    refresh of every active API source so the *next* view reflects any
    changes; it never blocks or delays this render. Background sync itself
    never zeroes out stock for a source that errored/timed out — it just
    reports it, and it's also run periodically on its own schedule per
    connection, so staleness is bounded even between shopper visits.

    If `edit_target` (a Message) is given, the result is rendered by editing
    that message in place (text or caption) instead of sending new messages,
    to avoid spamming the chat; `message_target` is still used as the
    fallback reply target if editing isn't possible (e.g. message too old).
    """
    from models import ApiConnection

    has_sources = db.query(ApiConnection).filter(ApiConnection.is_active == True).first() is not None
    if has_sources:
        # Always sync before rendering — user must see live supplier stock
        # every time they open the product list. The 30s cache inside
        # sync_active_supplier_products prevents hammering the supplier API
        # on rapid taps, so this is safe to call unconditionally.
        from services.api_service import sync_active_supplier_products
        try:
            await sync_active_supplier_products(db)
        except Exception as e:
            logger.error(f"[_send_product_list] sync failed: {e}")
        db.expire_all()

    show_oos = _get_show_out_of_stock(db)
    per_page = _get_products_per_page(db)
    products = get_active_products_for_bot(db, show_out_of_stock=show_oos)

    if not products:
        text = t(lang, "product_list_empty")
        if edit_target is None or not await _try_edit_message(edit_target, text, None):
            await message_target.reply_text(text)
        return

    if context is not None:
        context.user_data["last_products_page"] = 0
    title = t(lang, "product_list_title")
    kbd = product_list_keyboard(products, lang=lang, page=0, per_page=per_page)

    if edit_target is not None and await _try_edit_message(edit_target, title, kbd):
        return
    await message_target.reply_text(title, parse_mode="HTML", reply_markup=kbd)


async def products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        if not await _require_language_selected(update, db):
            return
        lang = _get_lang(db, update.effective_user.id)
        # /product(s) is also a navigation reset: cancel any in-progress
        # input flow (e.g. waiting_quantity) and delete its leftover prompt
        # message, same as 🏠 Trang chủ — see the screenshot report where
        # "Enter the quantity you want to buy:" stayed in the chat after
        # /product was used to go back to the list.
        await _cleanup_flow_state(context, update.effective_chat.id)
        await _send_product_list(update.message, db, context, lang)
    finally:
        db.close()


async def orders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    "🔍 Tìm đơn hàng" — prompts the shopper for an email or delivered
    account rather than dumping a raw order list (see message_handler's
    "waiting_order_search" state for the actual lookup).
    """
    db = SessionLocal()
    try:
        if not await _require_language_selected(update, db):
            return
        lang = _get_lang(db, update.effective_user.id)
        await _cleanup_flow_state(context, update.effective_chat.id)
        context.user_data["state"] = "waiting_order_search"
        await update.message.reply_text(t(lang, "order_search_prompt"))
    finally:
        db.close()


def _order_account_text(order) -> str:
    """Best-effort plain text of the account(s) delivered for this order,
    reusing whatever was already stored — never re-fetches from the API."""
    try:
        items = get_delivery_items(order)
    except Exception:
        items = []
    lines = []
    for it in items:
        if not isinstance(it, dict):
            continue
        v = it.get("value")
        if not v and it.get("username"):
            v = f"{it['username']}|{it.get('password', '')}" if it.get("password") else it["username"]
        if v:
            lines.append(str(v))
    if lines:
        return "\n".join(lines)
    if order.delivery_data:
        return str(order.delivery_data)
    return ""


async def _render_order_detail_text(db, order, lang: str) -> str:
    """Builds the "🔍 Tìm đơn hàng" detail message per spec: code, product,
    buyer, seller, delivered account (in a <pre> block), price, purchase
    time, warranty, days used/remaining, max refund, status."""
    product = order.product
    product_name = _product_display_name(product, lang)

    buyer = order.telegram_user_id
    user = order.user
    if user and (user.first_name or user.username):
        buyer_display = " ".join(filter(None, [user.first_name, user.last_name])) or (user.username or buyer)
        buyer = f"{buyer_display} ({order.telegram_user_id})"

    seller = "—"
    if product:
        from services.shared_catalog import resolve_api_product
        for s in getattr(product, "sources", []) or []:
            ap = resolve_api_product(db, s)
            if ap and ap.external_seller:
                seller = ap.external_seller
                break

    account_text = _order_account_text(order)
    account_block = (
        f"<pre>{html.escape(account_text)}</pre>" if account_text else t(lang, "order_detail_no_account")
    )

    if lang == "vi":
        price_str = f"{format_vnd(order.total_price)}đ"
    else:
        from services.normalize import format_usdt
        usdt_total = (product.price_usdt * order.quantity) if product else None
        if usdt_total is None:
            from services.exchange_rate_service import get_exchange_config
            from services.normalize import compute_price_usdt
            rate = float(get_exchange_config(db).get("fixed_rate") or 26500.0)
            usdt_total = compute_price_usdt(order.total_price, rate)
        price_str = f"{format_usdt(usdt_total)} USDT"

    result = refund_service.compute_refund(order)
    warranty_label = (product.warranty if product and product.warranty else "—")
    refund_amount_str = (
        f"{format_vnd(result['amount'])}đ" if result["currency"] == WalletCurrency.VND
        else f"{result['amount']:.4f} USDT"
    )
    if result["already_refunded"]:
        refund_amount_str = t(lang, "refund_already_done")

    sv = order.status.value if hasattr(order.status, "value") else order.status
    purchase_time = order.paid_at or order.created_at

    lines = [
        t(lang, "order_detail_title"),
        "",
        t(lang, "order_detail_code", code=order.order_code),
        t(lang, "order_detail_product", product=html.escape(product_name)),
        t(lang, "order_detail_buyer", buyer=html.escape(str(buyer))),
        t(lang, "order_detail_seller", seller=html.escape(str(seller))),
        t(lang, "order_detail_account"),
        account_block,
        t(lang, "order_detail_price", price=price_str),
        t(lang, "order_detail_purchase_time", time=purchase_time.strftime("%d/%m/%Y %H:%M")),
        t(lang, "order_detail_warranty", warranty=html.escape(str(warranty_label))),
        t(lang, "order_detail_days_used", days=result["used_days"]),
        t(lang, "order_detail_days_remaining", days=result["remaining_days"]),
        t(lang, "order_detail_max_refund", amount=refund_amount_str),
        t(lang, "order_detail_status", status=_status_label(sv, lang)),
    ]
    return "\n".join(lines)


async def _send_wallet_menu(bot_or_query, chat_id_or_none, db, tg_user, lang: str, edit=False):
    user = db.query(User).filter(User.telegram_id == str(tg_user.id)).first()
    vnd = wallet_service.get_balance(user, WalletCurrency.VND) if user else 0.0
    usdt = wallet_service.get_balance(user, WalletCurrency.USDT) if user else 0.0
    text = "\n".join([
        t(lang, "wallet_title"),
        "",
        t(lang, "wallet_balance_vnd", amount=format_vnd(vnd)),
        t(lang, "wallet_balance_usdt", amount=f"{usdt:.4f}"),
    ])
    kbd = wallet_menu_keyboard(lang=lang)
    if edit:
        try:
            await bot_or_query.message.edit_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            await bot_or_query.message.reply_text(text, parse_mode="HTML", reply_markup=kbd)
    else:
        await bot_or_query.reply_text(text, parse_mode="HTML", reply_markup=kbd)


async def wallet_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /wallet command and 💼 Ví của tôi / My Wallet menu button."""
    db = SessionLocal()
    try:
        if not await _require_language_selected(update, db):
            return
        lang = _get_lang(db, update.effective_user.id)
        tg_user = update.effective_user
        get_or_create_user(db, str(tg_user.id), tg_user.username, tg_user.first_name, tg_user.last_name)
        await _cleanup_flow_state(context, update.effective_chat.id)
        await _send_wallet_menu(update.message, None, db, tg_user, lang, edit=False)
    finally:
        db.close()


def _api_base_url() -> str:
    import os
    domain = os.environ.get("REPLIT_DEV_DOMAIN") or os.environ.get("REPLIT_DOMAINS", "").split(",")[0]
    return f"https://{domain}" if domain else "https://your-domain.example.com"


async def _send_api_menu(bot_or_query, db, tg_user, lang: str, edit=False, bot=None):
    """
    Prepaid-only API screen: just the (masked) key, a status line if it's
    locked/revoked, the prepaid-wallet notice, and Swagger + Regenerate.
    A key is auto-created the first time this screen is opened — there is
    no separate "Create key" step. Wallet balance, usage stats, and order
    history live in 👛 Ví / 📦 Đơn hàng / 👤 Thông tin instead of here.
    """
    client = api_client_service.get_client_for_user(db, str(tg_user.id))
    if not client:
        client, _full_key = api_client_service.generate_key_for_user(db, str(tg_user.id))
        admin_id = _get_admin_id(db)
        if admin_id and bot:
            try:
                await bot.send_message(
                    chat_id=int(admin_id),
                    text=t("vi", "api_admin_key_created", tg_id=tg_user.id),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"api_admin_key_created notify failed: {e}")

    lines = [t(lang, "api_menu_title"), ""]
    if client.status != ApiClientStatus.active:
        status_key = {
            ApiClientStatus.locked: "api_status_locked",
            ApiClientStatus.revoked: "api_status_revoked",
        }.get(client.status, "api_status_active")
        lines.append(t(lang, "api_menu_status", status=t(lang, status_key)))
    lines.append(t(lang, "api_menu_key", key=api_key_service.masked_display(client.key_prefix)))
    lines.append("")
    lines.append(t(lang, "api_menu_prepaid_notice"))
    text = "\n".join(lines)
    kbd = api_menu_keyboard(lang=lang, swagger_url=f"{_api_base_url()}/docs")
    if edit:
        try:
            await bot_or_query.message.edit_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            await bot_or_query.message.reply_text(text, parse_mode="HTML", reply_markup=kbd)
    else:
        await bot_or_query.reply_text(text, parse_mode="HTML", reply_markup=kbd)


async def api_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /api command and 🔗 API menu button."""
    db = SessionLocal()
    try:
        if not await _require_language_selected(update, db):
            return
        lang = _get_lang(db, update.effective_user.id)
        tg_user = update.effective_user
        get_or_create_user(db, str(tg_user.id), tg_user.username, tg_user.first_name, tg_user.last_name)
        await _cleanup_flow_state(context, update.effective_chat.id)
        await _send_api_menu(update.message, db, tg_user, lang, edit=False, bot=context.bot)
    finally:
        db.close()


def _time_of_day_key() -> str:
    """Vietnam-local (UTC+7) time-of-day bucket for the account-info greeting."""
    from datetime import datetime, timedelta
    hour = (datetime.utcnow() + timedelta(hours=7)).hour
    if 5 <= hour < 12:
        return "greeting_morning"
    if 12 <= hour < 18:
        return "greeting_afternoon"
    return "greeting_evening"


async def _account_info_text(db, tg_user, lang: str) -> str:
    from services import rank_service

    user = db.query(User).filter(User.telegram_id == str(tg_user.id)).first()
    vnd = wallet_service.get_balance(user, WalletCurrency.VND) if user else 0.0

    total_spent = rank_service.compute_total_spent(db, str(tg_user.id))
    total_accounts = rank_service.compute_total_accounts_purchased(db, str(tg_user.id))
    total_orders = getattr(user, "total_orders", 0) or 0

    current_rank = rank_service.get_rank_for_spend(db, total_spent)
    next_rank = rank_service.get_next_rank(db, current_rank) if current_rank else None
    progress = rank_service.get_progress(total_spent, current_rank, next_rank)

    if progress["is_max"] or not next_rank:
        progress_section = t(lang, "rank_max_section")
    else:
        bar = rank_service.render_progress_bar(progress["percent"])
        progress_section = t(lang, "rank_progress_section",
                              bar=bar, percent=round(progress["percent"]),
                              remaining=format_vnd(progress["remaining"]),
                              next_rank_emoji=next_rank.emoji, next_rank_name=next_rank.name)

    username_str = tg_user.username or "—"
    full_name = " ".join(filter(None, [tg_user.first_name, tg_user.last_name])) or username_str
    rank_emoji = current_rank.emoji if current_rank else "🥉"
    rank_name = current_rank.name if current_rank else "—"

    return t(lang, "account_info_v2",
              time_of_day=t(lang, _time_of_day_key()),
              full_name=full_name, tg_id=tg_user.id,
              rank_emoji=rank_emoji, rank_name=rank_name,
              balance=format_vnd(vnd), total_spent=format_vnd(total_spent),
              total_orders=total_orders, total_accounts=total_accounts,
              progress_section=progress_section)


async def account_info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /account command and 👤 Thông tin / Account menu button."""
    db = SessionLocal()
    try:
        if not await _require_language_selected(update, db):
            return
        lang = _get_lang(db, update.effective_user.id)
        tg_user = update.effective_user
        get_or_create_user(db, str(tg_user.id), tg_user.username, tg_user.first_name, tg_user.last_name)
        await _cleanup_flow_state(context, update.effective_chat.id)
        text = await _account_info_text(db, tg_user, lang)
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=account_info_keyboard(lang=lang))
    finally:
        db.close()


async def support_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        if not await _require_language_selected(update, db):
            return
        lang = _get_lang(db, update.effective_user.id)
        await _cleanup_flow_state(context, update.effective_chat.id)
        support = _get_support_username(db)
        if support:
            await update.message.reply_text(t(lang, "support_contact", username=support))
        else:
            await update.message.reply_text(t(lang, "support_contact_admin"))
    finally:
        db.close()


async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🌐 Truy cập trang quản trị tại địa chỉ máy chủ của bạn.")


async def unknown_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Fallback for any /command that isn't matched by a registered
    CommandHandler above (e.g. /abc, /test123, or the old /product typo).
    Never stays silent — always tells the shopper what commands exist,
    in their own language.
    """
    db = SessionLocal()
    try:
        lang = _get_lang(db, update.effective_user.id)
    finally:
        db.close()
    await update.message.reply_text(t(lang, "invalid_command"), parse_mode="HTML")


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /cancel (also triggered by "❌ Hủy bỏ" / "❌ Cancel" free text) — universal
    escape hatch: clears any in-progress flow (e.g. waiting_quantity) and
    returns the user to the main menu, from anywhere in the bot. Deletes any
    leftover prompt message from that flow (e.g. "Enter the quantity you want
    to buy:") so cancelling doesn't leave orphaned messages in the chat.
    """
    await _cleanup_flow_state(context, update.effective_chat.id)
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
        t(lang, "sepay_bank", bank=html.escape(sepay.bank_name or sepay.bank_bin)),
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


def _get_deposit_payment_display(db, method: str):
    """
    Return a dict of display fields for a wallet-deposit payment method, or
    None if that method isn't configured. Reuses the same config sources as
    the order-payment setup functions above (SepayConfig / Binance / crypto
    PaymentMethod rows) — read-only here, nothing is created upstream.
    """
    if method == "bank_transfer":
        sepay = get_sepay_config(db)
        if not sepay or not sepay.is_enabled or not sepay.account_number or not sepay.bank_bin or not sepay.account_name:
            return None
        return {
            "bank": sepay.bank_name or sepay.bank_bin, "bank_bin": sepay.bank_bin,
            "acc": sepay.account_number, "acc_name": sepay.account_name,
        }

    if method == "binance_pay":
        from services.binance_service import get_binance_config
        bnb_cfg = get_binance_config(db)
        if not bnb_cfg or not bnb_cfg.get("receiver_binance_id"):
            return None
        return {"network": "Binance Pay", "address": bnb_cfg.get("receiver_binance_id")}

    if method in ("usdt_bep20", "usdt_trc20", "usdt_erc20"):
        from models import PaymentMethod
        from crypto import decrypt
        pm = db.query(PaymentMethod).filter(PaymentMethod.method_code == method, PaymentMethod.is_active == True).first()
        if not pm or not pm.config_encrypted:
            return None
        try:
            pm_cfg = json.loads(decrypt(pm.config_encrypted) or "{}")
        except Exception:
            pm_cfg = {}
        wallet = (pm_cfg.get("wallet_address") or "").strip()
        if not wallet:
            return None
        network = {"usdt_bep20": "BEP20", "usdt_trc20": "TRC20", "usdt_erc20": "ERC20"}[method]
        return {"network": network, "address": wallet}

    return None


async def _setup_wallet_payment(context, db, tg_user, order, lang: str, processing_msg=None):
    """
    Pay-with-wallet: unlike the other methods, this is synchronous — no
    external wait for a webhook/on-chain confirmation. Debits wallet_vnd
    atomically, marks the order paid, then hands it straight to
    process_paid_order(). order.total_price is always VND-denominated
    regardless of chosen method, so only wallet_vnd is used here.
    """
    user = db.query(User).filter(User.telegram_id == str(tg_user.id)).first()
    balance = wallet_service.get_balance(user, WalletCurrency.VND) if user else 0.0

    if balance < order.total_price:
        if processing_msg:
            try:
                await processing_msg.delete()
            except Exception:
                pass
        await context.bot.send_message(
            chat_id=tg_user.id,
            text=t(lang, "wallet_insufficient_balance", needed=format_vnd(order.total_price), have=format_vnd(balance)),
            parse_mode="HTML",
            reply_markup=wallet_insufficient_balance_keyboard(order.id, lang=lang),
        )
        return False

    # The debit and the order's paid-status flip happen in ONE atomic
    # transaction (extra_updates), guarded by "payment_status is not
    # already paid/overpaid" — so a duplicate callback (double-tap, retry)
    # can never debit the wallet twice for the same order.
    now_iso = datetime.utcnow().isoformat(sep=" ")
    try:
        tx = wallet_service.debit_wallet(
            db, str(tg_user.id), WalletCurrency.VND, order.total_price,
            WalletTxType.purchase, order_id=order.id,
            note=f"Thanh toán đơn {order.order_code}", actor="system",
            extra_updates=[(
                "UPDATE orders SET payment_method = 'wallet', payment_currency = 'VND', "
                "payment_status = 'paid', paid_amount = ?, paid_at = ?, payment_chat_id = ? "
                "WHERE id = ? AND (payment_status IS NULL OR payment_status NOT IN ('paid', 'overpaid'))",
                (order.total_price, now_iso, str(tg_user.id), order.id),
            )],
        )
    except wallet_service.InsufficientBalanceError:
        if processing_msg:
            try:
                await processing_msg.delete()
            except Exception:
                pass
        await context.bot.send_message(
            chat_id=tg_user.id,
            text=t(lang, "wallet_insufficient_balance", needed=format_vnd(order.total_price), have=format_vnd(balance)),
            parse_mode="HTML",
            reply_markup=wallet_insufficient_balance_keyboard(order.id, lang=lang),
        )
        return False
    except wallet_service.AlreadyProcessedError:
        # Order was already marked paid by a concurrent/duplicate call —
        # nothing was debited this time around. Fall through to hand it to
        # process_paid_order in case that step itself didn't finish
        # previously; the balance shown falls back to the live value.
        logger.info(f"[wallet] order {order.id} already paid — skipping duplicate debit")
        tx = None

    db.refresh(order)
    db.refresh(user)

    if processing_msg:
        try:
            await processing_msg.delete()
        except Exception:
            pass
    try:
        display_balance = tx.balance_after if tx else wallet_service.get_balance(user, WalletCurrency.VND)
        await context.bot.send_message(
            chat_id=tg_user.id,
            text=t(lang, "wallet_purchase_debited", amount=format_vnd(order.total_price), balance=format_vnd(display_balance)),
            parse_mode="HTML",
        )
    except Exception:
        pass

    asyncio.create_task(process_paid_order(order.id))
    return True


async def _setup_binance_payment(context, db, tg_user, order, lang: str, processing_msg=None):
    """
    Set up Binance Pay for an existing order. Verification happens later,
    once the shopper submits a TXID, against the shop's own Binance API
    Management Pay History (services.crypto_monitor.verify_binance_payment)
    — there is no Merchant API checkout order to create up front.
    """
    from services.binance_service import get_binance_config
    from services.exchange_rate_service import calculate_crypto_amount, generate_unique_crypto_amount

    cfg_bot = db.query(TelegramBotConfig).first()
    support = cfg_bot.support_username if cfg_bot else ""

    bnb_cfg = get_binance_config(db)
    if not bnb_cfg or not bnb_cfg.get("api_key") or not bnb_cfg.get("secret_key") or not bnb_cfg.get("receiver_binance_id"):
        if processing_msg:
            try:
                await processing_msg.edit_text(t(lang, "payment_method_disabled"))
            except Exception:
                pass
        return False

    base_usdt, rate = await calculate_crypto_amount(db, order.total_price)
    unique_usdt = generate_unique_crypto_amount(db, base_usdt, "BINANCE")
    timeout = int(bnb_cfg.get("order_expiry_minutes") or 30)
    receiver_id = bnb_cfg.get("receiver_binance_id") or "—"

    order.payment_method = "binance_pay"
    order.payment_currency = "USDT"
    order.exchange_rate = rate
    order.expected_crypto_amount = unique_usdt
    order.payment_network = "BINANCE"
    order.payment_chat_id = tg_user.id
    order.payment_expires_at = datetime.utcnow() + timedelta(minutes=timeout)
    db.commit()

    if processing_msg:
        try:
            await processing_msg.delete()
        except Exception:
            pass

    text = "\n".join([
        t(lang, "binance_manual_title"),
        "",
        t(lang, "binance_pay_id", pay_id=receiver_id),
        t(lang, "binance_amount", amount=f"{unique_usdt:.4f}"),
        t(lang, "binance_order_code", code=order.order_code),
        "",
        t(lang, "binance_instruction"),
    ])
    qr_path = bnb_cfg.get("qr_image_path") or ""
    kbd = binance_keyboard(order.id, support, lang=lang)

    sent_msg = None
    if qr_path:
        try:
            from pathlib import Path
            local_path = Path(__file__).resolve().parent.parent / qr_path.lstrip("/")
            sent_msg = await context.bot.send_photo(
                chat_id=tg_user.id, photo=open(local_path, "rb"),
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
            logger.error(f"[binance_pay] send error: {e}")
            return False

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
    from services.warranty import parse_warranty_to_days
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
        warranty_days=parse_warranty_to_days(product.warranty),
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
    kbd = payment_method_keyboard(order.id, enabled_methods, lang=lang, show_wallet=True)

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


async def _render_product_detail(query, context, db, lang: str, product_id: int):
    """
    Render the product-detail card (image/caption + buy button, or the
    out-of-stock blocking screen) into the given callback query's chat.
    Shared by the `product:<id>` callback and the post-delivery "🛍 Mua tiếp"
    button (which opens the newest active product after a resync).
    """
    detail = get_product_detail(db, product_id)
    if not detail:
        await query.message.edit_text(t(lang, "product_not_found"))
        return
    p = detail["product"]
    sources = detail["sources"]
    # Tracked so the "buy:" handler can tell whether the shopper is already
    # looking at this product's detail card (normal browsing) or tapped
    # "Mua ngay" straight from a notification/list — in the latter case it
    # needs to render this detail first instead of jumping straight to the
    # quantity prompt. See callback_handler's "buy:" branch.
    context.user_data["detail_shown_product_id"] = product_id

    # Freshness check — re-sync if stale (>60s)
    from models import DeliveryMode
    from services.shared_catalog import resolve_api_product
    if p.delivery_mode == DeliveryMode.api_auto:
        for src in sources:
            src_ap = resolve_api_product(db, src)
            if src_ap and src_ap.last_sync_at:
                age = datetime.utcnow() - src_ap.last_sync_at
                if age > timedelta(seconds=60):
                    from services.api_service import sync_api_products
                    await sync_api_products(db, src_ap.api_connection_id)
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
        if src.is_active:
            ap = resolve_api_product(db, src)
            if ap:
                api_src = ap
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
    detail_custom_emoji_id = (getattr(p, "telegram_custom_emoji_id", None) or "").strip()
    if status in ("out_of_stock", "unavailable"):
        detail_icon = "❌"
        detail_custom_emoji_id = ""  # never show a chosen custom emoji on the error state
    # Telegram custom emoji (from the "Chọn icon sản phẩm" picker — see
    # services/telegram_emoji.py + routers/emoji_icons.py) render via the
    # <tg-emoji emoji-id="..."> HTML tag; the fallback emoji inside it is
    # what non-Premium Telegram users see instead. Requires parse_mode="HTML"
    # on the send/edit call below (already the case for every path here).
    from services.telegram_emoji import render_icon_html
    detail_icon_html = render_icon_html(detail_icon, detail_custom_emoji_id)
    lines = [f"{detail_icon_html} <b>{html.escape(display_name)}</b>\n"]
    if lang == "en":
        lines.append(t(lang, "product_price", price=format_usdt(p.price_usdt)))
    else:
        lines.append(t(lang, "product_price", price=f"{format_vnd(p.sale_price)}"))
    lines.append(stock_text)
    lines.append(t(lang, "product_sold_count", count=p.sold_count or 0))
    lines.append(t(lang, "product_min_qty", qty=min_qty))
    if p.duration:
        dur = translate_shorthand_to_en(p.duration) if lang == "en" else p.duration
        lines.append(t(lang, "product_duration", val=html.escape(dur)))
    if p.warranty:
        warr = translate_shorthand_to_en(p.warranty) if lang == "en" else p.warranty
        lines.append(t(lang, "product_warranty", val=html.escape(warr)))

    # Description: single source of truth for language-correct, cleanly
    # formatted description text — see services.localization. Translations
    # are generated once (on save, on API sync, or the first time a shopper
    # views the missing side) and reused as-is afterwards; a shopper NEVER
    # sees the other language's raw text mixed into their own card.
    from services.localization import get_localized_product_description
    external_desc = api_src.external_description if api_src else None
    desc = get_localized_product_description(p, lang, db=db, external_description=external_desc)
    if desc:
        # Rendered as a native Telegram blockquote card instead of plain
        # wall-of-text, per the admin's requested look. The admin's own
        # description text already contains any "please read carefully"
        # rules for this specific product, so no separate generic warning
        # header is added here anymore — that used to duplicate what the
        # description itself says. See render_description_blockquote().
        from services.telegram_emoji import render_description_blockquote
        lines.append("")
        lines.append(render_description_blockquote(t(lang, "product_description_header"), html.escape(desc)))

    # Deduplicated admin-only alert if the on-demand translation above
    # (or an earlier save/sync) left this product's translation failed —
    # never shown to the shopper, who already got the same-language
    # fallback text from get_localized_product_description.
    if p.translation_status == "failed":
        try:
            from services.translation_alerts import notify_admin_translation_failed
            await notify_admin_translation_failed(db, p)
        except Exception:
            logger.exception(f"[bot] translation-failure alert errored for product {p.id}")

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
            # First-time users go straight from language picker to the product
            # list — no separate "tap Products" step.
            await _send_product_list(query.message, db, context, lang_code)
        finally:
            db.close()
        return

    # ── close ──
    if data == "close":
        await query.message.delete()
        return

    # ── home ──
    if data == "home":
        # Ack the tap immediately (per spec: confirm the callback right away,
        # then sync, then render) so the button doesn't feel stuck/slow.
        try:
            await query.answer()
        except Exception:
            pass
        # Cancel any in-progress input flow (e.g. waiting_quantity, a wallet
        # deposit amount prompt, a pending txid entry) and any temp
        # navigation state — 🏠 Trang chủ is a hard reset back to the
        # product list, not just another menu screen. Also deletes any
        # leftover prompt message from that flow (e.g. "Enter the quantity
        # you want to buy:") so going home doesn't leave orphaned messages.
        await _cleanup_flow_state(context, query.message.chat_id)
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)

            # If 🏠 Home was tapped from an unpaid order's QR/payment screen,
            # cancel that order and delete its QR message first — editing a
            # photo message's caption in place (the usual "edit in place"
            # path below) would otherwise leave the QR image stuck on screen
            # under a "Product list" caption instead of disappearing.
            force_new_message = False
            order = (
                db.query(Order)
                .filter(
                    Order.telegram_user_id == str(tg_user.id),
                    Order.payment_message_id == query.message.message_id,
                )
                .first()
            )
            if order:
                ps = order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status or "")
                st = order.status.value if hasattr(order.status, "value") else str(order.status or "")
                if ps not in ("paid", "overpaid") and st not in ("cancelled", "completed"):
                    order.status = OrderStatus.cancelled
                    order.updated_at = datetime.utcnow()
                    db.commit()
                    chat_id = order.payment_chat_id or order.telegram_user_id
                    await _safe_del(context.bot, chat_id, order.payment_message_id)
                    force_new_message = True

            # Re-sync active sources + reload the DB + render the latest
            # product list, editing the current message in place instead of
            # sending a new one wherever possible (skipped above if we just
            # deleted the current message as a cancelled order's QR).
            await _send_product_list(
                query.message, db, context, lang,
                edit_target=None if force_new_message else query.message,
            )
        except Exception as e:
            logger.warning(f"[home] error: {e}")
        finally:
            db.close()
        return

    # ── wallet: menu / deposit flow ──
    if data == "wallet_home":
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)
            context.user_data.pop("state", None)
            await _send_wallet_menu(query, None, db, tg_user, lang, edit=True)
        finally:
            db.close()
        return

    if data == "wallet_history":
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)
            txs = wallet_service.list_wallet_transactions(db, str(tg_user.id), limit=20)
            if not txs:
                lines = [t(lang, "wallet_history_title"), "", t(lang, "wallet_history_empty")]
            else:
                lines = [t(lang, "wallet_history_title")]
                SIGN = {WalletTxType.deposit: "+", WalletTxType.refund: "+", WalletTxType.admin_credit: "+",
                        WalletTxType.purchase: "-", WalletTxType.admin_debit: "-"}
                for tx in txs:
                    sign = SIGN.get(tx.tx_type, "")
                    cur = tx.currency.value if hasattr(tx.currency, "value") else str(tx.currency)
                    amt_str = f"{format_vnd(tx.amount)}đ" if cur == "VND" else f"{tx.amount:.4f} USDT"
                    tt = tx.tx_type.value if hasattr(tx.tx_type, "value") else str(tx.tx_type)
                    lines.append(f"• {sign}{amt_str} — {tt} ({tx.created_at.strftime('%d/%m %H:%M')})")
            try:
                await query.message.edit_text(
                    "\n".join(lines), parse_mode="HTML",
                    reply_markup=wallet_menu_keyboard(lang=lang),
                )
            except Exception:
                pass
        finally:
            db.close()
        return

    if data == "wallet_deposit":
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)
            try:
                await query.message.edit_text(
                    t(lang, "wallet_choose_deposit_currency"),
                    reply_markup=wallet_deposit_currency_keyboard(lang=lang),
                )
            except Exception:
                pass
        finally:
            db.close()
        return

    if data.startswith("wallet_dep_cur:"):
        currency = data.split(":")[1]
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)
            enabled = get_enabled_payment_methods(db)
            try:
                await query.message.edit_text(
                    t(lang, "wallet_choose_deposit_method"),
                    reply_markup=wallet_deposit_method_keyboard(currency, enabled, lang=lang),
                )
            except Exception:
                pass
        finally:
            db.close()
        return

    if data.startswith("wallet_dep_method:"):
        _, currency, method = data.split(":")
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)
            context.user_data["state"] = "waiting_wallet_deposit_amount"
            context.user_data["wallet_dep_currency"] = currency
            context.user_data["wallet_dep_method"] = method
            prompt_key = "wallet_enter_amount_vnd" if currency == "VND" else "wallet_enter_amount_usdt"
            try:
                await query.message.edit_text(t(lang, prompt_key))
            except Exception:
                await context.bot.send_message(chat_id=tg_user.id, text=t(lang, prompt_key))
        finally:
            db.close()
        return

    if data == "account_orders":
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)
            context.user_data.clear()
            context.user_data["state"] = "waiting_order_search"
            try:
                await query.message.reply_text(t(lang, "order_search_prompt"))
            except Exception:
                await context.bot.send_message(chat_id=tg_user.id, text=t(lang, "order_search_prompt"))
        finally:
            db.close()
        return

    # ── order_pick: single order chosen from a multi-match search list ──
    if data.startswith("order_pick:"):
        order_id = int(data.split(":")[1])
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)
            admin_id = _get_admin_id(db)
            is_admin = str(tg_user.id) == str(admin_id)
            order = db.query(Order).filter(Order.id == order_id).first()
            if not order or (not is_admin and str(order.telegram_user_id) != str(tg_user.id)):
                await query.answer(t(lang, "order_not_found"), show_alert=True)
                return
            text = await _render_order_detail_text(db, order, lang)
            try:
                await query.message.edit_text(text, parse_mode="HTML",
                                               reply_markup=order_detail_keyboard(order.id, lang=lang))
            except Exception:
                await context.bot.send_message(chat_id=tg_user.id, text=text, parse_mode="HTML",
                                                reply_markup=order_detail_keyboard(order.id, lang=lang))
        finally:
            db.close()
        return

    # ── report_issue: "⚠️ Báo lỗi" tapped on an order detail screen ──
    if data.startswith("report_issue:"):
        order_id = int(data.split(":")[1])
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)
            order = db.query(Order).filter(Order.id == order_id).first()
            admin_id = _get_admin_id(db)
            is_admin = str(tg_user.id) == str(admin_id)
            if not order or (not is_admin and str(order.telegram_user_id) != str(tg_user.id)):
                await query.answer(t(lang, "order_not_found"), show_alert=True)
                return
            context.user_data.clear()
            context.user_data["state"] = "waiting_issue_text"
            context.user_data["issue_order_id"] = order_id
            try:
                await context.bot.send_message(chat_id=tg_user.id, text=t(lang, "issue_report_prompt"))
            except Exception:
                pass
        finally:
            db.close()
        return

    # ── admin_issue_*: admin actions on a reported issue ──
    if data.startswith("admin_issue_"):
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)
            admin_id = _get_admin_id(db)
            if str(tg_user.id) != str(admin_id):
                await query.answer(t(lang, "refund_not_authorized"), show_alert=True)
                return

            action, issue_id_str = data.rsplit(":", 1)
            issue_id = int(issue_id_str)
            issue = db.query(OrderIssue).filter(OrderIssue.id == issue_id).first()
            if not issue:
                await query.answer(t(lang, "issue_not_found"), show_alert=True)
                return
            order = db.query(Order).filter(Order.id == issue.order_id).first()

            if action == "admin_issue_view":
                text = await _render_order_detail_text(db, order, "vi")
                await context.bot.send_message(chat_id=tg_user.id, text=text, parse_mode="HTML")
                await query.answer()
                return

            if action == "admin_issue_reply":
                if issue.status != IssueStatus.open and issue.status != IssueStatus.reviewing:
                    await query.answer(t(lang, "issue_already_handled"), show_alert=True)
                    return
                context.user_data.clear()
                context.user_data["state"] = "waiting_admin_reply"
                context.user_data["admin_issue_id"] = issue_id
                await context.bot.send_message(chat_id=tg_user.id, text=t(lang, "issue_reply_prompt"))
                await query.answer()
                return

            if action == "admin_issue_reject":
                if issue.status != IssueStatus.open and issue.status != IssueStatus.reviewing:
                    await query.answer(t(lang, "issue_already_handled"), show_alert=True)
                    return
                context.user_data.clear()
                context.user_data["state"] = "waiting_admin_reject_reason"
                context.user_data["admin_issue_id"] = issue_id
                # Strip the action keyboard right away — reject is a one-way
                # action once initiated, so it must not be double-tappable
                # while the admin is typing the reason.
                await _finalize_admin_issue_message(context.bot, query.message.chat_id, query.message.message_id)
                await context.bot.send_message(chat_id=tg_user.id, text=t(lang, "issue_reject_prompt"))
                await query.answer()
                return

            if action == "admin_issue_resolve":
                if issue.status not in (IssueStatus.open, IssueStatus.reviewing):
                    await query.answer(t(lang, "issue_already_handled"), show_alert=True)
                    return
                issue.status = IssueStatus.resolved
                issue.handled_by = str(tg_user.id)
                issue.handled_at = datetime.utcnow()
                db.commit()
                await query.answer(t(lang, "issue_resolved_admin", id=issue.id), show_alert=True)
                await _finalize_admin_issue_message(
                    context.bot, query.message.chat_id, query.message.message_id,
                    f"✅ Issue #{issue.id} đã được đánh dấu xử lý bởi {tg_user.id}.",
                )
                return

            if action == "admin_issue_refund":
                if issue.status == IssueStatus.refunded:
                    await query.answer(t(lang, "refund_already_done"), show_alert=True)
                    return
                if issue.status not in (IssueStatus.open, IssueStatus.reviewing):
                    await query.answer(t(lang, "issue_already_handled"), show_alert=True)
                    return
                try:
                    result = refund_service.perform_refund(db, issue, order, str(tg_user.id))
                except AlreadyProcessedError:
                    await query.answer(t(lang, "refund_already_done"), show_alert=True)
                    await _finalize_admin_issue_message(
                        context.bot, query.message.chat_id, query.message.message_id,
                    )
                    return
                except Exception as e:
                    logger.error(f"[admin_issue_refund] issue={issue_id} error: {e}")
                    await query.answer(t(lang, "issue_report_error"), show_alert=True)
                    return

                if result["amount"] <= 0:
                    await query.answer(t(lang, "refund_warranty_expired"), show_alert=True)
                    return

                amount_str = (
                    f"{format_vnd(result['amount'])}đ" if result["currency"] == WalletCurrency.VND
                    else f"{result['amount']:.4f} USDT"
                )
                new_balance_str = (
                    f"{format_vnd(result['balance_after'])}đ" if result["currency"] == WalletCurrency.VND
                    else f"{result['balance_after']:.4f} USDT"
                )
                await query.answer(t(lang, "refund_success_admin", amount=amount_str, code=order.order_code),
                                    show_alert=True)
                await _finalize_admin_issue_message(
                    context.bot, query.message.chat_id, query.message.message_id,
                    f"✅ Đã hoàn {amount_str} vào ví cho đơn <code>{order.order_code}</code> (bởi {tg_user.id}).",
                )
                try:
                    buyer_lang = _get_lang(db, order.telegram_user_id)
                    await context.bot.send_message(
                        chat_id=int(order.payment_chat_id or order.telegram_user_id),
                        text=t(buyer_lang, "refund_success_user", code=order.order_code,
                               amount=amount_str, new_balance=new_balance_str),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.error(f"[admin_issue_refund] notify user failed: {e}")
                return
        finally:
            db.close()
        return

    # ── API key menu ──
    if data == "api_home":
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)
            await _send_api_menu(query, db, tg_user, lang, edit=True, bot=context.bot)
        finally:
            db.close()
        return

    if data == "api_generate":
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)
            client, full_key = api_client_service.generate_key_for_user(db, str(tg_user.id))
            try:
                await query.message.edit_text(
                    t(lang, "api_key_generated", key=full_key),
                    parse_mode="HTML", reply_markup=api_back_keyboard(lang=lang),
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=tg_user.id, text=t(lang, "api_key_generated", key=full_key),
                    parse_mode="HTML", reply_markup=api_back_keyboard(lang=lang),
                )
            admin_id = _get_admin_id(db)
            if admin_id:
                try:
                    await context.bot.send_message(
                        chat_id=int(admin_id),
                        text=t("vi", "api_admin_key_created", tg_id=tg_user.id),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.error(f"api_admin_key_created notify failed: {e}")
        finally:
            db.close()
        return

    if data == "api_regenerate":
        db = SessionLocal()
        try:
            lang = _get_lang(db, update.effective_user.id)
            try:
                await query.message.edit_text(
                    t(lang, "api_confirm_regenerate"),
                    reply_markup=api_confirm_keyboard("regenerate", lang=lang),
                )
            except Exception:
                pass
        finally:
            db.close()
        return

    if data == "api_revoke":
        db = SessionLocal()
        try:
            lang = _get_lang(db, update.effective_user.id)
            try:
                await query.message.edit_text(
                    t(lang, "api_confirm_revoke"),
                    reply_markup=api_confirm_keyboard("revoke", lang=lang),
                )
            except Exception:
                pass
        finally:
            db.close()
        return

    if data.startswith("api_confirm:"):
        action = data.split(":")[1]
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)
            client = api_client_service.get_client_for_user(db, str(tg_user.id))
            if not client:
                await query.message.edit_text(t(lang, "api_key_missing_to_show"), reply_markup=api_back_keyboard(lang=lang))
                return
            if action == "regenerate":
                full_key = api_client_service.regenerate_key(db, client)
                await query.message.edit_text(
                    t(lang, "api_key_regenerated", key=full_key),
                    parse_mode="HTML", reply_markup=api_back_keyboard(lang=lang),
                )
            elif action == "revoke":
                api_client_service.revoke_key(db, client)
                await query.message.edit_text(
                    t(lang, "api_key_revoked"), reply_markup=api_back_keyboard(lang=lang),
                )
        finally:
            db.close()
        return

    if data == "api_history":
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)
            client = api_client_service.get_client_for_user(db, str(tg_user.id))
            if not client:
                lines = [t(lang, "api_history_title"), "", t(lang, "api_history_empty")]
            else:
                logs = (
                    db.query(ApiRequestLog)
                    .filter(ApiRequestLog.api_client_id == client.id)
                    .order_by(ApiRequestLog.created_at.desc())
                    .limit(20)
                    .all()
                )
                if not logs:
                    lines = [t(lang, "api_history_title"), "", t(lang, "api_history_empty")]
                else:
                    lines = [t(lang, "api_history_title")]
                    for lg in logs:
                        lines.append(
                            f"• {lg.method} <code>{lg.endpoint}</code> — {lg.status_code} "
                            f"({lg.created_at.strftime('%d/%m %H:%M')})"
                        )
            try:
                await query.message.edit_text(
                    "\n".join(lines), parse_mode="HTML", reply_markup=api_back_keyboard(lang=lang),
                )
            except Exception:
                pass
        finally:
            db.close()
        return

    if data == "api_guide":
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            lang = _get_lang(db, tg_user.id)
            client = api_client_service.get_client_for_user(db, str(tg_user.id))
            rate_limit = client.rate_limit_per_minute if client else 30
            daily_limit = client.daily_limit if client else 2000
            text = "\n".join([
                t(lang, "api_guide_title"), "",
                t(lang, "api_guide_body", base=_api_base_url(), rate_limit=rate_limit, daily_limit=daily_limit),
            ])
            try:
                await query.message.edit_text(text, parse_mode="HTML", reply_markup=api_back_keyboard(lang=lang))
            except Exception:
                await context.bot.send_message(chat_id=tg_user.id, text=text, parse_mode="HTML",
                                                 reply_markup=api_back_keyboard(lang=lang))
        finally:
            db.close()
        return

    # ── back to product list ──
    if data == "back_products":
        # Same as 🏠 Home: cancel any in-progress input flow (e.g.
        # waiting_quantity from tapping 🛒 Mua on the product just viewed)
        # and delete its leftover prompt message before going back to the list.
        await _cleanup_flow_state(context, query.message.chat_id)
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
            await _render_product_detail(query, context, db, lang, product_id)
        finally:
            db.close()
        return

    # ── buy_more (post-delivery "🛍 Mua tiếp") ──
    if data == "buy_more":
        db = SessionLocal()
        try:
            lang = _get_lang(db, update.effective_user.id)

            # Clean up the whole just-completed purchase thread (product
            # card, quantity prompt, payment QR, delivery text + file) so
            # repeated purchases don't pile up as clutter — "Mua tiếp" is a
            # fresh start, not a continuation. Best-effort: any message
            # already gone/too old to delete is silently skipped.
            from services.payment_service import delete_order_thread_messages
            from services.order_service import get_latest_order_for_user
            from services.bot_service import bot_manager
            latest_order = get_latest_order_for_user(db, str(update.effective_user.id))
            if latest_order and bot_manager.is_running():
                try:
                    await delete_order_thread_messages(bot_manager._application.bot, latest_order, db)
                except Exception as e:
                    logger.warning(f"[buy_more] cleanup error: {e}")
            # The "🛍 Mua tiếp" message itself (this callback's own message)
            # is also part of the thread being cleared.
            try:
                await query.message.delete()
            except Exception:
                pass

            # Refresh product data from all active API connections first,
            # same as the product-list "Làm mới" button, so the list shown
            # below reflects current stock/price.
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
            context.user_data["last_products_page"] = 0
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=t(lang, "product_list_title"),
                parse_mode="HTML",
                reply_markup=product_list_keyboard(products, lang=lang, page=0, per_page=per_page),
            )
        except Exception as e:
            logger.warning(f"[buy_more] error: {e}")
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

            # "Mua ngay" reached from a "new product" notification (or any
            # other list/card that isn't already this product's detail
            # view) must show the full detail — image, price, stock,
            # description — before prompting for quantity. If the shopper
            # is already on this product's detail card (normal browsing:
            # detail -> "Mua ngay"), skip the redundant re-render.
            already_showing_detail = (
                context.user_data.get("detail_shown_product_id") == product_id
                and context.user_data.get("product_message_id") == query.message.message_id
            )
            if not already_showing_detail:
                await _render_product_detail(query, context, db, lang, product_id)
        finally:
            db.close()

        # If the shopper tapped "🛒 Mua" before on another product (or the
        # same one) without finishing, an old "Enter the quantity..." prompt
        # may still be sitting in the chat with its message_id about to be
        # overwritten below — delete it now or it becomes permanently
        # orphaned (only the latest id ever gets tracked/cleaned up).
        old_prompt_id = context.user_data.get("quantity_prompt_message_id")
        if old_prompt_id:
            await _safe_del(context.bot, query.message.chat_id, old_prompt_id)

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
            elif method == "wallet":
                await _setup_wallet_payment(context, db, tg_user, order, lang, processing_msg)
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
                "", t(lang, "sepay_bank", bank=html.escape(sepay.bank_name or sepay.bank_bin)),
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

            # For Binance Pay, an explicit "check payment" press triggers a
            # real (throttled) check against Pay History instead of only
            # reporting the last-known DB state.
            if sv == "pending_payment" and ps == "pending" and order.payment_network == "BINANCE" and order.payment_txid:
                from services.crypto_monitor import verify_binance_payment
                result = await verify_binance_payment(db, order)
                if result.get("ok"):
                    await query.answer(t(lang, "txid_ok_confirmed"), show_alert=True)
                    return
                if result.get("reason") == "permission_denied":
                    order.status = OrderStatus.waiting_manual_verification
                    db.commit()
                    await query.answer(t(lang, "txid_fail_permission_denied"), show_alert=True)
                    return
                db.refresh(order)
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

    # ── check_deposit (manual "🔄 Kiểm tra thanh toán" on the wallet deposit QR) ──
    if data.startswith("check_deposit:"):
        deposit_id = int(data.split(":")[1])
        tg_user = update.effective_user
        db = SessionLocal()
        try:
            lang = _get_lang(db, tg_user.id)
            deposit = db.query(WalletDeposit).filter(WalletDeposit.id == deposit_id).first()
            if not deposit or deposit.telegram_user_id != str(tg_user.id):
                await query.answer(t(lang, "order_not_found"), show_alert=True)
                return
            if deposit.status == WalletDepositStatus.credited:
                await query.answer(t(lang, "wallet_deposit_check_credited", ref=deposit.reference_code), show_alert=True)
            elif deposit.status == WalletDepositStatus.pending:
                await query.answer(t(lang, "wallet_deposit_check_pending", ref=deposit.reference_code), show_alert=True)
            else:
                await query.answer(t(lang, "wallet_deposit_check_gone"), show_alert=True)
        finally:
            db.close()
        return

    # ── cancel_deposit ("❌ Hủy đơn" on the wallet deposit QR) ──
    if data.startswith("cancel_deposit:"):
        deposit_id = int(data.split(":")[1])
        tg_user = update.effective_user
        db = SessionLocal()
        try:
            lang = _get_lang(db, tg_user.id)
            deposit = db.query(WalletDeposit).filter(WalletDeposit.id == deposit_id).first()
            if not deposit or deposit.telegram_user_id != str(tg_user.id):
                await query.answer(t(lang, "order_not_found"), show_alert=True)
                return

            # Only a still-pending deposit can be cancelled by the shopper —
            # atomic status-guarded UPDATE so a webhook crediting it at the
            # exact same moment can never be raced/overwritten by this cancel.
            from sqlalchemy import text as _sql_text
            rows = db.execute(
                _sql_text("UPDATE wallet_deposits SET status='cancelled' WHERE id=:id AND status='pending'"),
                {"id": deposit_id},
            )
            db.commit()
            if rows.rowcount == 0:
                db.refresh(deposit)
                if deposit.status == WalletDepositStatus.credited:
                    await query.answer(t(lang, "wallet_deposit_check_credited", ref=deposit.reference_code), show_alert=True)
                else:
                    await query.answer(t(lang, "wallet_deposit_cancel_denied"), show_alert=True)
                return

            try:
                if query.message.photo:
                    await query.message.edit_caption(
                        caption=t(lang, "wallet_deposit_cancelled_user"), reply_markup=None,
                    )
                else:
                    await query.message.edit_text(t(lang, "wallet_deposit_cancelled_user"), reply_markup=None)
            except Exception:
                await query.answer()
                await context.bot.send_message(chat_id=tg_user.id, text=t(lang, "wallet_deposit_cancelled_user"))
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
                value = bnb_cfg.get("receiver_binance_id") or "—"
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
            if sv != "pending_payment" or order.payment_network not in ("BEP20", "TRC20", "ERC20", "BINANCE"):
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

async def _finalize_admin_issue_message(bot, chat_id, message_id, status_line: str = None):
    """
    After an admin acts on an issue (refund/reject/resolve), strip the
    action keyboard from the original admin DM so it can't be tapped again
    — works uniformly for plain-text and photo/video/document alerts since
    it only touches the reply_markup, never the text/caption body. A
    separate status message (rather than editing the caption/text) is sent
    to record what happened, so this never fails on media messages.
    """
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except Exception as e:
        logger.warning(f"[_finalize_admin_issue_message] strip keyboard failed (non-fatal): {e}")
    if status_line:
        try:
            await bot.send_message(chat_id=chat_id, text=status_line, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"[_finalize_admin_issue_message] status message failed (non-fatal): {e}")


async def _create_and_notify_issue(context, db, order, tg_user, issue_text: str = None,
                                    media_type: str = None, telegram_file_id: str = None) -> "OrderIssue":
    """Shared by the text-only and media issue-report flows: saves the
    order_issues row, precomputes the max refund for the admin's reference,
    and DMs the admin immediately with the full detail + action keyboard."""
    result = refund_service.compute_refund(order)
    issue = OrderIssue(
        order_id=order.id,
        telegram_user_id=str(tg_user.id),
        telegram_chat_id=str(tg_user.id),
        issue_text=issue_text or None,
        media_type=media_type,
        telegram_file_id=telegram_file_id,
        status=IssueStatus.open,
        calculated_refund_amount=result["amount"],
        calculated_refund_currency=result["currency"],
    )
    db.add(issue)
    db.commit()
    db.refresh(issue)

    admin_id = _get_admin_id(db)
    if admin_id:
        from bot.notifier import notify_admin_new_issue
        try:
            await notify_admin_new_issue(
                context.bot, order, issue, admin_id,
                admin_keyboard=admin_issue_keyboard(issue.id, lang="vi"),
            )
        except Exception as e:
            logger.error(f"[_create_and_notify_issue] admin notify failed: {e}")
    return issue


async def media_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Photo/video/document capture for the "⚠️ Báo lỗi" flow. Only acts when
    the user is mid-report (state == waiting_issue_text); otherwise ignores
    the media silently so it never interferes with unrelated flows.
    """
    state = context.user_data.get("state")
    if state != "waiting_issue_text":
        return

    order_id = context.user_data.get("issue_order_id")
    msg = update.message
    media_type, file_id = None, None
    if msg.photo:
        media_type, file_id = "photo", msg.photo[-1].file_id
    elif msg.video:
        media_type, file_id = "video", msg.video.file_id
    elif msg.document:
        media_type, file_id = "document", msg.document.file_id
    else:
        return

    context.user_data.clear()
    db = SessionLocal()
    try:
        lang = _get_lang(db, update.effective_user.id)
        tg_user = update.effective_user
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            await msg.reply_text(t(lang, "order_not_found"))
            return
        caption = (msg.caption or "").strip() or None
        await _create_and_notify_issue(
            context, db, order, tg_user, issue_text=caption,
            media_type=media_type, telegram_file_id=file_id,
        )
        await msg.reply_text(t(lang, "issue_report_saved"))
    except Exception as e:
        logger.error(f"[media_message_handler] error: {e}")
        try:
            await msg.reply_text(t(_get_lang(db, update.effective_user.id), "issue_report_error"))
        except Exception:
            pass
    finally:
        db.close()


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ── Admin bulk icon import: forward/paste a message containing Telegram
    # custom emoji here and it's read straight off the message entities —
    # this is the only way to pull custom_emoji_id for icons mixed together
    # from several different sticker packs (getStickerSet only covers one
    # named pack at a time). See services/telegram_emoji.import_icons_from_entities.
    entities_map = update.message.parse_entities(types=["custom_emoji"])
    if entities_map:
        db = SessionLocal()
        try:
            admin_id = _get_admin_id(db)
            tg_user = update.effective_user
            if admin_id and str(tg_user.id) == str(admin_id):
                from services.telegram_emoji import import_icons_from_entities
                result = import_icons_from_entities(db, entities_map)
                await update.message.reply_text(
                    f"✨ Đã phát hiện {len(entities_map)} icon tùy chỉnh trong tin nhắn.\n"
                    f"➕ Đã thêm mới: {result['added']}\n"
                    f"⏭ Đã có sẵn (bỏ qua): {result['skipped_duplicate']}\n\n"
                    "Vào trang quản trị → Kho icon để đặt tên và dùng cho sản phẩm."
                )
                return
        finally:
            db.close()

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

    if state == "waiting_wallet_deposit_amount":
        currency = context.user_data.get("wallet_dep_currency")
        method = context.user_data.get("wallet_dep_method")
        raw = (update.message.text or "").strip().replace(",", "").replace(".", "" if currency == "VND" else ".")
        context.user_data.pop("state", None)
        context.user_data.pop("wallet_dep_currency", None)
        context.user_data.pop("wallet_dep_method", None)

        try:
            amount = float(raw)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(t(lang, "wallet_amount_invalid"))
            return

        db = SessionLocal()
        try:
            tg_user = update.effective_user
            payment_info = _get_deposit_payment_display(db, method)
            if not payment_info:
                await update.message.reply_text(t(lang, "wallet_deposit_no_payment_configured"))
                return

            amount = wallet_service.quantize_amount(currency, amount)
            ref = wallet_service.generate_deposit_reference()

            network_map = {"binance_pay": "BINANCE", "usdt_bep20": "BEP20",
                            "usdt_trc20": "TRC20", "usdt_erc20": "ERC20"}
            network = network_map.get(method)
            expiry_minutes = 60
            required_conf = None
            final_amount = amount

            if network:
                from services.exchange_rate_service import generate_unique_crypto_amount
                # Add a tiny offset so this deposit's amount never collides
                # with another pending deposit OR order payment on the same
                # shared wallet address — the same on-chain amount-matching
                # trick used for order payments.
                final_amount = generate_unique_crypto_amount(db, amount, network)
                if method in ("usdt_bep20", "usdt_trc20", "usdt_erc20"):
                    from models import PaymentMethod
                    from crypto import decrypt
                    pm = db.query(PaymentMethod).filter(
                        PaymentMethod.method_code == method, PaymentMethod.is_active == True
                    ).first()
                    pm_cfg = {}
                    if pm and pm.config_encrypted:
                        try:
                            pm_cfg = json.loads(decrypt(pm.config_encrypted) or "{}")
                        except Exception:
                            pm_cfg = {}
                    required_conf = int(pm_cfg.get("required_confirmations") or (20 if network == "TRC20" else 12))
                    expiry_minutes = int(pm_cfg.get("timeout_minutes") or 60)
                else:  # binance_pay
                    from services.binance_service import get_binance_config
                    bnb_cfg = get_binance_config(db) or {}
                    expiry_minutes = int(bnb_cfg.get("order_expiry_minutes") or 30)

            deposit = WalletDeposit(
                telegram_user_id=str(tg_user.id),
                currency=WalletCurrency(currency),
                amount=final_amount,
                method=method,
                reference_code=ref,
                status=WalletDepositStatus.pending,
                network=network,
                receiving_address=payment_info.get("address") or payment_info.get("acc"),
                payment_content=ref,
                chat_id=tg_user.id,
                confirmations=0,
                required_confirmations=required_conf,
                expires_at=datetime.utcnow() + timedelta(minutes=expiry_minutes),
            )
            db.add(deposit)
            db.commit()
            db.refresh(deposit)

            if currency == "VND":
                text = t(lang, "wallet_deposit_created_vnd", ref=ref, amount=format_vnd(final_amount),
                         bank=payment_info["bank"], acc=payment_info["acc"], acc_name=payment_info["acc_name"])
                kbd = wallet_deposit_qr_keyboard(deposit.id, lang=lang)
                cfg = db.query(TelegramBotConfig).first()
                shop_name = getattr(cfg, "shop_name", "") or "" if cfg else ""
                qr_url = generate_vietqr_url(
                    bank_bin=payment_info["bank_bin"],
                    account_number=payment_info["acc"],
                    amount=final_amount,
                    payment_code=ref,
                    account_name=payment_info["acc_name"],
                    shop_name=shop_name,
                )
                sent = None
                try:
                    sent = await update.message.reply_photo(
                        photo=qr_url, caption=text, parse_mode="HTML", reply_markup=kbd,
                    )
                except Exception:
                    pass
                if not sent:
                    try:
                        import httpx as _httpx
                        async with _httpx.AsyncClient(timeout=15) as c:
                            resp = await c.get(qr_url)
                        if resp.status_code == 200:
                            sent = await update.message.reply_photo(
                                photo=io.BytesIO(resp.content), caption=text,
                                parse_mode="HTML", reply_markup=kbd,
                            )
                    except Exception:
                        pass
                if not sent:
                    text_only = text + f'\n\n🔗 <a href="{qr_url}">Mở QR VietQR</a>'
                    sent = await update.message.reply_text(
                        text_only, parse_mode="HTML", reply_markup=kbd, disable_web_page_preview=True,
                    )
            else:
                text = t(lang, "wallet_deposit_created_usdt", ref=ref, amount=f"{final_amount:.4f}",
                         network=payment_info["network"], address=payment_info["address"])
                sent = await update.message.reply_text(text, parse_mode="HTML")
            deposit.deposit_message_id = sent.message_id
            db.commit()
        finally:
            db.close()
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

            if order.payment_network == "BINANCE":
                from services.crypto_monitor import verify_binance_payment
                result = await verify_binance_payment(db, order, submitted_txid=txid)
            else:
                from services.crypto_monitor import verify_txid_for_order
                result = await verify_txid_for_order(db, order, txid)

            if result.get("ok"):
                try:
                    await checking_msg.edit_text(t(lang, "txid_ok_confirmed"))
                except Exception:
                    pass
                return

            reason = result.get("reason", "generic")

            # Binance API key lacking Pay History permission → fall back to
            # manual admin review instead of silently failing or retrying.
            if order.payment_network == "BINANCE" and reason == "permission_denied":
                order.status = OrderStatus.waiting_manual_verification
                db.commit()
                try:
                    await checking_msg.edit_text(t(lang, "txid_fail_permission_denied"))
                except Exception:
                    pass
                return

            key = f"txid_fail_{reason}"
            try:
                if reason == "insufficient_confirmations":
                    msg = t(lang, key, confirmations=result.get("confirmations", 0), required=result.get("required", 0))
                else:
                    msg = t(lang, key)
            except Exception:
                msg = t(lang, "txid_fail_generic")

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

    if state == "waiting_order_search":
        raw = (update.message.text or "").strip()
        context.user_data.pop("state", None)
        if not raw:
            await update.message.reply_text(t(lang, "order_search_invalid"))
            return
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            admin_id = _get_admin_id(db)
            is_admin = str(tg_user.id) == str(admin_id)
            orders = find_orders(db, raw, telegram_user_id=str(tg_user.id), is_admin=is_admin)
            if not orders:
                await update.message.reply_text(t(lang, "order_search_not_found"))
                return
            if len(orders) == 1:
                order = orders[0]
                text = await _render_order_detail_text(db, order, lang)
                await update.message.reply_text(
                    text, parse_mode="HTML",
                    reply_markup=order_detail_keyboard(order.id, lang=lang),
                )
            else:
                await update.message.reply_text(
                    t(lang, "order_search_pick_title", count=len(orders)), parse_mode="HTML",
                    reply_markup=order_search_list_keyboard(orders, lang=lang),
                )
        finally:
            db.close()
        return

    if state == "waiting_issue_text":
        order_id = context.user_data.get("issue_order_id")
        issue_text = (update.message.text or "").strip()
        context.user_data.clear()
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            order = db.query(Order).filter(Order.id == order_id).first()
            if not order:
                await update.message.reply_text(t(lang, "order_not_found"))
                return
            await _create_and_notify_issue(
                context, db, order, tg_user, issue_text=issue_text, media_type=None, telegram_file_id=None,
            )
            await update.message.reply_text(t(lang, "issue_report_saved"))
        except Exception as e:
            logger.error(f"[waiting_issue_text] error: {e}")
            await update.message.reply_text(t(lang, "issue_report_error"))
        finally:
            db.close()
        return

    if state == "waiting_admin_reply":
        issue_id = context.user_data.get("admin_issue_id")
        reply_text = (update.message.text or "").strip()
        context.user_data.clear()
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            admin_id = _get_admin_id(db)
            if str(tg_user.id) != str(admin_id):
                return
            issue = db.query(OrderIssue).filter(OrderIssue.id == issue_id).first()
            if not issue:
                await update.message.reply_text(t("vi", "issue_not_found"))
                return
            order = db.query(Order).filter(Order.id == issue.order_id).first()
            if issue.status == IssueStatus.open:
                issue.status = IssueStatus.reviewing
                db.commit()
            try:
                buyer_lang = _get_lang(db, issue.telegram_user_id)
                await context.bot.send_message(
                    chat_id=int(issue.telegram_chat_id or issue.telegram_user_id),
                    text=t(buyer_lang, "issue_reply_received", code=order.order_code if order else "—",
                           text=html.escape(reply_text)),
                    parse_mode="HTML",
                )
                await update.message.reply_text(t("vi", "issue_reply_sent"))
            except Exception as e:
                logger.error(f"[waiting_admin_reply] send failed: {e}")
        finally:
            db.close()
        return

    if state == "waiting_admin_reject_reason":
        issue_id = context.user_data.get("admin_issue_id")
        reason = (update.message.text or "").strip()
        context.user_data.clear()
        db = SessionLocal()
        try:
            tg_user = update.effective_user
            admin_id = _get_admin_id(db)
            if str(tg_user.id) != str(admin_id):
                return
            issue = db.query(OrderIssue).filter(OrderIssue.id == issue_id).first()
            if not issue or issue.status not in (IssueStatus.open, IssueStatus.reviewing):
                await update.message.reply_text(t("vi", "issue_already_handled"))
                return
            order = db.query(Order).filter(Order.id == issue.order_id).first()
            issue.status = IssueStatus.rejected
            issue.handled_by = str(tg_user.id)
            issue.handled_at = datetime.utcnow()
            issue.resolution_note = reason
            db.commit()
            await update.message.reply_text(t("vi", "issue_rejected_admin", id=issue.id))
            try:
                buyer_lang = _get_lang(db, issue.telegram_user_id)
                await context.bot.send_message(
                    chat_id=int(issue.telegram_chat_id or issue.telegram_user_id),
                    text=t(buyer_lang, "issue_rejected_user", code=order.order_code if order else "—",
                           reason=html.escape(reason)),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"[waiting_admin_reject_reason] notify failed: {e}")
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
            from services.shared_catalog import resolve_api_product

            # Re-sync if stale (>60s)
            if p.delivery_mode == DeliveryMode.api_auto:
                for src in sources:
                    src_ap = resolve_api_product(db, src)
                    if src_ap and src_ap.last_sync_at:
                        age = datetime.utcnow() - src_ap.last_sync_at
                        if age > timedelta(seconds=60):
                            from services.api_service import sync_api_products
                            await sync_api_products(db, src_ap.api_connection_id)
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
