import logging
import traceback

from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters,
)
from bot.handlers import (
    start_handler, products_handler, orders_handler, support_handler,
    admin_panel_handler, callback_handler, message_handler, language_menu_handler,
    menu_handler, myid_handler, _set_bot_commands, cancel_handler,
    unknown_command_handler, wallet_handler, api_handler, account_info_handler,
    media_message_handler,
)

logger = logging.getLogger(__name__)


async def setup_application(token: str, db_session_factory):
    async def _post_init(app):
        """Set default Telegram Menu commands on startup."""
        await _set_bot_commands(app.bot, lang="vi")

    async def _on_error(update, context):
        """
        Without this, an exception raised inside any handler is silently
        swallowed by PTB (just a bare log line) and the user gets NO reply
        at all — indistinguishable from the bot being dead. Log the full
        traceback (tenant-scoped, since this app runs one Application per
        tenant) so a "no reply to /start" report is actually debuggable,
        and best-effort tell the user something went wrong instead of
        leaving them staring at silence.
        """
        logger.error(
            "TELEGRAM_HANDLER_ERROR: %s\n%s",
            context.error,
            "".join(traceback.format_exception(type(context.error), context.error, context.error.__traceback__)),
        )
        try:
            if update is not None and getattr(update, "effective_chat", None):
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="⚠️ Đã có lỗi xảy ra, vui lòng thử lại hoặc liên hệ hỗ trợ.",
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
    app.add_handler(CommandHandler("language", language_menu_handler))
    app.add_handler(CommandHandler("support",  support_handler))
    app.add_handler(CommandHandler("myid",     myid_handler))
    app.add_handler(CommandHandler("cancel",   cancel_handler))

    # ── VI menu buttons ───────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Regex(r"^🛍 Sản phẩm$"),        products_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🔍 Tìm đơn hàng$"),    orders_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^💼 Ví của tôi$"),       wallet_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🔗 API$"),             api_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^👤 Thông tin$"),        account_info_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^💬 Hỗ trợ$"),          support_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🌐 Ngôn ngữ$"),        language_menu_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🌐 Mở trang quản trị$"), admin_panel_handler))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^(❌\s*)?(hủy|huỷ|hủy bỏ|huỷ bỏ)$"), cancel_handler))

    # ── EN menu buttons ───────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Regex(r"^🛍 Products$"),    products_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🔍 Find order$"), orders_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^💼 My Wallet$"),    wallet_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^👤 Account$"),      account_info_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^💬 Support$"),     support_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🌐 Language$"),    language_menu_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^🌐 Admin panel$"), admin_panel_handler))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^(❌\s*)?cancel$"), cancel_handler))

    # ── Inline keyboard callbacks ─────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(callback_handler))

    # ── Media capture for "⚠️ Báo lỗi" issue reports (photo/video/document) ──
    #    Must be registered before the text-only handler; it no-ops unless
    #    the user is mid-report (state == waiting_issue_text).
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.VIDEO | filters.Document.ALL) & ~filters.COMMAND,
        media_message_handler,
    ))

    # ── Free-text input (quantity, etc.) ─────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # ── Unknown command fallback (must be last: only fires if no command
    #    handler above matched) ────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command_handler))

    return app
