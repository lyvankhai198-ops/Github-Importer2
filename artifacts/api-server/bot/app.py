from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters,
)
from bot.handlers import (
    start_handler, products_handler, orders_handler, support_handler,
    admin_panel_handler, callback_handler, message_handler, language_menu_handler,
)


async def setup_application(token: str, db_session_factory):
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start_handler))

    # ── VI menu buttons ───────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Regex(r"^🛍 Sản phẩm$"), products_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^📦 Đơn hàng$"), orders_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^💬 Hỗ trợ$"), support_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🌐 Ngôn ngữ$"), language_menu_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🌐 Mở trang quản trị$"), admin_panel_handler))

    # ── EN menu buttons ───────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Regex(r"^🛍 Products$"), products_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^📦 Orders$"), orders_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^💬 Support$"), support_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🌐 Language$"), language_menu_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🌐 Admin panel$"), admin_panel_handler))

    # ── Inline keyboard callbacks ─────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(callback_handler))

    # ── Free-text input (quantity, etc.) ─────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    return app
