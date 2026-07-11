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
        label = f"{emoji} {p.name} - {p.sale_price:,.0f}đ"
        buttons.append([InlineKeyboardButton(label, callback_data=f"product:{p.id}")])
    buttons.append([InlineKeyboardButton("❌ Đóng", callback_data="close")])
    return InlineKeyboardMarkup(buttons)


def product_detail_keyboard(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Mua ngay", callback_data=f"buy:{product_id}")],
        [InlineKeyboardButton("◀️ Quay lại", callback_data="back_products")],
    ])


def confirm_order_keyboard(product_id: int, quantity: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Xác nhận", callback_data=f"confirm_order:{product_id}:{quantity}"),
            InlineKeyboardButton("❌ Huỷ", callback_data="cancel_order"),
        ]
    ])
