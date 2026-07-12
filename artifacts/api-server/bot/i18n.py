"""
Internationalization (i18n) for the Telegram bot.
Usage: t(lang, "key") or t(lang, "key", var=value)
Language codes: "vi" (default), "en"
"""

TRANSLATIONS: dict[str, dict[str, str]] = {
    "vi": {
        # ── Menu ──────────────────────────────────────────────────────────────
        "menu_products": "🛍 Sản phẩm",
        "menu_orders": "📦 Đơn hàng",
        "menu_language": "🌐 Ngôn ngữ",
        "menu_support": "💬 Hỗ trợ",
        "menu_admin": "🌐 Mở trang quản trị",
        # ── Language selection ────────────────────────────────────────────────
        "choose_lang": "🌐 Chọn ngôn ngữ / Choose language",
        "lang_vi": "🇻🇳 Tiếng Việt",
        "lang_en": "🇬🇧 English",
        "lang_changed": "✅ Đã chuyển sang Tiếng Việt.",
        # ── Product list ──────────────────────────────────────────────────────
        "product_list_title": "🛍 <b>Danh sách sản phẩm:</b>",
        "product_list_empty": "Hiện không có sản phẩm nào.",
        "btn_close": "❌ Đóng",
        # ── Product detail ────────────────────────────────────────────────────
        "product_in_stock": "🟢 Còn hàng ({count})",
        "product_low_stock": "🟡 Còn ít ({count})",
        "product_out_of_stock": "🔴 Hết hàng",
        "product_unavailable": "⚠️ Nguồn tạm lỗi",
        "product_price": "💰 Giá bán: <b>{price}đ/tài khoản</b>",
        "product_stock_label": "📊 Tồn kho nguồn: {stock}",
        "product_min_qty": "🛒 Tối thiểu: {qty}",
        "product_duration": "⌛ Thời hạn: {val}",
        "product_warranty": "🛡 Bảo hành: {val}",
        "product_description": "📝 Mô tả:\n{desc}",
        "btn_buy_now": "🛒 Mua ngay",
        "btn_back": "◀️ Quay lại",
        "btn_home": "🏠 Trang chủ",
        "btn_check_again": "🔄 Kiểm tra lại",
        # ── Out of stock ──────────────────────────────────────────────────────
        "out_of_stock_title": "🔴 SẢN PHẨM ĐÃ HẾT HÀNG",
        "out_of_stock_body": "Sản phẩm này hiện không còn hàng tại nguồn.\nVui lòng quay lại sau hoặc chọn sản phẩm khác.",
        # ── Quantity prompt ───────────────────────────────────────────────────
        "enter_quantity": "🔢 Nhập số lượng bạn muốn mua:",
        "qty_invalid": "❌ Vui lòng nhập số lượng hợp lệ (số nguyên dương).",
        "qty_below_min": "❌ Số lượng tối thiểu là <b>{min}</b>.",
        "qty_exceeds_stock": "⚠️ Số lượng còn lại không đủ.\n\nKho hiện có: <b>{stock}</b>\nBạn yêu cầu: <b>{qty}</b>",
        "product_not_found": "Sản phẩm không tồn tại.",
        "product_out_of_stock_recheck": "🔴 Sản phẩm đã hết hàng. Vui lòng chọn sản phẩm khác.",
        # ── Payment method selection ──────────────────────────────────────────
        "choose_payment_title": "💳 <b>CHỌN PHƯƠNG THỨC THANH TOÁN</b>\n\nMã đơn: <code>{order_code}</code>\nSản phẩm: {product}\nSố lượng: {qty}\nTổng tiền: <b>{total}đ</b>",
        "btn_bank_transfer": "🏦 Chuyển khoản ngân hàng",
        "btn_binance_pay": "🟡 Binance Pay",
        "btn_usdt_bep20": "🟨 USDT BEP20",
        "btn_usdt_trc20": "🔴 USDT TRC20",
        "btn_cancel_order": "❌ Hủy đơn",
        # ── SePay / Bank transfer ─────────────────────────────────────────────
        "sepay_payment_title": "💳 <b>THANH TOÁN ĐƠN HÀNG</b>",
        "sepay_order_code": "Mã đơn: <code>{code}</code>",
        "sepay_product": "Sản phẩm: {name}",
        "sepay_qty": "Số lượng: {qty}",
        "sepay_amount": "Số tiền: <b>{amount}đ</b>",
        "sepay_bank": "🏦 Ngân hàng: <b>{bank}</b>",
        "sepay_account_number": "Số tài khoản: <code>{acc}</code>",
        "sepay_account_name": "Chủ TK: {name}",
        "sepay_content": "Nội dung CK: <code>{code}</code>",
        "sepay_expiry": "⏰ Hết hạn: {time} ({min} phút)",
        "btn_check_payment": "🔄 Kiểm tra thanh toán",
        "btn_regen_qr": "🖼 Tạo lại QR",
        "btn_cancel_pending": "❌ Hủy đơn",
        "btn_support": "💬 Hỗ trợ",
        # ── Binance Pay Manual ────────────────────────────────────────────────
        "binance_manual_title": "🟡 <b>BINANCE PAY</b>",
        "binance_pay_id": "Pay ID: <code>{pay_id}</code>",
        "binance_recipient": "Recipient: {name}",
        "binance_amount": "Amount: <b>{amount} USDT</b>",
        "binance_order_code": "Order code: <code>{code}</code>",
        "binance_instruction": "Sau khi thanh toán, gửi Transaction ID hoặc ảnh biên nhận.",
        "btn_open_binance": "🟡 Open Binance Pay",
        "btn_sent_proof": "📤 Đã gửi biên nhận",
        "binance_waiting": "⏳ Đang chờ admin xác nhận thanh toán...\n\nMã đơn: <code>{code}</code>",
        # ── Binance Pay Merchant ──────────────────────────────────────────────
        "btn_open_binance_merchant": "🟡 Open Binance Pay",
        "btn_check_binance": "🔄 Check payment",
        # ── USDT BEP20 ────────────────────────────────────────────────────────
        "usdt_bep20_title": "🟨 <b>USDT BEP20</b>",
        "usdt_bep20_network": "Network: BNB Smart Chain (BEP20)",
        "usdt_bep20_token": "Token: USDT",
        "usdt_bep20_address": "Address:\n<code>{address}</code>",
        "usdt_bep20_amount": "Amount:\n<code>{amount} USDT</code>",
        "usdt_bep20_warning": "⚠️ Chỉ gửi USDT qua mạng BEP20.\nGửi sai mạng có thể mất tài sản.",
        "usdt_bep20_order": "Mã đơn: <code>{code}</code>",
        # ── USDT TRC20 ────────────────────────────────────────────────────────
        "usdt_trc20_title": "🔴 <b>USDT TRC20</b>",
        "usdt_trc20_network": "Network: TRON (TRC20)",
        "usdt_trc20_token": "Token: USDT",
        "usdt_trc20_address": "Address:\n<code>{address}</code>",
        "usdt_trc20_amount": "Amount:\n<code>{amount} USDT</code>",
        "usdt_trc20_warning": "⚠️ Chỉ gửi USDT qua mạng TRC20.\nGửi sai mạng có thể mất tài sản.",
        "usdt_trc20_order": "Mã đơn: <code>{code}</code>",
        # ── Crypto payment status ─────────────────────────────────────────────
        "crypto_detected": "⏳ Đã phát hiện giao dịch.\nĐang chờ {current}/{required} xác nhận mạng.",
        "crypto_confirmed": "✅ Thanh toán thành công.\nĐang lấy hàng tự động...",
        # ── Payment status messages ───────────────────────────────────────────
        "payment_not_received": "⏳ Chưa nhận được thanh toán.",
        "payment_partial": "⚠️ Đã nhận {paid}đ.\nCòn thiếu {remaining}đ.",
        "payment_done_processing": "✅ Thanh toán thành công.\nĐơn đang được lấy hàng tự động.",
        "payment_expired_msg": "⏰ Đơn hàng đã hết hạn thanh toán.",
        "payment_confirmed_interim": "✅ Đã nhận thanh toán.\nĐang lấy hàng tự động từ nguồn...",
        # ── Cancel / cancel pending ───────────────────────────────────────────
        "order_cancelled": "❌ Đã hủy đặt hàng.",
        "order_cancel_paid": "Đơn đã thanh toán — không thể hủy. Liên hệ hỗ trợ.",
        "order_cancel_success": "❌ Đã hủy đơn hàng.",
        "order_not_found": "Không tìm thấy đơn hàng.",
        # ── Delivery / orders ─────────────────────────────────────────────────
        "orders_title": "📦 <b>Đơn hàng gần đây:</b>\n",
        "orders_empty": "Bạn chưa có đơn hàng nào.",
        "support_contact": "💬 Liên hệ hỗ trợ: @{username}",
        "support_contact_admin": "💬 Vui lòng liên hệ quản trị viên để được hỗ trợ.",
        # ── Paid waiting stock ────────────────────────────────────────────────
        "paid_waiting_stock_user": (
            "✅ Đã nhận thanh toán.\n\n"
            "⚠️ Sản phẩm vừa hết hàng tại nguồn.\n"
            "Đơn đã chuyển sang chờ admin xử lý."
        ),
        # ── Error ─────────────────────────────────────────────────────────────
        "payment_not_configured": "❌ Hệ thống thanh toán chưa được cấu hình.",
        "payment_method_disabled": "❌ Phương thức thanh toán này hiện không khả dụng.",
        "order_error": "❌ Lỗi đặt hàng. Vui lòng thử lại.",
        "processing_order": "⏳ Đang xử lý đơn hàng...",
    },

    "en": {
        # ── Menu ──────────────────────────────────────────────────────────────
        "menu_products": "🛍 Products",
        "menu_orders": "📦 Orders",
        "menu_language": "🌐 Language",
        "menu_support": "💬 Support",
        "menu_admin": "🌐 Admin panel",
        # ── Language selection ────────────────────────────────────────────────
        "choose_lang": "🌐 Chọn ngôn ngữ / Choose language",
        "lang_vi": "🇻🇳 Tiếng Việt",
        "lang_en": "🇬🇧 English",
        "lang_changed": "✅ Switched to English.",
        # ── Product list ──────────────────────────────────────────────────────
        "product_list_title": "🛍 <b>Product list:</b>",
        "product_list_empty": "No products available.",
        "btn_close": "❌ Close",
        # ── Product detail ────────────────────────────────────────────────────
        "product_in_stock": "🟢 In stock ({count})",
        "product_low_stock": "🟡 Low stock ({count})",
        "product_out_of_stock": "🔴 Out of stock",
        "product_unavailable": "⚠️ Source unavailable",
        "product_price": "💰 Price: <b>{price} VND/account</b>",
        "product_stock_label": "📊 Source stock: {stock}",
        "product_min_qty": "🛒 Minimum: {qty}",
        "product_duration": "⌛ Duration: {val}",
        "product_warranty": "🛡 Warranty: {val}",
        "product_description": "📝 Description:\n{desc}",
        "btn_buy_now": "🛒 Buy now",
        "btn_back": "◀️ Back",
        "btn_home": "🏠 Home",
        "btn_check_again": "🔄 Check again",
        # ── Out of stock ──────────────────────────────────────────────────────
        "out_of_stock_title": "🔴 OUT OF STOCK",
        "out_of_stock_body": "This product is currently out of stock at the source.\nPlease come back later or choose another product.",
        # ── Quantity prompt ───────────────────────────────────────────────────
        "enter_quantity": "🔢 Enter the quantity you want to buy:",
        "qty_invalid": "❌ Please enter a valid quantity (positive integer).",
        "qty_below_min": "❌ Minimum quantity is <b>{min}</b>.",
        "qty_exceeds_stock": "⚠️ Not enough stock.\n\nAvailable: <b>{stock}</b>\nYou requested: <b>{qty}</b>",
        "product_not_found": "Product not found.",
        "product_out_of_stock_recheck": "🔴 Product is out of stock. Please choose another.",
        # ── Payment method selection ──────────────────────────────────────────
        "choose_payment_title": "💳 <b>CHOOSE PAYMENT METHOD</b>\n\nOrder: <code>{order_code}</code>\nProduct: {product}\nQty: {qty}\nTotal: <b>{total} VND</b>",
        "btn_bank_transfer": "🏦 Bank transfer",
        "btn_binance_pay": "🟡 Binance Pay",
        "btn_usdt_bep20": "🟨 USDT BEP20",
        "btn_usdt_trc20": "🔴 USDT TRC20",
        "btn_cancel_order": "❌ Cancel order",
        # ── SePay / Bank transfer ─────────────────────────────────────────────
        "sepay_payment_title": "💳 <b>PAYMENT</b>",
        "sepay_order_code": "Order: <code>{code}</code>",
        "sepay_product": "Product: {name}",
        "sepay_qty": "Quantity: {qty}",
        "sepay_amount": "Amount: <b>{amount} VND</b>",
        "sepay_bank": "🏦 Bank: <b>{bank}</b>",
        "sepay_account_number": "Account: <code>{acc}</code>",
        "sepay_account_name": "Account holder: {name}",
        "sepay_content": "Transfer note: <code>{code}</code>",
        "sepay_expiry": "⏰ Expires: {time} ({min} min)",
        "btn_check_payment": "🔄 Check payment",
        "btn_regen_qr": "🖼 Regenerate QR",
        "btn_cancel_pending": "❌ Cancel order",
        "btn_support": "💬 Support",
        # ── Binance Pay Manual ────────────────────────────────────────────────
        "binance_manual_title": "🟡 <b>BINANCE PAY</b>",
        "binance_pay_id": "Pay ID: <code>{pay_id}</code>",
        "binance_recipient": "Recipient: {name}",
        "binance_amount": "Amount: <b>{amount} USDT</b>",
        "binance_order_code": "Order code: <code>{code}</code>",
        "binance_instruction": "After payment, send your Transaction ID or a screenshot of the receipt.",
        "btn_open_binance": "🟡 Open Binance Pay",
        "btn_sent_proof": "📤 I've sent proof",
        "binance_waiting": "⏳ Waiting for admin payment confirmation...\n\nOrder: <code>{code}</code>",
        # ── Binance Pay Merchant ──────────────────────────────────────────────
        "btn_open_binance_merchant": "🟡 Open Binance Pay",
        "btn_check_binance": "🔄 Check payment",
        # ── USDT BEP20 ────────────────────────────────────────────────────────
        "usdt_bep20_title": "🟨 <b>USDT BEP20</b>",
        "usdt_bep20_network": "Network: BNB Smart Chain (BEP20)",
        "usdt_bep20_token": "Token: USDT",
        "usdt_bep20_address": "Address:\n<code>{address}</code>",
        "usdt_bep20_amount": "Amount:\n<code>{amount} USDT</code>",
        "usdt_bep20_warning": "⚠️ Only send USDT via BEP20 network.\nSending on the wrong network may result in loss of funds.",
        "usdt_bep20_order": "Order: <code>{code}</code>",
        # ── USDT TRC20 ────────────────────────────────────────────────────────
        "usdt_trc20_title": "🔴 <b>USDT TRC20</b>",
        "usdt_trc20_network": "Network: TRON (TRC20)",
        "usdt_trc20_token": "Token: USDT",
        "usdt_trc20_address": "Address:\n<code>{address}</code>",
        "usdt_trc20_amount": "Amount:\n<code>{amount} USDT</code>",
        "usdt_trc20_warning": "⚠️ Only send USDT via TRC20 network.\nSending on the wrong network may result in loss of funds.",
        "usdt_trc20_order": "Order: <code>{code}</code>",
        # ── Crypto payment status ─────────────────────────────────────────────
        "crypto_detected": "⏳ Transaction detected.\nWaiting for {current}/{required} network confirmations.",
        "crypto_confirmed": "✅ Payment confirmed.\nFetching your items automatically...",
        # ── Payment status messages ───────────────────────────────────────────
        "payment_not_received": "⏳ Payment not received yet.",
        "payment_partial": "⚠️ Received {paid} VND.\nStill missing {remaining} VND.",
        "payment_done_processing": "✅ Payment successful.\nOrder is being processed automatically.",
        "payment_expired_msg": "⏰ Payment window has expired.",
        "payment_confirmed_interim": "✅ Payment received.\nFetching items automatically from source...",
        # ── Cancel ────────────────────────────────────────────────────────────
        "order_cancelled": "❌ Order cancelled.",
        "order_cancel_paid": "Order already paid — cannot cancel. Contact support.",
        "order_cancel_success": "❌ Order cancelled.",
        "order_not_found": "Order not found.",
        # ── Delivery / orders ─────────────────────────────────────────────────
        "orders_title": "📦 <b>Recent orders:</b>\n",
        "orders_empty": "You have no orders yet.",
        "support_contact": "💬 Contact support: @{username}",
        "support_contact_admin": "💬 Please contact the administrator for support.",
        # ── Paid waiting stock ────────────────────────────────────────────────
        "paid_waiting_stock_user": (
            "✅ Payment received.\n\n"
            "⚠️ Product just went out of stock at the source.\n"
            "Your order has been moved to manual processing."
        ),
        # ── Error ─────────────────────────────────────────────────────────────
        "payment_not_configured": "❌ Payment system is not configured.",
        "payment_method_disabled": "❌ This payment method is currently unavailable.",
        "order_error": "❌ Order error. Please try again.",
        "processing_order": "⏳ Processing order...",
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    """
    Return translated string for the given language and key.
    Falls back to Vietnamese if the key is missing for the requested language.
    Supports format placeholders: t(lang, "key", var=value)
    """
    lang = lang if lang in TRANSLATIONS else "vi"
    text = TRANSLATIONS[lang].get(key) or TRANSLATIONS["vi"].get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text


def get_user_lang(db, telegram_user_id: str) -> str:
    """Fetch language_code for a user from DB. Defaults to 'vi'."""
    from models import User
    user = db.query(User).filter(User.telegram_id == str(telegram_user_id)).first()
    return (user.language_code if user and user.language_code else "vi")
