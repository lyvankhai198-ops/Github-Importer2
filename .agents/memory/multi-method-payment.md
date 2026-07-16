---
name: Multi-method payment architecture
description: How the bot presents payment method selection after quantity entry
---

## Rule
After user enters quantity → validate stock → create Order row with status=pending_payment but payment_method=None → send "choose payment" inline keyboard with order_id in callback data → user taps method → pay_method:{order_id}:{method} callback → update order + send payment instructions.

## Why
Decouples order creation from payment method so users can choose without re-entering data. order_id in callback data is authoritative (no reliance on user_data which can be cleared).

## How to apply
- `pay_method:` callback: verify order.telegram_user_id == update.effective_user.id before acting
- payment_method_keyboard(order_id, enabled_methods, lang) builds buttons
- get_enabled_payment_methods(db) returns ["bank_transfer", ...active PaymentMethod codes]
- bank_transfer=SePay, binance_pay, usdt_bep20, usdt_trc20 are all options
