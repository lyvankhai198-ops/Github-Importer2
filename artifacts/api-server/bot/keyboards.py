from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from bot.i18n import t
from services.normalize import format_vnd, format_usdt


def main_menu_keyboard(lang: str = "vi", is_admin: bool = False) -> ReplyKeyboardMarkup:
    buttons = [
        [t(lang, "menu_products"), t(lang, "menu_orders")],
        [t(lang, "menu_language"), t(lang, "menu_support")],
    ]
    if is_admin:
        buttons.append([t(lang, "menu_admin")])
    # Persistent row — "☰ Menu" / "⬅️ Quay lại" must always be present and never
    # removed, so every reply_markup in the bot should use this keyboard (never
    # ReplyKeyboardRemove) to keep it visible across the whole conversation.
    buttons.append([t(lang, "menu_persistent"), t(lang, "menu_back")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇻🇳 Tiếng Việt", callback_data="set_lang:vi")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="set_lang:en")],
    ])


def product_list_keyboard(products: list, lang: str = "vi",
                           page: int = 0, per_page: int = 15) -> InlineKeyboardMarkup:
    """
    Product list keyboard.
    - In-stock: [icon] Name - price  → product:{id}
    - Out-of-stock/unavailable: ❌ Name - Hết hàng  → oos:{id}
    - Pagination if > per_page items.
    - Bottom row: 🔄 Làm mới | 🏠 Trang chủ
    """
    total = len(products)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    page_products = products[start:start + per_page]

    buttons = []
    for item in page_products:
        p = item["product"]
        status = item.get("status", "in_stock")
        is_unavailable = status in ("out_of_stock", "unavailable")

        display_name = p.name_en if (lang == "en" and getattr(p, "name_en", None)) else p.name
        price_str = f"{format_vnd(p.sale_price)}đ" if lang == "vi" else f"{format_usdt(p.price_usdt)} USDT"

        if is_unavailable:
            label = f"❌ {display_name} - {t(lang, 'product_list_out_of_stock')}"
            cb = f"oos:{p.id}"
        elif status == "accepting_orders":
            icon = (getattr(p, "telegram_icon", None) or "").strip() or "📦"
            label = f"🟡 {icon} {display_name} - {price_str} ({t(lang, 'product_list_accept_order')})"
            cb = f"product:{p.id}"
        else:
            icon = (getattr(p, "telegram_icon", None) or "").strip() or "📦"
            label = f"{icon} {display_name} - {price_str}"
            cb = f"product:{p.id}"

        buttons.append([InlineKeyboardButton(label, callback_data=cb)])

    # Pagination row (only when > 1 page)
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️", callback_data=f"products_page:{page - 1}"))
        page_label = f"Trang {page + 1}/{total_pages}" if lang == "vi" else f"Page {page + 1}/{total_pages}"
        nav.append(InlineKeyboardButton(page_label, callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("➡️", callback_data=f"products_page:{page + 1}"))
        buttons.append(nav)

    # Refresh + Home
    buttons.append([
        InlineKeyboardButton(t(lang, "btn_refresh"), callback_data=f"refresh_products:{page}"),
        InlineKeyboardButton(t(lang, "btn_home"), callback_data="home"),
    ])

    return InlineKeyboardMarkup(buttons)


def product_detail_keyboard(product_id: int, lang: str = "vi") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_buy_now"), callback_data=f"buy:{product_id}")],
        [
            InlineKeyboardButton(t(lang, "btn_back"), callback_data="back_products"),
            InlineKeyboardButton(t(lang, "btn_home"), callback_data="home"),
        ],
    ])


def out_of_stock_keyboard(product_id: int, lang: str = "vi") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_check_again"), callback_data=f"product:{product_id}")],
        [InlineKeyboardButton(t(lang, "btn_back"), callback_data="back_products")],
        [InlineKeyboardButton(t(lang, "btn_home"), callback_data="home")],
    ])


def payment_method_keyboard(order_id: int, enabled_methods: list, lang: str = "vi") -> InlineKeyboardMarkup:
    """
    Show only the enabled payment methods.
    enabled_methods: list of method_code strings, e.g. ["bank_transfer", "binance_pay"]
    """
    METHOD_BUTTONS = {
        "bank_transfer":  ("btn_bank_transfer",  f"pay_method:{order_id}:bank_transfer"),
        "binance_pay":    ("btn_binance_pay",     f"pay_method:{order_id}:binance_pay"),
        "usdt_bep20":     ("btn_usdt_bep20",      f"pay_method:{order_id}:usdt_bep20"),
        "usdt_trc20":     ("btn_usdt_trc20",      f"pay_method:{order_id}:usdt_trc20"),
        "usdt_erc20":     ("btn_usdt_erc20",      f"pay_method:{order_id}:usdt_erc20"),
    }
    rows = []
    for code in ["bank_transfer", "binance_pay", "usdt_bep20", "usdt_trc20", "usdt_erc20"]:
        if code in enabled_methods:
            label_key, callback = METHOD_BUTTONS[code]
            rows.append([InlineKeyboardButton(t(lang, label_key), callback_data=callback)])
    rows.append([InlineKeyboardButton(t(lang, "btn_cancel_order"), callback_data=f"cancel_pending:{order_id}")])
    return InlineKeyboardMarkup(rows)


def payment_keyboard(order_id: int, support_username: str = "", lang: str = "vi",
                     show_regen_qr: bool = False) -> InlineKeyboardMarkup:
    """Keyboard shown with SePay QR payment message."""
    rows = [
        [InlineKeyboardButton(t(lang, "btn_check_payment"), callback_data=f"check_payment:{order_id}")],
    ]
    if show_regen_qr:
        rows.append([InlineKeyboardButton(t(lang, "btn_regen_qr"), callback_data=f"regen_qr:{order_id}")])
    rows.append([InlineKeyboardButton(t(lang, "btn_cancel_pending"), callback_data=f"cancel_pending:{order_id}")])
    support_row = []
    if support_username:
        support_row.append(
            InlineKeyboardButton(t(lang, "btn_support"), url=f"https://t.me/{support_username.lstrip('@')}")
        )
    support_row.append(InlineKeyboardButton(t(lang, "btn_home"), callback_data="home"))
    rows.append(support_row)
    return InlineKeyboardMarkup(rows)


def binance_manual_keyboard(order_id: int, support_username: str = "", lang: str = "vi") -> InlineKeyboardMarkup:
    """Keyboard for Binance Pay Manual payment."""
    rows = [
        [
            InlineKeyboardButton(t(lang, "btn_copy_payid"), callback_data=f"copy_payid:{order_id}"),
            InlineKeyboardButton(t(lang, "btn_copy_amount"), callback_data=f"copy_amt:{order_id}"),
        ],
        [InlineKeyboardButton(t(lang, "btn_check_payment"), callback_data=f"check_payment:{order_id}")],
        [InlineKeyboardButton(t(lang, "btn_cancel_pending"), callback_data=f"cancel_pending:{order_id}")],
    ]
    if support_username:
        rows.append([InlineKeyboardButton(t(lang, "btn_support"), url=f"https://t.me/{support_username.lstrip('@')}")])
    rows.append([InlineKeyboardButton(t(lang, "btn_home"), callback_data="home")])
    return InlineKeyboardMarkup(rows)


def binance_merchant_keyboard(order_id: int, checkout_url: str = "", support_username: str = "",
                               lang: str = "vi") -> InlineKeyboardMarkup:
    rows = []
    if checkout_url:
        rows.append([InlineKeyboardButton(t(lang, "btn_open_binance_merchant"), url=checkout_url)])
    rows.append([InlineKeyboardButton(t(lang, "btn_check_binance"), callback_data=f"check_payment:{order_id}")])
    rows.append([InlineKeyboardButton(t(lang, "btn_cancel_pending"), callback_data=f"cancel_pending:{order_id}")])
    if support_username:
        rows.append([InlineKeyboardButton(t(lang, "btn_support"), url=f"https://t.me/{support_username.lstrip('@')}")])
    rows.append([InlineKeyboardButton(t(lang, "btn_home"), callback_data="home")])
    return InlineKeyboardMarkup(rows)


def crypto_payment_keyboard(order_id: int, support_username: str = "", lang: str = "vi") -> InlineKeyboardMarkup:
    """Keyboard shown with crypto (BEP20/TRC20/ERC20) payment instructions."""
    rows = [
        [
            InlineKeyboardButton(t(lang, "btn_copy_address"), callback_data=f"copy_addr:{order_id}"),
            InlineKeyboardButton(t(lang, "btn_copy_amount"), callback_data=f"copy_amt:{order_id}"),
        ],
        [InlineKeyboardButton(t(lang, "btn_verify_txid"), callback_data=f"verify_txid:{order_id}")],
        [InlineKeyboardButton(t(lang, "btn_check_payment"), callback_data=f"check_payment:{order_id}")],
        [InlineKeyboardButton(t(lang, "btn_cancel_pending"), callback_data=f"cancel_pending:{order_id}")],
    ]
    if support_username:
        rows.append([InlineKeyboardButton(t(lang, "btn_support"), url=f"https://t.me/{support_username.lstrip('@')}")])
    rows.append([InlineKeyboardButton(t(lang, "btn_home"), callback_data="home")])
    return InlineKeyboardMarkup(rows)


def post_delivery_keyboard(order_id: int, support_username: str = "", lang: str = "vi") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📦 Xem đơn hàng" if lang == "vi" else "📦 View order", callback_data=f"view_order:{order_id}")],
        [InlineKeyboardButton("📥 Tải lại tài khoản" if lang == "vi" else "📥 Re-download accounts", callback_data=f"reload_order:{order_id}")],
    ]
    support_row = []
    if support_username:
        support_row.append(
            InlineKeyboardButton(t(lang, "btn_support"), url=f"https://t.me/{support_username.lstrip('@')}")
        )
    support_row.append(InlineKeyboardButton(t(lang, "btn_home"), callback_data="home"))
    rows.append(support_row)
    return InlineKeyboardMarkup(rows)


def partial_delivery_keyboard(order_id: int, support_username: str = "", lang: str = "vi") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📥 Tải lại tài khoản đã nhận" if lang == "vi" else "📥 Re-download received accounts",
                              callback_data=f"reload_order:{order_id}")],
    ]
    if support_username:
        rows.append([InlineKeyboardButton(t(lang, "btn_support"), url=f"https://t.me/{support_username.lstrip('@')}")])
    rows.append([InlineKeyboardButton(t(lang, "btn_home"), callback_data="home")])
    return InlineKeyboardMarkup(rows)


# ── Legacy compat (payment_keyboard used without lang in payment_service) ──────
def confirm_order_keyboard(product_id: int, quantity: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Xác nhận mua", callback_data=f"confirm_order:{product_id}:{quantity}"),
            InlineKeyboardButton("❌ Hủy", callback_data="cancel_order"),
        ]
    ])
