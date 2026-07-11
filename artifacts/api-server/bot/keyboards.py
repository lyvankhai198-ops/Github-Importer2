from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton


def main_menu_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    buttons = [
        ["🛍 Sản phẩm", "📦 Đơn hàng"],
        ["💬 Hỗ trợ"],
    ]
    if is_admin:
        buttons.append(["🌐 Mở trang quản trị"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def product_list_keyboard(products: list) -> InlineKeyboardMarkup:
    """Danh sách: chỉ hiện emoji tình trạng + tên sản phẩm, không hiện giá."""
    buttons = []
    for item in products:
        p = item["product"]
        stock = item.get("stock", 0)
        if stock > 10:
            emoji = "🟢"
        elif stock > 0:
            emoji = "🟡"
        else:
            emoji = "🔴"
        label = f"{emoji} {p.name}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"product:{p.id}")])
    buttons.append([InlineKeyboardButton("❌ Đóng", callback_data="close")])
    return InlineKeyboardMarkup(buttons)


def product_detail_keyboard(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Mua ngay", callback_data=f"buy:{product_id}")],
        [
            InlineKeyboardButton("◀️ Quay lại", callback_data="back_products"),
            InlineKeyboardButton("🏠 Trang chủ", callback_data="home"),
        ],
    ])


def confirm_order_keyboard(product_id: int, quantity: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Xác nhận mua", callback_data=f"confirm_order:{product_id}:{quantity}"),
            InlineKeyboardButton("❌ Hủy", callback_data="cancel_order"),
        ]
    ])


def payment_keyboard(order_id: int, support_username: str = "") -> InlineKeyboardMarkup:
    """Bàn phím hiện cùng QR thanh toán."""
    rows = [
        [InlineKeyboardButton("🔄 Kiểm tra thanh toán", callback_data=f"check_payment:{order_id}")],
        [InlineKeyboardButton("❌ Hủy đơn", callback_data=f"cancel_pending:{order_id}")],
    ]
    support_row = []
    if support_username:
        support_row.append(
            InlineKeyboardButton("💬 Hỗ trợ", url=f"https://t.me/{support_username.lstrip('@')}")
        )
    support_row.append(InlineKeyboardButton("🏠 Trang chủ", callback_data="home"))
    rows.append(support_row)
    return InlineKeyboardMarkup(rows)


def post_delivery_keyboard(order_id: int, support_username: str = "") -> InlineKeyboardMarkup:
    """Bàn phím sau khi giao hàng thành công."""
    rows = [
        [InlineKeyboardButton("📦 Xem đơn hàng", callback_data=f"view_order:{order_id}")],
        [InlineKeyboardButton("📥 Tải lại tài khoản", callback_data=f"reload_order:{order_id}")],
    ]
    support_row = []
    if support_username:
        support_row.append(
            InlineKeyboardButton("💬 Hỗ trợ", url=f"https://t.me/{support_username.lstrip('@')}")
        )
    support_row.append(InlineKeyboardButton("🏠 Trang chủ", callback_data="home"))
    rows.append(support_row)
    return InlineKeyboardMarkup(rows)


def partial_delivery_keyboard(order_id: int, support_username: str = "") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📥 Tải lại tài khoản đã nhận", callback_data=f"reload_order:{order_id}")],
    ]
    if support_username:
        rows.append([
            InlineKeyboardButton("💬 Liên hệ hỗ trợ", url=f"https://t.me/{support_username.lstrip('@')}")
        ])
    rows.append([InlineKeyboardButton("🏠 Trang chủ", callback_data="home")])
    return InlineKeyboardMarkup(rows)
