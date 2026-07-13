from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters,
)
from bot.handlers import (
    start_handler, products_handler, orders_handler, support_handler,
    admin_panel_handler, callback_handler, message_handler, language_menu_handler,
    menu_handler, myid_handler, _set_bot_commands, cancel_handler, back_button_handler,
    unknown_command_handler, wallet_handler, api_handler,
)


async def setup_application(token: str, db_session_factory):
    async def _post_init(app):
        """Set default Telegram Menu commands on startup."""
        await _set_bot_commands(app.bot, lang="vi")

    app = ApplicationBuilder().token(token).post_init(_post_init).build()

    # ── Slash commands ────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",    start_handler))
    app.add_handler(CommandHandler("menu",     menu_handler))
    app.add_handler(CommandHandler("product",  products_handler))
    app.add_handler(CommandHandler("products", products_handler))
    app.add_handler(CommandHandler("orders",   orders_handler))
    app.add_handler(CommandHandler("wallet",   wallet_handler))
    app.add_handler(CommandHandler("api",      api_handler))
    app.add_handler(CommandHandler("language", language_menu_handler))
    app.add_handler(CommandHandler("support",  support_handler))
    app.add_handler(CommandHandler("myid",     myid_handler))
    app.add_handler(CommandHandler("cancel",   cancel_handler))

    # ── VI menu buttons ───────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Regex(r"^🛍 Sản phẩm$"),        products_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^📦 Đơn hàng$"),        orders_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^💼 Ví của tôi$"),       wallet_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🔗 API$"),             api_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^💬 Hỗ trợ$"),          support_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🌐 Ngôn ngữ$"),        language_menu_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🌐 Mở trang quản trị$"), admin_panel_handler))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^(❌\s*)?(hủy|huỷ|hủy bỏ|huỷ bỏ)$"), cancel_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Quay lại$"), back_button_handler))

    # ── EN menu buttons ───────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Regex(r"^🛍 Products$"),    products_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^📦 Orders$"),      orders_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^💼 My Wallet$"),    wallet_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^💬 Support$"),     support_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🌐 Language$"),    language_menu_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🌐 Admin panel$"), admin_panel_handler))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^(❌\s*)?cancel$"), cancel_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^⬅️ Back$"), back_button_handler))

    # ── Persistent green-menu buttons (VI + EN share the same "☰ Menu" label) ──
    app.add_handler(MessageHandler(filters.Regex(r"^☰ Menu$"), menu_handler))

    # ── Inline keyboard callbacks ─────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(callback_handler))

    # ── Free-text input (quantity, etc.) ─────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # ── Unknown command fallback (must be last: only fires if no command
    #    handler above matched) ────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command_handler))

    return app
