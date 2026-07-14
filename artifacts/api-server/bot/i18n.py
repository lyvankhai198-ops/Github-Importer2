"""
Internationalization (i18n) for the Telegram bot.
Usage: t(lang, "key") or t(lang, "key", var=value)
Language codes: "vi" (default), "en"
"""

TRANSLATIONS: dict[str, dict[str, str]] = {
    "vi": {
        # ── Menu ──────────────────────────────────────────────────────────────
        "menu_products": "🛍 Sản phẩm",
        "menu_orders": "🔍 Tìm đơn hàng",
        "menu_language": "🌐 Ngôn ngữ",
        "menu_support": "💬 Hỗ trợ",
        "menu_btn_account": "👤 Thông tin",
        "menu_admin": "🌐 Mở trang quản trị",
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
        # ── Order search ──────────────────────────────────────────────────────
        "order_search_prompt": "📧 Nhập email hoặc tài khoản đã mua để tìm đơn hàng:",
        "order_search_not_found": "❌ Không tìm thấy đơn hàng nào khớp với thông tin bạn nhập.",
        "order_search_pick_title": "🔍 <b>Tìm thấy {count} đơn hàng khớp:</b>",
        "order_search_invalid": "⚠️ Vui lòng nhập email hoặc tài khoản hợp lệ.",
        "order_detail_title": "📋 <b>CHI TIẾT ĐƠN HÀNG</b>",
        "order_detail_code": "🆔 Mã đơn: <code>{code}</code>",
        "order_detail_product": "📦 Sản phẩm: {product}",
        "order_detail_buyer": "👤 Người mua: <code>{buyer}</code>",
        "order_detail_seller": "🏪 Người bán: {seller}",
        "order_detail_account": "🔑 Tài khoản đã giao:",
        "order_detail_price": "💰 Giá: {price}",
        "order_detail_purchase_time": "📅 Thời gian mua: {time}",
        "order_detail_warranty": "🛡 Bảo hành: {warranty}",
        "order_detail_days_used": "📆 Đã dùng: {days} ngày",
        "order_detail_days_remaining": "⏳ Còn lại: {days} ngày",
        "order_detail_max_refund": "💵 Hoàn tiền tối đa: {amount}",
        "order_detail_status": "📊 Trạng thái: {status}",
        "order_detail_no_account": "(chưa có tài khoản được giao)",
        "btn_report_issue": "⚠️ Báo lỗi",
        # ── Issue reporting ───────────────────────────────────────────────────
        "issue_report_prompt": "📝 Vui lòng mô tả lỗi bạn gặp phải (có thể gửi kèm ảnh/video/tệp):",
        "issue_report_saved": "✅ Đã gửi báo lỗi tới admin. Vui lòng chờ phản hồi.",
        "issue_report_error": "⚠️ Không thể gửi báo lỗi, vui lòng thử lại.",
        "issue_reply_prompt": "💬 Nhập nội dung trả lời cho khách hàng:",
        "issue_reply_sent": "✅ Đã gửi trả lời tới khách hàng.",
        "issue_reply_received": "💬 <b>Phản hồi từ admin về đơn <code>{code}</code>:</b>\n\n{text}",
        "issue_reject_prompt": "❌ Nhập lý do từ chối:",
        "issue_rejected_admin": "❌ Đã từ chối báo lỗi #{id}.",
        "issue_rejected_user": "❌ <b>Báo lỗi đơn <code>{code}</code> đã bị từ chối.</b>\n\nLý do: {reason}",
        "issue_resolved_admin": "✅ Đã đánh dấu báo lỗi #{id} là đã xử lý.",
        "issue_already_handled": "⚠️ Báo lỗi này đã được xử lý trước đó.",
        "issue_not_found": "⚠️ Không tìm thấy báo lỗi.",
        "refund_success_admin": "✅ Đã hoàn {amount} vào ví của khách cho đơn <code>{code}</code>.",
        "refund_success_user": (
            "✅ <b>HOÀN TIỀN THÀNH CÔNG</b>\n\n"
            "🧾 Mã đơn: <code>{code}</code>\n"
            "💰 Số tiền hoàn: <b>{amount}</b>\n"
            "👛 Số dư mới: <b>{new_balance}</b>"
        ),
        "refund_already_done": "⚠️ Giao dịch này đã được hoàn tiền trước đó.",
        "refund_warranty_expired": "⚠️ Đơn hàng đã hết thời gian bảo hành, không thể hoàn tiền.",
        "refund_not_authorized": "🚫 Bạn không có quyền thực hiện thao tác này.",
        "support_contact": "💬 Liên hệ hỗ trợ: @{username}",
        "support_contact_admin": "💬 Vui lòng liên hệ quản trị viên để được hỗ trợ.",
        # ── Paid waiting stock ────────────────────────────────────────────────
        "paid_waiting_stock_user": (
            "✅ Đã nhận thanh toán.\n\n"
            "⚠️ Sản phẩm vừa hết hàng tại nguồn.\n"
            "Đơn đã chuyển sang chờ admin xử lý."
        ),
        "pending_seller_fulfillment_user": (
            "✅ Đã nhận thanh toán.\n\n"
            "⏳ Sản phẩm này cần người bán xử lý (loại slot).\n"
            "Đơn của bạn đang chờ người bán xác nhận, chúng tôi sẽ thông báo ngay khi có kết quả."
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
        "account_info_full": (
            "👤 <b>THÔNG TIN TÀI KHOẢN</b>\n\n"
            "Tên: {full_name}\n"
            "Username: @{username}\n"
            "Chat ID: <code>{tg_id}</code>\n"
            "Ngôn ngữ: {language}\n\n"
            "👛 Số dư VND: <b>{balance_vnd}đ</b>\n"
            "💵 Số dư USDT: <b>{balance_usdt} USDT</b>\n\n"
            "🔗 Trạng thái API: {api_status}\n"
            "🔑 API Key: <code>{api_key_masked}</code>\n"
            "📅 Ngày tạo API: {api_created_at}\n"
            "📦 Tổng đơn API: {api_order_count}\n"
            "💳 Tổng chi qua API: {api_total_spent}\n\n"
            "📦 Tổng đơn hàng: {total_orders}\n"
            "✅ Đơn hoàn thành: {completed_orders}"
        ),
        # ── Redesigned "Thông tin" (account info) with membership rank ─────────
        "greeting_morning": "buổi sáng",
        "greeting_afternoon": "buổi chiều",
        "greeting_evening": "buổi tối",
        "account_info_v2": (
            "🌅 Chào {time_of_day} {full_name}\n"
            "🆔 ID: <code>{tg_id}</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "👑 <b>Cấp bậc:</b>\n{rank_emoji} {rank_name}\n\n"
            "💰 <b>Số dư:</b>\n{balance} VNĐ\n\n"
            "📉 <b>Tổng chi tiêu:</b>\n{total_spent} VNĐ\n\n"
            "📦 <b>Tổng đơn hàng:</b>\n{total_orders}\n\n"
            "🛍 <b>Tổng tài khoản đã mua:</b>\n{total_accounts}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "{progress_section}"
        ),
        "rank_progress_section": (
            "📈 <b>Tiến độ lên cấp tiếp theo</b>\n\n"
            "{bar} {percent}%\n\n"
            "Còn thiếu:\n{remaining} VNĐ\n\n"
            "để lên:\n\n{next_rank_emoji} {next_rank_name}"
        ),
        "rank_max_section": "🏆 Bạn đã đạt cấp cao nhất.",
        "rank_upgraded": (
            "🎉 <b>CHÚC MỪNG!</b>\n\n"
            "Bạn đã được nâng cấp lên\n\n"
            "{rank_emoji} <b>{rank_name}</b>\n\n"
            "Cảm ơn bạn đã đồng hành cùng AI Center."
        ),
        "btn_account_docs": "📘 Tài liệu API",
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
        # ── invalid command ───────────────────────────────────────────────────
        "invalid_command": (
            "⚠️ Lệnh không hợp lệ. Các lệnh được hỗ trợ:\n\n"
            "/start – Bắt đầu và xem menu\n"
            "/menu – Mở menu chính\n"
            "/products – Xem sản phẩm\n"
            "/orders – Xem đơn hàng\n"
            "/wallet – Ví của tôi\n"
            "/language – Đổi ngôn ngữ\n"
            "/support – Hỗ trợ"
        ),
        # ── Wallet ────────────────────────────────────────────────────────────
        "menu_wallet": "💼 Ví của tôi",
        "menu_btn_wallet": "💼 Ví của tôi",
        "wallet_title": "💼 <b>VÍ CỦA TÔI</b>",
        "wallet_balance_vnd": "💰 Số dư VND: <b>{amount}đ</b>",
        "wallet_balance_usdt": "💰 Số dư USDT: <b>{amount} USDT</b>",
        "btn_wallet_deposit": "➕ Nạp tiền",
        "btn_wallet_history": "📜 Lịch sử giao dịch",
        "wallet_choose_deposit_currency": "💼 Bạn muốn nạp bằng loại tiền nào?",
        "btn_wallet_deposit_vnd": "🏦 VND (Chuyển khoản)",
        "btn_wallet_deposit_usdt": "🟨 USDT (Crypto)",
        "wallet_choose_deposit_method": "💼 Chọn phương thức nạp tiền:",
        "wallet_enter_amount_vnd": "🔢 Nhập số tiền muốn nạp (VND), ví dụ: 100000",
        "wallet_enter_amount_usdt": "🔢 Nhập số tiền muốn nạp (USDT), ví dụ: 10",
        "wallet_amount_invalid": "❌ Vui lòng nhập số tiền hợp lệ (số dương).",
        "wallet_deposit_no_payment_configured": "❌ Phương thức thanh toán này chưa được cấu hình. Vui lòng chọn phương thức khác hoặc liên hệ hỗ trợ.",
        "wallet_deposit_created_vnd": (
            "✅ <b>YÊU CẦU NẠP TIỀN ĐÃ TẠO</b>\n\n"
            "🔑 Mã tham chiếu: <code>{ref}</code>\n\n"
            "🏦 Ngân hàng: <b>{bank}</b>\n"
            "👤 Chủ tài khoản: {acc_name}\n"
            "🔢 Số tài khoản: <code>{acc}</code>\n"
            "💰 Số tiền: <code>{amount}</code>đ\n"
            "📝 Nội dung: <code>{ref}</code>\n\n"
            "⚠️ Vui lòng chuyển khoản đúng số tiền và ghi đúng nội dung ở trên.\n"
            "🤖 Hệ thống sẽ tự động cộng tiền vào ví của bạn ngay khi nhận được chuyển khoản, không cần chờ admin.\n"
            "📷 Hoặc quét mã QR ở trên bằng app ngân hàng bất kỳ để chuyển khoản ngay."
        ),
        "btn_check_deposit": "🔄 Kiểm tra thanh toán",
        "btn_cancel_deposit": "❌ Hủy đơn",
        "wallet_deposit_cancelled_user": "❌ Yêu cầu nạp tiền đã được hủy.",
        "wallet_deposit_cancel_denied": "Không thể hủy — yêu cầu này đã được xử lý hoặc không tồn tại.",
        "wallet_deposit_check_pending": "⏳ Chưa nhận được chuyển khoản cho mã <code>{ref}</code>. Hệ thống sẽ tự động cộng tiền ngay khi nhận được — bạn không cần làm gì thêm.",
        "wallet_deposit_check_credited": "✅ Yêu cầu nạp tiền <code>{ref}</code> đã được cộng tiền vào ví!",
        "wallet_deposit_check_gone": "Yêu cầu nạp tiền này không còn ở trạng thái chờ thanh toán.",
        "wallet_deposit_confirmed_detail": (
            "✅ <b>NẠP TIỀN THÀNH CÔNG</b>\n\n"
            "🔑 Mã tham chiếu: <code>{ref}</code>\n"
            "💰 Đã cộng: <b>{amount}</b>\n"
            "💼 Số dư mới: <b>{balance}</b>\n"
            "🕒 Thời gian: {time}"
        ),
        "wallet_deposit_created_usdt": (
            "✅ <b>YÊU CẦU NẠP TIỀN ĐÃ TẠO</b>\n\n"
            "🔑 Mã tham chiếu: <code>{ref}</code>\n"
            "💰 Số tiền: <b>{amount} USDT</b>\n\n"
            "🌐 Network: {network}\n"
            "Địa chỉ: <code>{address}</code>\n\n"
            "⚠️ Vui lòng gửi <b>đúng số tiền</b> ở trên (kể cả phần lẻ) để hệ thống nhận diện đúng giao dịch của bạn.\n"
            "🤖 Ví của bạn sẽ được cộng tiền tự động sau khi giao dịch đủ số xác nhận trên blockchain."
        ),
        "wallet_deposit_detecting": "🔎 Đã phát hiện giao dịch nạp tiền <code>{ref}</code> — đang chờ xác nhận blockchain ({current}/{required}).",
        "wallet_history_title": "📜 <b>LỊCH SỬ GIAO DỊCH VÍ</b>\n",
        "wallet_history_empty": "Bạn chưa có giao dịch ví nào.",
        "wallet_deposit_confirmed_user": "✅ Yêu cầu nạp tiền <code>{ref}</code> đã được cộng tiền tự động!\n💰 Đã cộng: <b>{amount}</b> vào ví của bạn.",
        "wallet_deposit_rejected_user": "❌ Yêu cầu nạp tiền <code>{ref}</code> đã bị từ chối.\n{note}",
        "wallet_deposit_expired_user": "⌛ Yêu cầu nạp tiền <code>{ref}</code> đã hết hạn vì không nhận được giao dịch. Vui lòng tạo yêu cầu mới nếu bạn vẫn muốn nạp tiền.",
        "wallet_refund_notice": "💼 Đơn <code>{code}</code> gặp lỗi khi giao hàng — số tiền <b>{amount}đ</b> đã được hoàn lại vào ví của bạn.",
        "wallet_admin_credit_notice": "💼 Ví của bạn đã được admin cộng thêm <b>{amount}</b>.\n📝 Lý do: {note}",
        "wallet_admin_debit_notice": "💼 Ví của bạn đã bị admin trừ <b>{amount}</b>.\n📝 Lý do: {note}",
        "btn_pay_wallet": "💼 Thanh toán bằng Ví",
        "wallet_insufficient_balance": "❌ Số dư ví không đủ.\nCần: <b>{needed}đ</b>\nHiện có: <b>{have}đ</b>\n\nVui lòng nạp thêm tiền vào ví.",
        "wallet_purchase_debited": "✅ Đã thanh toán <b>{amount}đ</b> từ Ví.\nSố dư còn lại: <b>{balance}đ</b>",
        # ── Customer API ──────────────────────────────────────────────────────
        "menu_btn_api": "🔗 API",
        "api_menu_title": "🔗 <b>LIÊN KẾT API</b>",
        "api_menu_no_key": "Bạn chưa có API key.\nTạo key để lấy sản phẩm và đặt hàng qua API.",
        "api_menu_status": "📶 Trạng thái: <b>{status}</b>",
        "api_menu_key": "🔑 API Key của bạn:\n<code>{key}</code>",
        "api_menu_prepaid_notice": "⚠️ API hoạt động theo cơ chế trả trước.\nBạn phải có số dư trong ví trước khi gọi API mua hàng.",
        "api_menu_balance": "💰 Số dư: <b>{vnd}đ</b> | <b>{usdt} USDT</b>",
        "api_menu_usage": "📊 Đã dùng: {requests} request, {orders} đơn hàng",
        "api_menu_permissions": "🔐 Quyền: {permissions}",
        "api_menu_created": "📅 Ngày tạo: {date}",
        "api_status_active": "✅ Đang hoạt động",
        "api_status_locked": "⏸ Đã bị khóa (do admin)",
        "api_status_revoked": "🚫 Đã thu hồi",
        "btn_api_generate": "🆕 Tạo API key",
        "btn_api_regenerate": "♻️ Tạo lại API Key",
        "btn_api_revoke": "🗑 Thu hồi key",
        "btn_api_history": "📜 Lịch sử request",
        "btn_api_guide": "📘 Hướng dẫn dùng API",
        "btn_api_swagger": "📘 Mở Swagger",
        "api_key_generated": (
            "✅ <b>API key đã được tạo!</b>\n\n"
            "<code>{key}</code>\n\n"
            "⚠️ Đây là LẦN DUY NHẤT bạn thấy key này — hãy lưu lại ngay.\n"
            "Nếu mất key, dùng \"🔄 Cấp lại key\" để tạo key mới (key cũ sẽ bị hủy)."
        ),
        "api_key_regenerated": (
            "✅ <b>Key mới đã được tạo!</b>\n\n"
            "<code>{key}</code>\n\n"
            "⚠️ Key cũ đã ngừng hoạt động ngay lập tức. Lưu key mới này lại."
        ),
        "api_key_revoked": "🗑 Đã thu hồi API key. Bạn có thể tạo key mới bất cứ lúc nào.",
        "api_confirm_regenerate": "Cấp lại key mới sẽ vô hiệu hóa key cũ ngay lập tức. Tiếp tục?",
        "api_confirm_revoke": "Thu hồi key sẽ dừng mọi request đang dùng key này. Tiếp tục?",
        "api_history_title": "📜 <b>LỊCH SỬ REQUEST (20 gần nhất)</b>\n",
        "api_history_empty": "Chưa có request nào.",
        "api_guide_title": "📘 <b>HƯỚNG DẪN DÙNG API</b>",
        "api_guide_body": (
            "Gửi header <code>X-API-Key: &lt;key_của_bạn&gt;</code> trong mọi request.\n\n"
            "<b>Danh sách sản phẩm:</b>\n"
            "<code>curl {base}/api/v1/products \\\n"
            "  -H \"X-API-Key: YOUR_KEY\"</code>\n\n"
            "<b>Số dư:</b>\n"
            "<code>curl {base}/api/v1/balance \\\n"
            "  -H \"X-API-Key: YOUR_KEY\"</code>\n\n"
            "<b>Đặt hàng:</b>\n"
            "<code>curl -X POST {base}/api/v1/orders \\\n"
            "  -H \"X-API-Key: YOUR_KEY\" \\\n"
            "  -H \"Content-Type: application/json\" \\\n"
            "  -d '{{\"product_id\": 1, \"quantity\": 1, \"currency\": \"VND\", \"client_order_id\": \"my-unique-id-1\"}}'</code>\n\n"
            "<b>Xem đơn hàng:</b>\n"
            "<code>curl {base}/api/v1/orders/ORD-XXXXXXXX \\\n"
            "  -H \"X-API-Key: YOUR_KEY\"</code>\n\n"
            "⚠️ <code>client_order_id</code> phải là duy nhất cho mỗi đơn — gửi lại cùng ID sẽ trả về kết quả đơn cũ, KHÔNG bị trừ tiền lần 2.\n"
            "Giới hạn: {rate_limit} request/phút, {daily_limit} request/ngày."
        ),
        "api_key_missing_to_show": "Bạn chưa có key nào để hiển thị. Hãy tạo key trước.",
        "api_admin_key_created": "🔗 Khách hàng <code>{tg_id}</code> đã tạo API key mới.",
        "api_admin_order_success": (
            "✅ <b>Đơn API thành công</b>\n"
            "📋 <code>{order_code}</code> | Client #{client_id}\n"
            "💰 {amount}"
        ),
        "api_admin_order_failed": (
            "🚨 <b>Đơn API gặp lỗi sau thanh toán</b>\n"
            "📋 <code>{order_code}</code> | Client #{client_id}\n"
            "Trạng thái: {status}"
        ),
        "api_admin_client_locked": "⏸ API client #{client_id} (<code>{tg_id}</code>) đã bị khóa bởi admin.",
    },

    "en": {
        # ── Menu ──────────────────────────────────────────────────────────────
        "menu_products": "🛍 Products",
        "menu_orders": "🔍 Find order",
        "menu_language": "🌐 Language",
        "menu_support": "💬 Support",
        "menu_btn_account": "👤 Account",
        "menu_admin": "🌐 Admin panel",
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
        # ── Order search ──────────────────────────────────────────────────────
        "order_search_prompt": "📧 Enter the email or purchased account to find your order:",
        "order_search_not_found": "❌ No order found matching what you entered.",
        "order_search_pick_title": "🔍 <b>Found {count} matching orders:</b>",
        "order_search_invalid": "⚠️ Please enter a valid email or account.",
        "order_detail_title": "📋 <b>ORDER DETAIL</b>",
        "order_detail_code": "🆔 Order: <code>{code}</code>",
        "order_detail_product": "📦 Product: {product}",
        "order_detail_buyer": "👤 Buyer: <code>{buyer}</code>",
        "order_detail_seller": "🏪 Seller: {seller}",
        "order_detail_account": "🔑 Delivered account:",
        "order_detail_price": "💰 Price: {price}",
        "order_detail_purchase_time": "📅 Purchase time: {time}",
        "order_detail_warranty": "🛡 Warranty: {warranty}",
        "order_detail_days_used": "📆 Used: {days} days",
        "order_detail_days_remaining": "⏳ Remaining: {days} days",
        "order_detail_max_refund": "💵 Max refund: {amount}",
        "order_detail_status": "📊 Status: {status}",
        "order_detail_no_account": "(no account delivered yet)",
        "btn_report_issue": "⚠️ Report issue",
        # ── Issue reporting ───────────────────────────────────────────────────
        "issue_report_prompt": "📝 Please describe the issue (you can attach a photo/video/file):",
        "issue_report_saved": "✅ Your report was sent to the admin. Please wait for a reply.",
        "issue_report_error": "⚠️ Could not send the report, please try again.",
        "issue_reply_prompt": "💬 Enter the reply to send to the customer:",
        "issue_reply_sent": "✅ Reply sent to the customer.",
        "issue_reply_received": "💬 <b>Admin reply about order <code>{code}</code>:</b>\n\n{text}",
        "issue_reject_prompt": "❌ Enter the rejection reason:",
        "issue_rejected_admin": "❌ Issue #{id} rejected.",
        "issue_rejected_user": "❌ <b>Your report for order <code>{code}</code> was rejected.</b>\n\nReason: {reason}",
        "issue_resolved_admin": "✅ Issue #{id} marked as resolved.",
        "issue_already_handled": "⚠️ This issue was already handled.",
        "issue_not_found": "⚠️ Issue not found.",
        "refund_success_admin": "✅ Refunded {amount} to the customer's wallet for order <code>{code}</code>.",
        "refund_success_user": (
            "✅ <b>REFUND SUCCESSFUL</b>\n\n"
            "🧾 Order: <code>{code}</code>\n"
            "💰 Refund amount: <b>{amount}</b>\n"
            "👛 New balance: <b>{new_balance}</b>"
        ),
        "refund_already_done": "⚠️ This transaction was already refunded.",
        "refund_warranty_expired": "⚠️ The order's warranty has expired — no refund available.",
        "refund_not_authorized": "🚫 You are not authorized to perform this action.",
        "support_contact": "💬 Contact support: @{username}",
        "support_contact_admin": "💬 Please contact the administrator for support.",
        # ── Paid waiting stock ────────────────────────────────────────────────
        "paid_waiting_stock_user": (
            "✅ Payment received.\n\n"
            "⚠️ Product just went out of stock at the source.\n"
            "Your order has been moved to manual processing."
        ),
        "pending_seller_fulfillment_user": (
            "✅ Payment received.\n\n"
            "⏳ This product requires seller processing (slot type).\n"
            "Your order is waiting for the seller to confirm — we'll notify you as soon as it's ready."
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
        "account_info_full": (
            "👤 <b>ACCOUNT INFORMATION</b>\n\n"
            "Name: {full_name}\n"
            "Username: @{username}\n"
            "Chat ID: <code>{tg_id}</code>\n"
            "Language: {language}\n\n"
            "👛 VND balance: <b>{balance_vnd} VND</b>\n"
            "💵 USDT balance: <b>{balance_usdt} USDT</b>\n\n"
            "🔗 API status: {api_status}\n"
            "🔑 API Key: <code>{api_key_masked}</code>\n"
            "📅 API created at: {api_created_at}\n"
            "📦 API orders: {api_order_count}\n"
            "💳 Total API spending: {api_total_spent}\n\n"
            "📦 Total orders: {total_orders}\n"
            "✅ Completed orders: {completed_orders}"
        ),
        # ── Redesigned "Account info" with membership rank ──────────────────────
        "greeting_morning": "morning",
        "greeting_afternoon": "afternoon",
        "greeting_evening": "evening",
        "account_info_v2": (
            "🌅 Good {time_of_day}, {full_name}\n"
            "🆔 ID: <code>{tg_id}</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "👑 <b>Rank:</b>\n{rank_emoji} {rank_name}\n\n"
            "💰 <b>Balance:</b>\n{balance} VND\n\n"
            "📉 <b>Total spent:</b>\n{total_spent} VND\n\n"
            "📦 <b>Total orders:</b>\n{total_orders}\n\n"
            "🛍 <b>Total accounts purchased:</b>\n{total_accounts}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "{progress_section}"
        ),
        "rank_progress_section": (
            "📈 <b>Progress to next rank</b>\n\n"
            "{bar} {percent}%\n\n"
            "Remaining:\n{remaining} VND\n\n"
            "to reach:\n\n{next_rank_emoji} {next_rank_name}"
        ),
        "rank_max_section": "🏆 You've reached the highest rank.",
        "rank_upgraded": (
            "🎉 <b>CONGRATULATIONS!</b>\n\n"
            "You've been upgraded to\n\n"
            "{rank_emoji} <b>{rank_name}</b>\n\n"
            "Thank you for being with AI Center."
        ),
        "btn_account_docs": "📘 API Documentation",
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
        # ── invalid command ───────────────────────────────────────────────────
        "invalid_command": (
            "⚠️ Invalid command. Supported commands:\n\n"
            "/start – Start and open menu\n"
            "/menu – Open the main menu\n"
            "/products – Show products\n"
            "/orders – Show orders\n"
            "/wallet – My wallet\n"
            "/language – Change language\n"
            "/support – Open support"
        ),
        # ── Wallet ────────────────────────────────────────────────────────────
        "menu_wallet": "💼 My Wallet",
        "menu_btn_wallet": "💼 My Wallet",
        "wallet_title": "💼 <b>MY WALLET</b>",
        "wallet_balance_vnd": "💰 VND balance: <b>{amount} VND</b>",
        "wallet_balance_usdt": "💰 USDT balance: <b>{amount} USDT</b>",
        "btn_wallet_deposit": "➕ Deposit",
        "btn_wallet_history": "📜 Transaction history",
        "wallet_choose_deposit_currency": "💼 Which currency would you like to deposit?",
        "btn_wallet_deposit_vnd": "🏦 VND (Bank transfer)",
        "btn_wallet_deposit_usdt": "🟨 USDT (Crypto)",
        "wallet_choose_deposit_method": "💼 Choose a deposit method:",
        "wallet_enter_amount_vnd": "🔢 Enter the amount to deposit (VND), e.g. 100000",
        "wallet_enter_amount_usdt": "🔢 Enter the amount to deposit (USDT), e.g. 10",
        "wallet_amount_invalid": "❌ Please enter a valid amount (positive number).",
        "wallet_deposit_no_payment_configured": "❌ This payment method isn't configured yet. Please choose another or contact support.",
        "wallet_deposit_created_vnd": (
            "✅ <b>DEPOSIT REQUEST CREATED</b>\n\n"
            "🔑 Reference: <code>{ref}</code>\n\n"
            "🏦 Bank: <b>{bank}</b>\n"
            "👤 Account holder: {acc_name}\n"
            "🔢 Account number: <code>{acc}</code>\n"
            "💰 Amount: <code>{amount}</code> VND\n"
            "📝 Transfer note: <code>{ref}</code>\n\n"
            "⚠️ Please transfer the exact amount and use the note shown above.\n"
            "🤖 Your wallet will be credited automatically as soon as the transfer arrives — no admin wait.\n"
            "📷 Or scan the QR code above with any banking app to pay instantly."
        ),
        "btn_check_deposit": "🔄 Check payment",
        "btn_cancel_deposit": "❌ Cancel",
        "wallet_deposit_cancelled_user": "❌ Deposit request has been cancelled.",
        "wallet_deposit_cancel_denied": "Can't cancel — this request has already been processed or doesn't exist.",
        "wallet_deposit_check_pending": "⏳ No transfer received yet for <code>{ref}</code>. Your wallet will be credited automatically as soon as it arrives — nothing else to do.",
        "wallet_deposit_check_credited": "✅ Deposit <code>{ref}</code> has already been credited to your wallet!",
        "wallet_deposit_check_gone": "This deposit request is no longer pending.",
        "wallet_deposit_confirmed_detail": (
            "✅ <b>DEPOSIT SUCCESSFUL</b>\n\n"
            "🔑 Reference: <code>{ref}</code>\n"
            "💰 Credited: <b>{amount}</b>\n"
            "💼 New balance: <b>{balance}</b>\n"
            "🕒 Time: {time}"
        ),
        "wallet_deposit_created_usdt": (
            "✅ <b>DEPOSIT REQUEST CREATED</b>\n\n"
            "🔑 Reference: <code>{ref}</code>\n"
            "💰 Amount: <b>{amount} USDT</b>\n\n"
            "🌐 Network: {network}\n"
            "Address: <code>{address}</code>\n\n"
            "⚠️ Please send <b>exactly</b> the amount above (including the decimals) so the system can match your transfer.\n"
            "🤖 Your wallet will be credited automatically once the transaction reaches the required confirmations."
        ),
        "wallet_deposit_detecting": "🔎 Detected an incoming transfer for deposit <code>{ref}</code> — waiting on blockchain confirmations ({current}/{required}).",
        "wallet_history_title": "📜 <b>WALLET TRANSACTION HISTORY</b>\n",
        "wallet_history_empty": "You have no wallet transactions yet.",
        "wallet_deposit_confirmed_user": "✅ Deposit request <code>{ref}</code> auto-credited!\n💰 Credited: <b>{amount}</b> to your wallet.",
        "wallet_deposit_rejected_user": "❌ Deposit request <code>{ref}</code> was rejected.\n{note}",
        "wallet_deposit_expired_user": "⌛ Deposit request <code>{ref}</code> expired — no matching transfer was received. Please create a new request if you'd still like to top up.",
        "wallet_refund_notice": "💼 Order <code>{code}</code> could not be fulfilled — <b>{amount} VND</b> has been refunded to your wallet.",
        "wallet_admin_credit_notice": "💼 Admin credited your wallet with <b>{amount}</b>.\n📝 Reason: {note}",
        "wallet_admin_debit_notice": "💼 Admin deducted <b>{amount}</b> from your wallet.\n📝 Reason: {note}",
        "btn_pay_wallet": "💼 Pay with Wallet",
        "wallet_insufficient_balance": "❌ Insufficient wallet balance.\nNeeded: <b>{needed} VND</b>\nYou have: <b>{have} VND</b>\n\nPlease top up your wallet.",
        "wallet_purchase_debited": "✅ Paid <b>{amount} VND</b> from your Wallet.\nRemaining balance: <b>{balance} VND</b>",
        # ── Customer API ──────────────────────────────────────────────────────
        "menu_btn_api": "🔗 API",
        "api_menu_title": "🔗 <b>API CONNECTION</b>",
        "api_menu_no_key": "You don't have an API key yet.\nGenerate one to fetch products and place orders via API.",
        "api_menu_status": "📶 Status: <b>{status}</b>",
        "api_menu_key": "🔑 Your API Key:\n<code>{key}</code>",
        "api_menu_prepaid_notice": "⚠️ This API uses a prepaid wallet system.\nYou must have sufficient wallet balance before using the purchase API.",
        "api_menu_balance": "💰 Balance: <b>{vnd} VND</b> | <b>{usdt} USDT</b>",
        "api_menu_usage": "📊 Usage: {requests} requests, {orders} orders",
        "api_menu_permissions": "🔐 Permissions: {permissions}",
        "api_menu_created": "📅 Created: {date}",
        "api_status_active": "✅ Active",
        "api_status_locked": "⏸ Locked (by admin)",
        "api_status_revoked": "🚫 Revoked",
        "btn_api_generate": "🆕 Generate API key",
        "btn_api_regenerate": "♻️ Regenerate API Key",
        "btn_api_revoke": "🗑 Revoke key",
        "btn_api_history": "📜 Request history",
        "btn_api_guide": "📘 Usage guide",
        "btn_api_swagger": "📘 Open Swagger",
        "api_key_generated": (
            "✅ <b>API key generated!</b>\n\n"
            "<code>{key}</code>\n\n"
            "⚠️ This is the ONLY TIME you'll see this key — save it now.\n"
            "If you lose it, use \"🔄 Regenerate key\" to create a new one (the old one will stop working)."
        ),
        "api_key_regenerated": (
            "✅ <b>New key generated!</b>\n\n"
            "<code>{key}</code>\n\n"
            "⚠️ The old key stopped working immediately. Save this new one."
        ),
        "api_key_revoked": "🗑 API key revoked. You can generate a new one anytime.",
        "api_confirm_regenerate": "Regenerating will immediately invalidate the old key. Continue?",
        "api_confirm_revoke": "Revoking will stop all requests using this key. Continue?",
        "api_history_title": "📜 <b>REQUEST HISTORY (last 20)</b>\n",
        "api_history_empty": "No requests yet.",
        "api_guide_title": "📘 <b>API USAGE GUIDE</b>",
        "api_guide_body": (
            "Send header <code>X-API-Key: &lt;your_key&gt;</code> on every request.\n\n"
            "<b>List products:</b>\n"
            "<code>curl {base}/api/v1/products \\\n"
            "  -H \"X-API-Key: YOUR_KEY\"</code>\n\n"
            "<b>Balance:</b>\n"
            "<code>curl {base}/api/v1/balance \\\n"
            "  -H \"X-API-Key: YOUR_KEY\"</code>\n\n"
            "<b>Create order:</b>\n"
            "<code>curl -X POST {base}/api/v1/orders \\\n"
            "  -H \"X-API-Key: YOUR_KEY\" \\\n"
            "  -H \"Content-Type: application/json\" \\\n"
            "  -d '{{\"product_id\": 1, \"quantity\": 1, \"currency\": \"VND\", \"client_order_id\": \"my-unique-id-1\"}}'</code>\n\n"
            "<b>Check order:</b>\n"
            "<code>curl {base}/api/v1/orders/ORD-XXXXXXXX \\\n"
            "  -H \"X-API-Key: YOUR_KEY\"</code>\n\n"
            "⚠️ <code>client_order_id</code> must be unique per order — resubmitting the same ID returns the original result and is NEVER charged twice.\n"
            "Limits: {rate_limit} requests/min, {daily_limit} requests/day."
        ),
        "api_key_missing_to_show": "You don't have a key to show yet. Generate one first.",
        "api_admin_key_created": "🔗 Customer <code>{tg_id}</code> generated a new API key.",
        "api_admin_order_success": (
            "✅ <b>API order succeeded</b>\n"
            "📋 <code>{order_code}</code> | Client #{client_id}\n"
            "💰 {amount}"
        ),
        "api_admin_order_failed": (
            "🚨 <b>API order failed after payment</b>\n"
            "📋 <code>{order_code}</code> | Client #{client_id}\n"
            "Status: {status}"
        ),
        "api_admin_client_locked": "⏸ API client #{client_id} (<code>{tg_id}</code>) was locked by admin.",
    },
}


def t(lang: str, i18n_key: str, **kwargs) -> str:
    """
    Return translated string for the given language and key.
    Falls back to Vietnamese if the key is missing for the requested language.
    Supports format placeholders: t(lang, "key", var=value)

    NOTE: the translation-key argument is named `i18n_key` (not `key`) on
    purpose — several templates have a `{key}` placeholder (e.g. showing an
    API key), and callers pass that as `key=...`. Naming this parameter
    `key` would collide with that kwarg ("got multiple values for
    argument 'key'"). Always call this positionally: t(lang, "some_key", ...).
    """
    lang = lang if lang in TRANSLATIONS else "vi"
    text = TRANSLATIONS[lang].get(i18n_key) or TRANSLATIONS["vi"].get(i18n_key, i18n_key)
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
