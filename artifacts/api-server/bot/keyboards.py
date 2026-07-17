from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from bot.i18n import t
from services.normalize import format_vnd, format_usdt


def main_menu_keyboard(lang: str = "en", is_admin: bool = False) -> ReplyKeyboardMarkup:
    buttons = [
        [t(lang, "menu_products"), t(lang, "menu_orders")],
        [t(lang, "menu_btn_wallet"), t(lang, "menu_support")],
        [t(lang, "menu_btn_api"), t(lang, "menu_btn_account")],
    ]
    if is_admin:
        buttons.append([t(lang, "menu_admin")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def product_list_keyboard(products: list, lang: str = "en",
                           page: int = 0, per_page: int = 15) -> InlineKeyboardMarkup:
    """
    Product list keyboard.
    - In-stock: [icon] Name - price  → product:{id}
    - Out-of-stock/unavailable: ❌ Name - Out of stock  → oos:{id}
    - Pagination if > per_page items.
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

        if getattr(p, "name_en", None):
            display_name = p.name_en
        else:
            from services.normalize import translate_shorthand_to_en
            display_name = translate_shorthand_to_en(p.name)

        price_str = f"{format_usdt(p.price_usdt)} USDT"

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
        nav.append(InlineKeyboardButton(f"Page {page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("➡️", callback_data=f"products_page:{page + 1}"))
        buttons.append(nav)

    # Refresh + Home
    buttons.append([
        InlineKeyboardButton(t(lang, "btn_refresh"), callback_data=f"refresh_products:{page}"),
        InlineKeyboardButton(t(lang, "btn_home"), callback_data="home"),
    ])

    return InlineKeyboardMarkup(buttons)


def product_detail_keyboard(product_id: int, lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_buy_now"), callback_data=f"buy:{product_id}")],
        [
            InlineKeyboardButton(t(lang, "btn_back"), callback_data="back_products"),
            InlineKeyboardButton(t(lang, "btn_home"), callback_data="home"),
        ],
    ])


def out_of_stock_keyboard(product_id: int, lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_check_again"), callback_data=f"product:{product_id}")],
        [InlineKeyboardButton(t(lang, "btn_notify_restock"), callback_data=f"notify_restock:{product_id}")],
        [InlineKeyboardButton(t(lang, "btn_back"), callback_data="back_products")],
        [InlineKeyboardButton(t(lang, "btn_home"), callback_data="home")],
    ])


def payment_method_keyboard(order_id: int, enabled_methods: list, lang: str = "en",
                              show_wallet: bool = False) -> InlineKeyboardMarkup:
    """
    Show only the enabled payment methods.
    enabled_methods: list of method_code strings, e.g. ["bank_transfer", "binance_pay"]
    show_wallet: adds a "Pay with Wallet" row (VND-only) above the others.
    """
    METHOD_BUTTONS = {
        "bank_transfer":  ("btn_bank_transfer",  f"pay_method:{order_id}:bank_transfer"),
        "binance_pay":    ("btn_binance_pay",     f"pay_method:{order_id}:binance_pay"),
        "usdt_bep20":     ("btn_usdt_bep20",      f"pay_method:{order_id}:usdt_bep20"),
        "usdt_trc20":     ("btn_usdt_trc20",      f"pay_method:{order_id}:usdt_trc20"),
        "usdt_erc20":     ("btn_usdt_erc20",      f"pay_method:{order_id}:usdt_erc20"),
    }
    rows = []
    if show_wallet:
        rows.append([InlineKeyboardButton(t(lang, "btn_pay_wallet"), callback_data=f"pay_method:{order_id}:wallet")])
    for code in ["bank_transfer", "binance_pay", "usdt_bep20", "usdt_trc20", "usdt_erc20"]:
        if code in enabled_methods:
            label_key, callback = METHOD_BUTTONS[code]
            rows.append([InlineKeyboardButton(t(lang, label_key), callback_data=callback)])
    rows.append([InlineKeyboardButton(t(lang, "btn_cancel_order"), callback_data=f"cancel_pending:{order_id}")])
    return InlineKeyboardMarkup(rows)


# ── Wallet ───────────────────────────────────────────────────────────────────

def wallet_menu_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_wallet_deposit"), callback_data="wallet_deposit")],
        [InlineKeyboardButton(t(lang, "btn_wallet_history"), callback_data="wallet_history")],
        [InlineKeyboardButton(t(lang, "btn_home"), callback_data="home")],
    ])


def wallet_deposit_currency_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_wallet_deposit_vnd"), callback_data="wallet_dep_cur:VND")],
        [InlineKeyboardButton(t(lang, "btn_wallet_deposit_usdt"), callback_data="wallet_dep_cur:USDT")],
        [InlineKeyboardButton(t(lang, "btn_back"), callback_data="wallet_home")],
    ])


def wallet_deposit_method_keyboard(currency: str, enabled_methods: list, lang: str = "en") -> InlineKeyboardMarkup:
    """Reuse the same method labels as order payment (bank/crypto), filtered by currency."""
    vnd_methods = {"bank_transfer": ("btn_bank_transfer", "bank_transfer")}
    usdt_methods = {
        "binance_pay": ("btn_binance_pay", "binance_pay"),
        "usdt_bep20":  ("btn_usdt_bep20",  "usdt_bep20"),
        "usdt_trc20":  ("btn_usdt_trc20",  "usdt_trc20"),
        "usdt_erc20":  ("btn_usdt_erc20",  "usdt_erc20"),
    }
    pool = vnd_methods if currency == "VND" else usdt_methods
    rows = []
    for code, (label_key, m) in pool.items():
        if code in enabled_methods:
            rows.append([InlineKeyboardButton(t(lang, label_key), callback_data=f"wallet_dep_method:{currency}:{m}")])
    rows.append([InlineKeyboardButton(t(lang, "btn_back"), callback_data="wallet_deposit")])
    return InlineKeyboardMarkup(rows)


def wallet_deposit_qr_keyboard(deposit_id: int, lang: str = "en") -> InlineKeyboardMarkup:
    """Shown under the VND deposit QR: manual check + cancel."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_check_deposit"), callback_data=f"check_deposit:{deposit_id}")],
        [InlineKeyboardButton(t(lang, "btn_cancel_deposit"), callback_data=f"cancel_deposit:{deposit_id}")],
    ])


def wallet_insufficient_balance_keyboard(order_id: int, lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_wallet_deposit"), callback_data="wallet_deposit")],
        [InlineKeyboardButton(t(lang, "btn_cancel_order"), callback_data=f"cancel_pending:{order_id}")],
    ])


# ── Customer API ─────────────────────────────────────────────────────────────

def api_menu_keyboard(lang: str = "en", has_key: bool = False, swagger_url: str = "") -> InlineKeyboardMarkup:
    rows = []
    if swagger_url:
        rows.append([InlineKeyboardButton(t(lang, "btn_api_swagger"), url=swagger_url)])
    rows.append([InlineKeyboardButton(t(lang, "btn_api_regenerate"), callback_data="api_regenerate")])
    rows.append([InlineKeyboardButton(t(lang, "btn_home"), callback_data="home")])
    return InlineKeyboardMarkup(rows)


def account_info_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "menu_btn_wallet"), callback_data="wallet_home")],
        [InlineKeyboardButton(t(lang, "menu_orders"), callback_data="account_orders")],
        [InlineKeyboardButton(t(lang, "btn_wallet_history"), callback_data="wallet_history")],
        [InlineKeyboardButton(t(lang, "btn_account_docs"), callback_data="api_guide")],
        [InlineKeyboardButton(t(lang, "btn_home"), callback_data="home")],
    ])


def api_back_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_back"), callback_data="api_home")],
    ])


def api_confirm_keyboard(action: str, lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ OK", callback_data=f"api_confirm:{action}")],
        [InlineKeyboardButton(t(lang, "btn_back"), callback_data="api_home")],
    ])


def payment_keyboard(order_id: int, support_username: str = "", lang: str = "en",
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


def binance_keyboard(order_id: int, support_username: str = "", lang: str = "en") -> InlineKeyboardMarkup:
    """Keyboard for Binance Pay."""
    rows = [
        [
            InlineKeyboardButton(t(lang, "btn_copy_payid"), callback_data=f"copy_payid:{order_id}"),
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


def crypto_payment_keyboard(order_id: int, support_username: str = "", lang: str = "en") -> InlineKeyboardMarkup:
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


def post_delivery_keyboard(order_id: int, support_username: str = "", lang: str = "en") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🛍 Buy more", callback_data="buy_more")],
        [InlineKeyboardButton("📦 View order", callback_data=f"reload_order:{order_id}")],
    ]
    support_row = []
    if support_username:
        support_row.append(
            InlineKeyboardButton("💬 Support", url=f"https://t.me/{support_username.lstrip('@')}")
        )
    support_row.append(InlineKeyboardButton("🏠 Home", callback_data="home"))
    rows.append(support_row)
    return InlineKeyboardMarkup(rows)


def partial_delivery_keyboard(order_id: int, support_username: str = "", lang: str = "en") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📥 Re-download received accounts",
                              callback_data=f"reload_order:{order_id}")],
    ]
    if support_username:
        rows.append([InlineKeyboardButton(t(lang, "btn_support"), url=f"https://t.me/{support_username.lstrip('@')}")])
    rows.append([InlineKeyboardButton(t(lang, "btn_home"), callback_data="home")])
    return InlineKeyboardMarkup(rows)


# ── Order search / issue reporting ──────────────────────────────────────────

def order_search_list_keyboard(orders: list, lang: str = "en") -> InlineKeyboardMarkup:
    """One button per matching order: order code • product • purchase time."""
    rows = []
    for o in orders:
        product_name = o.product.name if o.product else str(o.product_id)
        label = f"{o.order_code} • {product_name[:20]} • {o.created_at.strftime('%d/%m/%Y')}"
        rows.append([InlineKeyboardButton(label[:64], callback_data=f"order_pick:{o.id}")])
    rows.append([InlineKeyboardButton(t(lang, "btn_home"), callback_data="home")])
    return InlineKeyboardMarkup(rows)


def order_detail_keyboard(order_id: int, lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_report_issue"), callback_data=f"report_issue:{order_id}")],
        [InlineKeyboardButton(t(lang, "btn_home"), callback_data="home")],
    ])


def admin_issue_keyboard(issue_id: int, lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 View order", callback_data=f"admin_issue_view:{issue_id}")],
        [InlineKeyboardButton("💬 Reply to customer", callback_data=f"admin_issue_reply:{issue_id}")],
        [InlineKeyboardButton("💰 Refund to wallet", callback_data=f"admin_issue_refund:{issue_id}")],
        [InlineKeyboardButton("❌ Reject", callback_data=f"admin_issue_reject:{issue_id}")],
        [InlineKeyboardButton("✅ Mark as resolved", callback_data=f"admin_issue_resolve:{issue_id}")],
    ])


# ── Legacy compat ──────────────────────────────────────────────────────────────
def confirm_order_keyboard(product_id: int, quantity: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_order:{product_id}:{quantity}"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_order"),
        ]
    ])
