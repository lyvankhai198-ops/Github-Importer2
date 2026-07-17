"""
Internationalization (i18n) for the Telegram bot.
Usage: t(lang, "key") or t(lang, "key", var=value)
Language: English only.
"""

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # ── Menu ──────────────────────────────────────────────────────────────
        "menu_products": "🛍 Products",
        "menu_orders": "🔍 Find order",
        "menu_language": "🌐 Language",
        "menu_support": "💬 Support",
        "menu_btn_account": "👤 Account",
        "menu_admin": "🌐 Admin panel",
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
        "product_sold_count": "🔥 Sold: {count}",
        "product_duration": "⌛ Duration: {val}",
        "product_warranty": "🛡 Warranty: {val}",
        "product_description_header": "💬 <b>Description:</b>",
        "btn_buy_now": "🛒 Buy now",
        "btn_back": "◀️ Back",
        "btn_home": "🏠 Home",
        "btn_check_again": "🔄 Check again",
        # ── Automatic new-product / restock broadcasts ────────────────────────
        "notify_new_product_title": "🆕 <b>NEW PRODUCT</b>",
        "notify_restock_title": "🔄 <b>RESTOCKED</b>",
        "notify_price_line": "💰 Price: {price}",
        "notify_current_stock_line": "📦 Current stock: {stock}",
        "notify_added_line": "➕ Added: {qty}",
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
        # ── Binance Pay ───────────────────────────────────────────────────────
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
        # ── Account info ──────────────────────────────────────────────────────
        "menu_account_info": (
            "👤 <b>ACCOUNT INFORMATION</b>\n\n"
            "🆔 Telegram ID: <code>{tg_id}</code>\n"
            "👤 Username: {username}\n"
            "📦 Total orders: {total_orders}\n"
            "✅ Status: {status}"
        ),
        "account_info_full": (
            "👤 <b>ACCOUNT INFORMATION</b>\n\n"
            "Name: {full_name}\n"
            "Username: @{username}\n"
            "Chat ID: <code>{tg_id}</code>\n\n"
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
        # ── Redesigned account info with membership rank ──────────────────────
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
        "menu_btn_support":   "💬 Support",
        # ── /myid ─────────────────────────────────────────────────────────────
        "myid_response": "🆔 Your Telegram ID: <code>{tg_id}</code>",
        # ── out-of-stock popup ────────────────────────────────────────────────
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
    Return the English translation for the given key.
    Supports format placeholders: t(lang, "key", var=value)

    NOTE: the translation-key argument is named `i18n_key` (not `key`) on
    purpose — several templates have a `{key}` placeholder (e.g. showing an
    API key), and callers pass that as `key=...`. Naming this parameter
    `key` would collide with that kwarg ("got multiple values for
    argument 'key'"). Always call this positionally: t(lang, "some_key", ...).
    """
    text = TRANSLATIONS["en"].get(i18n_key, i18n_key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text


def get_user_lang(db, telegram_user_id: str) -> str:
    """Always returns English — Vietnamese support has been removed."""
    return "en"
