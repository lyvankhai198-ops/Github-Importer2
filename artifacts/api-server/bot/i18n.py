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
        "menu_persistent": "☰ Menu",
        "menu_back": "⬅️ Quay lại",
        # ── Language selection ────────────────────────────────────────────────
        "choose_lang": "🌐 Chọn ngôn ngữ / Choose language",
        "lang_vi": "🇻🇳 Tiếng Việt",
        "lang_en": "🇬🇧 English",
        "lang_changed": "✅ Đã chuyển sang Tiếng Việt.",
        "cancelled_returned_home": "❌ Đã hủy thao tác. Bạn đang ở trang chủ.",
        "btn_cancel_flow": "❌ Hủy bỏ",
        # ── Product list ──────────────────────────────────────────────────────
        "product_list_title": "🛍 <b>Danh sách sản phẩm:</b>",
        "product_list_empty": "Hiện không có sản phẩm nào.",
        "products_syncing": "⏳ Đang cập nhật sản phẩm...",
        "products_sync_partial_warning": "⚠️ Một số nguồn chưa đồng bộ được, danh sách có thể chưa mới nhất.",
        "product_list_refreshed": "✅ Đã cập nhật danh sách sản phẩm.",
        "product_list_out_of_stock": "Hết hàng",
        "product_list_accept_order": "Nhận đặt hàng",
        "btn_close": "❌ Đóng",
        "btn_refresh": "🔄 Làm mới",
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
        "product_description": "📝 Mô tả:\n<pre>{desc}</pre>",
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
        "btn_usdt_erc20": "🔵 USDT ERC20",
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
        # ── Binance Pay (verified via Binance API Management Pay History) ──────
        "binance_manual_title": "🟡 <b>BINANCE PAY</b>",
        "binance_pay_id": "Binance ID: <code>{pay_id}</code>",
        "binance_amount": "Số tiền: <b>{amount} USDT</b>",
        "binance_order_code": "Mã đơn: <code>{code}</code>",
        "binance_instruction": "Sau khi chuyển đúng số tiền trên đến Binance ID trên, hãy gửi mã Transaction ID của giao dịch để hệ thống tự động xác minh.",
        "binance_waiting_manual": "⏳ Hệ thống chưa thể tự xác minh giao dịch này. Đơn hàng của bạn đang chờ admin kiểm tra thủ công.\n\nMã đơn: <code>{code}</code>",
        "txid_fail_wrong_receiver": "❌ Giao dịch không chuyển đến đúng Binance ID nhận.\nVui lòng kiểm tra lại và gửi TXID khác.",
        "txid_fail_wrong_currency": "❌ Giao dịch không đúng loại tiền USDT quy định.\nVui lòng kiểm tra lại và gửi TXID khác.",
        "txid_fail_time_window": "❌ Giao dịch nằm ngoài khoảng thời gian hợp lệ của đơn hàng.\nVui lòng kiểm tra lại và gửi TXID khác.",
        "txid_fail_permission_denied": "⏳ Hệ thống chưa thể tự xác minh giao dịch. Đơn hàng đã được chuyển sang chờ admin kiểm tra thủ công.",
        "txid_fail_unavailable": "⚠️ Không thể kết nối tới Binance lúc này. Vui lòng thử lại sau ít phút — QR/thông tin thanh toán vẫn còn hiệu lực.",
        "txid_fail_empty": "❌ Vui lòng gửi mã Transaction ID sau khi đã chuyển khoản.",
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
        # ── USDT ERC20 ────────────────────────────────────────────────────────
        "usdt_erc20_title": "🔵 <b>USDT ERC20</b>",
        "usdt_erc20_network": "Network: Ethereum (ERC20)",
        "usdt_erc20_token": "Token: USDT",
        "usdt_erc20_address": "Address:\n<code>{address}</code>",
        "usdt_erc20_amount": "Amount:\n<code>{amount} USDT</code>",
        "usdt_erc20_warning": "⚠️ Chỉ gửi USDT qua mạng ERC20 (Ethereum).\nGửi sai mạng có thể mất tài sản.",
        "usdt_erc20_order": "Mã đơn: <code>{code}</code>",
        # ── Crypto: copy buttons / manual TXID verification ───────────────────
        "btn_copy_address": "📋 Copy địa chỉ",
        "btn_copy_amount": "📋 Copy số tiền",
        "btn_copy_payid": "📋 Copy Pay ID",
        "btn_verify_txid": "🔎 Tôi đã thanh toán — Xác minh TXID",
        "copy_address_alert": "Địa chỉ ví:\n{value}",
        "copy_amount_alert": "Số tiền cần chuyển:\n{value}",
        "copy_payid_alert": "Pay ID:\n{value}",
        "waiting_txid_prompt": "🔎 Vui lòng dán (paste) mã Transaction ID (TXID) của giao dịch bạn vừa chuyển.",
        "txid_checking": "⏳ Đang xác minh giao dịch trên blockchain...",
        "txid_ok_confirmed": "✅ Xác minh thành công! Đang lấy hàng tự động...",
        "txid_fail_not_found": "❌ Không tìm thấy giao dịch với TXID này trên blockchain.\nVui lòng kiểm tra lại và gửi TXID khác.",
        "txid_fail_wrong_wallet": "❌ Giao dịch không chuyển đến đúng địa chỉ ví nhận.\nVui lòng kiểm tra lại và gửi TXID khác.",
        "txid_fail_wrong_token": "❌ Giao dịch không đúng loại token/contract USDT quy định.\nVui lòng kiểm tra lại và gửi TXID khác.",
        "txid_fail_amount_mismatch": "❌ Số tiền giao dịch không khớp với số tiền cần thanh toán.\nVui lòng kiểm tra lại và gửi TXID khác.",
        "txid_fail_txid_reused": "❌ Mã TXID này đã được dùng để thanh toán cho một đơn khác.\nVui lòng gửi TXID của giao dịch đúng cho đơn này.",
        "txid_fail_order_not_pending": "❌ Đơn hàng này không còn ở trạng thái chờ thanh toán.",
        "txid_fail_already_paid": "✅ Đơn hàng này đã được xác nhận thanh toán rồi.",
        "txid_fail_unsupported_network": "❌ Phương thức thanh toán của đơn này không hỗ trợ xác minh TXID thủ công.",
        "txid_fail_config_missing": "❌ Hệ thống chưa cấu hình đầy đủ cho mạng thanh toán này. Vui lòng liên hệ hỗ trợ.",
        "txid_fail_insufficient_confirmations": "⏳ Giao dịch hợp lệ nhưng đang chờ thêm xác nhận mạng ({confirmations}/{required}).\nHệ thống sẽ tự động xác nhận khi đủ — bạn không cần gửi lại TXID.",
        "txid_fail_generic": "❌ Không thể xác minh giao dịch. Vui lòng kiểm tra lại TXID và thử lại.",
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
        # ── /menu account info ────────────────────────────────────────────────
        "menu_account_info": (
            "👤 <b>THÔNG TIN TÀI KHOẢN</b>\n\n"
            "🆔 Telegram ID: <code>{tg_id}</code>\n"
            "👤 Username: {username}\n"
            "🌐 Ngôn ngữ: {language}\n"
            "📦 Tổng đơn: {total_orders}\n"
            "✅ Trạng thái: {status}"
        ),
        "menu_btn_products":  "🛍 Sản phẩm",
        "menu_btn_orders":    "📦 Đơn hàng",
        "menu_btn_language":  "🌐 Ngôn ngữ",
        "menu_btn_support":   "💬 Hỗ trợ",
        # ── /myid ─────────────────────────────────────────────────────────────
        "myid_response": "🆔 Telegram ID của bạn: <code>{tg_id}</code>",
        # ── out-of-stock popup (query.answer show_alert) ──────────────────────
        "oos_popup": "⚠️ Sản phẩm tạm hết hàng. Vui lòng liên hệ admin.\n\n✈️ Telegram: @{support}",
        "oos_popup_no_support": "⚠️ Sản phẩm tạm hết hàng. Vui lòng quay lại sau.",
        "btn_notify_restock": "🔔 Báo khi có hàng",
        "notify_restock_subscribed": "🔔 Đã đăng ký! Bạn sẽ được thông báo khi sản phẩm có hàng trở lại.",
        "notify_restock_already": "🔔 Bạn đã đăng ký thông báo cho sản phẩm này rồi.",
        # ── user status labels ────────────────────────────────────────────────
        "user_status_active": "Hoạt động",
        "user_status_banned": "Bị khóa",
    },

    "en": {
        # ── Menu ──────────────────────────────────────────────────────────────
        "menu_products": "🛍 Products",
        "menu_orders": "📦 Orders",
        "menu_language": "🌐 Language",
        "menu_support": "💬 Support",
        "menu_admin": "🌐 Admin panel",
        "menu_persistent": "☰ Menu",
        "menu_back": "⬅️ Back",
        # ── Language selection ────────────────────────────────────────────────
        "choose_lang": "🌐 Chọn ngôn ngữ / Choose language",
        "lang_vi": "🇻🇳 Tiếng Việt",
        "lang_en": "🇬🇧 English",
        "lang_changed": "✅ Switched to English.",
        "cancelled_returned_home": "❌ Cancelled. You're back at the home menu.",
        "btn_cancel_flow": "❌ Cancel",
        # ── Product list ──────────────────────────────────────────────────────
        "product_list_title": "🛍 <b>Product list:</b>",
        "product_list_empty": "No products available.",
        "products_syncing": "⏳ Updating products...",
        "products_sync_partial_warning": "⚠️ Some sources could not be refreshed — the list may not be fully up to date.",
        "product_list_refreshed": "✅ Product list updated.",
        "product_list_out_of_stock": "Out of stock",
        "product_list_accept_order": "Pre-order",
        "btn_close": "❌ Close",
        "btn_refresh": "🔄 Refresh",
        # ── Product detail ────────────────────────────────────────────────────
        "product_in_stock": "🟢 In stock ({count})",
        "product_low_stock": "🟡 Low stock ({count})",
        "product_out_of_stock": "🔴 Out of stock",
        "product_unavailable": "⚠️ Source unavailable",
        "product_price": "💰 Price: <b>{price} USDT/account</b>",
        "product_stock_label": "📊 Source stock: {stock}",
        "product_min_qty": "🛒 Minimum: {qty}",
        "product_duration": "⌛ Duration: {val}",
        "product_warranty": "🛡 Warranty: {val}",
        "product_description": "📝 Description:\n<pre>{desc}</pre>",
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
        "choose_payment_title": "💳 <b>CHOOSE PAYMENT METHOD</b>\n\nOrder: <code>{order_code}</code>\nProduct: {product}\nQty: {qty}\nTotal: <b>{total} USDT</b>",
        "btn_bank_transfer": "🏦 Bank transfer",
        "btn_binance_pay": "🟡 Binance Pay",
        "btn_usdt_bep20": "🟨 USDT BEP20",
        "btn_usdt_trc20": "🔴 USDT TRC20",
        "btn_usdt_erc20": "🔵 USDT ERC20",
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
        # ── Binance Pay (verified via Binance API Management Pay History) ──────
        "binance_manual_title": "🟡 <b>BINANCE PAY</b>",
        "binance_pay_id": "Binance ID: <code>{pay_id}</code>",
        "binance_amount": "Amount: <b>{amount} USDT</b>",
        "binance_order_code": "Order code: <code>{code}</code>",
        "binance_instruction": "After sending the exact amount above to the Binance ID shown, send the transaction's Transaction ID so the system can verify it automatically.",
        "binance_waiting_manual": "⏳ We couldn't auto-verify this transaction. Your order is now waiting for manual admin review.\n\nOrder: <code>{code}</code>",
        "txid_fail_wrong_receiver": "❌ The transaction did not go to the correct receiving Binance ID.\nPlease double-check and send a different TXID.",
        "txid_fail_wrong_currency": "❌ The transaction is not the correct USDT currency.\nPlease double-check and send a different TXID.",
        "txid_fail_time_window": "❌ The transaction falls outside this order's valid time window.\nPlease double-check and send a different TXID.",
        "txid_fail_permission_denied": "⏳ We couldn't auto-verify this transaction right now. Your order has been moved to manual admin review.",
        "txid_fail_unavailable": "⚠️ Couldn't reach Binance right now. Please try again in a few minutes — your payment details are still valid.",
        "txid_fail_empty": "❌ Please send the Transaction ID after making the transfer.",
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
        # ── USDT ERC20 ────────────────────────────────────────────────────────
        "usdt_erc20_title": "🔵 <b>USDT ERC20</b>",
        "usdt_erc20_network": "Network: Ethereum (ERC20)",
        "usdt_erc20_token": "Token: USDT",
        "usdt_erc20_address": "Address:\n<code>{address}</code>",
        "usdt_erc20_amount": "Amount:\n<code>{amount} USDT</code>",
        "usdt_erc20_warning": "⚠️ Only send USDT via ERC20 (Ethereum) network.\nSending on the wrong network may result in loss of funds.",
        "usdt_erc20_order": "Order: <code>{code}</code>",
        # ── Crypto: copy buttons / manual TXID verification ───────────────────
        "btn_copy_address": "📋 Copy address",
        "btn_copy_amount": "📋 Copy amount",
        "btn_copy_payid": "📋 Copy Pay ID",
        "btn_verify_txid": "🔎 I've paid — Verify TXID",
        "copy_address_alert": "Wallet address:\n{value}",
        "copy_amount_alert": "Amount to send:\n{value}",
        "copy_payid_alert": "Pay ID:\n{value}",
        "waiting_txid_prompt": "🔎 Please paste the Transaction ID (TXID) of the transfer you just made.",
        "txid_checking": "⏳ Verifying transaction on-chain...",
        "txid_ok_confirmed": "✅ Verified! Fetching your items automatically...",
        "txid_fail_not_found": "❌ No transaction found with this TXID on-chain.\nPlease double-check and send a different TXID.",
        "txid_fail_wrong_wallet": "❌ The transaction did not go to the correct receiving wallet.\nPlease double-check and send a different TXID.",
        "txid_fail_wrong_token": "❌ The transaction is not the correct USDT token/contract.\nPlease double-check and send a different TXID.",
        "txid_fail_amount_mismatch": "❌ The transaction amount does not match the amount due.\nPlease double-check and send a different TXID.",
        "txid_fail_txid_reused": "❌ This TXID has already been used to pay for a different order.\nPlease send the TXID for this order's payment.",
        "txid_fail_order_not_pending": "❌ This order is no longer awaiting payment.",
        "txid_fail_already_paid": "✅ This order has already been confirmed as paid.",
        "txid_fail_unsupported_network": "❌ This order's payment method doesn't support manual TXID verification.",
        "txid_fail_config_missing": "❌ This payment network isn't fully configured yet. Please contact support.",
        "txid_fail_insufficient_confirmations": "⏳ Transaction found and valid, but still waiting for more network confirmations ({confirmations}/{required}).\nThe system will confirm automatically once ready — no need to resend the TXID.",
        "txid_fail_generic": "❌ Could not verify the transaction. Please check the TXID and try again.",
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
        # ── /menu account info ────────────────────────────────────────────────
        "menu_account_info": (
            "👤 <b>ACCOUNT INFORMATION</b>\n\n"
            "🆔 Telegram ID: <code>{tg_id}</code>\n"
            "👤 Username: {username}\n"
            "🌐 Language: {language}\n"
            "📦 Total orders: {total_orders}\n"
            "✅ Status: {status}"
        ),
        "menu_btn_products":  "🛍 Products",
        "menu_btn_orders":    "📦 Orders",
        "menu_btn_language":  "🌐 Language",
        "menu_btn_support":   "💬 Support",
        # ── /myid ─────────────────────────────────────────────────────────────
        "myid_response": "🆔 Your Telegram ID: <code>{tg_id}</code>",
        # ── out-of-stock popup (query.answer show_alert) ──────────────────────
        "oos_popup": "⚠️ This product is temporarily out of stock.\nPlease contact admin.\n\n✈️ Telegram: @{support}",
        "oos_popup_no_support": "⚠️ This product is temporarily out of stock. Please check back later.",
        "btn_notify_restock": "🔔 Notify me when back in stock",
        "notify_restock_subscribed": "🔔 Subscribed! You'll be notified when this product is back in stock.",
        "notify_restock_already": "🔔 You're already subscribed for this product.",
        # ── user status labels ────────────────────────────────────────────────
        "user_status_active": "Active",
        "user_status_banned": "Banned",
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
