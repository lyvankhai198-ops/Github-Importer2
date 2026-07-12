from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from bot.i18n import t


def main_menu_keyboard(lang: str = "vi", is_admin: bool = False) -> ReplyKeyboardMarkup:
    buttons = [
        [t(lang, "menu_products"), t(lang, "menu_orders")],
        [t(lang, "menu_language"), t(lang, "menu_support")],
    ]
    if is_admin:
        buttons.append([t(lang, "menu_admin")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇻🇳 Tiếng Việt", callback_data="set_lang:vi")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="set_lang:en")],
    ])


def product_list_keyboard(products: list, lang: str = "vi") -> InlineKeyboardMarkup:
    """Product list with 🟢/🔴/⚠️ status emoji."""
    buttons = []
    for item in products:
        p = item["product"]
        stock = item.get("stock", 0)
        status = item.get("status", "in_stock")
        if status == "unavailable":
            emoji = "⚠️"
        elif status == "out_of_stock" or stock <= 0:
            emoji = "🔴"
        elif stock > 10:
            emoji = "🟢"
        else:
            emoji = "🟡"
        label = f"{emoji} {p.name}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"product:{p.id}")])
    buttons.append([InlineKeyboardButton(t(lang, "btn_close"), callback_data="close")])
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
    }
    rows = []
    for code in ["bank_transfer", "binance_pay", "usdt_bep20", "usdt_trc20"]:
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
    """Keyboard shown with crypto payment instructions."""
    rows = [
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
