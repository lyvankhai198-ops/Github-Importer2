"""
Explicit, admin-triggered restock notification (the "Notify users" checkbox
shown when adding stock to a product) — distinct from
inventory_service.notify_restock_if_enabled, which is the *global*
notify_users_when_restocked toggle that only pings paid_waiting_stock orders.

Targeting rule: if the product has an explicit "notify me" waiting list
(RestockSubscription rows, created via the bot's out-of-stock button), only
those users are notified and their subscriptions are consumed. Otherwise,
every non-banned bot user is notified as a general audience.
"""
import logging
import html

from database import SessionLocal
from models import Product, RestockSubscription, User

logger = logging.getLogger(__name__)


async def notify_restock_waiting_list(product_id: int) -> dict:
    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product:
            return {"notified": 0, "audience": "none"}

        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return {"notified": 0, "audience": "bot_offline"}

        from bot.i18n import get_user_lang
        bot = bot_manager._application.bot

        subs = db.query(RestockSubscription).filter(
            RestockSubscription.product_id == product_id
        ).all()

        if subs:
            audience = "waiting_list"
            targets = [(s.telegram_user_id, s) for s in subs]
        else:
            audience = "all_users"
            users = db.query(User).filter(User.is_banned == False).all()
            targets = [(u.telegram_id, None) for u in users]

        notified = 0
        name = html.escape(product.name)
        for telegram_user_id, sub in targets:
            lang = get_user_lang(db, telegram_user_id)
            text = (
                f"📦 <b>{name}</b> đã có hàng trở lại!\nVào bot để đặt hàng ngay."
                if lang != "en" else
                f"📦 <b>{name}</b> is back in stock!\nOpen the bot to order now."
            )
            try:
                await bot.send_message(chat_id=int(telegram_user_id), text=text, parse_mode="HTML")
                notified += 1
            except Exception as e:
                logger.error(f"[restock_notify] send failed for {telegram_user_id}: {e}")
            if sub:
                db.delete(sub)

        if subs:
            db.commit()

        return {"notified": notified, "audience": audience}
    except Exception as e:
        logger.error(f"[restock_notify] notify_restock_waiting_list error: {e}")
        return {"notified": 0, "audience": "error"}
    finally:
        db.close()
