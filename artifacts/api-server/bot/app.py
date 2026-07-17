import logging
import traceback

from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters,
)
from bot.handlers import (
    start_handler, products_handler, orders_handler, support_handler,
    admin_panel_handler, callback_handler, message_handler,
    menu_handler, myid_handler, _set_bot_commands, cancel_handler,
    unknown_command_handler, wallet_handler, api_handler, account_info_handler,
    media_message_handler,
)

logger = logging.getLogger(__name__)


async def setup_application(token: str, db_session_factory):
    async def _post_init(app):
        """Set default Telegram Menu commands on startup."""
        await _set_bot_commands(app.bot, lang="en")

    async def _on_error(update, context):
        logger.error(
            "TELEGRAM_HANDLER_ERROR: %s\n%s",
            context.error,
            "".join(traceback.format_exception(type(context.error), context.error, context.error.__traceback__)),
        )
        try:
            if update is not None and getattr(update, "effective_chat", None):
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="⚠️ An error occurred. Please try again or contact support.",
                )
        except Exception:
            pass

    app = ApplicationBuilder().token(token).post_init(_post_init).build()
    app.add_error_handler(_on_error)

    # ── Slash commands ────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",    start_handler))
    app.add_handler(CommandHandler("menu",     menu_handler))
    app.add_handler(CommandHandler("product",  products_handler))
    app.add_handler(CommandHandler("products", products_handler))
    app.add_handler(CommandHandler("orders",   orders_handler))
    app.add_handler(CommandHandler("wallet",   wallet_handler))
    app.add_handler(CommandHandler("api",      api_handler))
    app.add_handler(CommandHandler("account",  account_info_handler))
    app.add_handler(CommandHandler("support",  support_handler))
    app.add_handler(CommandHandler("myid",     myid_handler))
    app.add_handler(CommandHandler("cancel",   cancel_handler))

    # ── Menu buttons (English) ────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Regex(r"^🛍 Products$"),    products_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🔍 Find order$"),  orders_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^💼 My Wallet$"),   wallet_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🔗 API$"),         api_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^👤 Account$"),     account_info_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^💬 Support$"),     support_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🌐 Admin panel$"), admin_panel_handler))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^(❌\s*)?cancel$"), cancel_handler))

    # ── Inline keyboard callbacks ─────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(callback_handler))

    # ── Media capture for issue reports (photo/video/document) ───────────────
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.VIDEO | filters.Document.ALL) & ~filters.COMMAND,
        media_message_handler,
    ))

    # ── Free-text input (quantity, TXID, etc.) ────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # ── Unknown command fallback ──────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command_handler))

    return app
