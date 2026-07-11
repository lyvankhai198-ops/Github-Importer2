from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.handlers import (
    start_handler, products_handler, orders_handler, support_handler,
    admin_panel_handler, callback_handler, message_handler
)


async def setup_application(token: str, db_session_factory):
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.Regex("^🛍 Sản phẩm$"), products_handler))
    app.add_handler(MessageHandler(filters.Regex("^📦 Đơn hàng$"), orders_handler))
    app.add_handler(MessageHandler(filters.Regex("^💬 Hỗ trợ$"), support_handler))
    app.add_handler(MessageHandler(filters.Regex("^🌐 Mở trang quản trị$"), admin_panel_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    return app
