"""
Admin -> all bot users broadcast (dashboard "📢 Thông báo Bot" page).
Sends a title + content message, optionally with an image, to every
non-banned bot user, and reports back sent/failed counts.
"""
import logging
import html

from sqlalchemy.orm import Session
from models import User

logger = logging.getLogger(__name__)


async def send_broadcast(db: Session, title: str, content: str, image_path: str | None = None) -> dict:
    from services.bot_service import bot_manager
    if not bot_manager.is_running():
        return {"sent": 0, "failed": 0, "total": 0, "error": "Bot chưa chạy — vui lòng bật bot trước khi gửi thông báo."}

    users = db.query(User).filter(User.is_banned == False).all()
    total = len(users)
    sent = 0
    failed = 0

    bot = bot_manager._application.bot
    text = f"📢 <b>{html.escape(title)}</b>\n\n{html.escape(content)}"

    photo_source = None
    if image_path:
        if image_path.startswith("/uploads/"):
            from config import UPLOADS_DIR
            fpath = UPLOADS_DIR / image_path.split("/uploads/", 1)[1]
            if fpath.exists():
                photo_source = fpath.read_bytes()
        else:
            photo_source = image_path  # external URL

    for user in users:
        try:
            if photo_source:
                await bot.send_photo(chat_id=int(user.telegram_id), photo=photo_source, caption=text, parse_mode="HTML")
            else:
                await bot.send_message(chat_id=int(user.telegram_id), text=text, parse_mode="HTML")
            sent += 1
        except Exception as e:
            failed += 1
            logger.error(f"[broadcast] send failed for user {user.telegram_id}: {e}")

    logger.info(f"BROADCAST_SENT: total={total} sent={sent} failed={failed}")
    return {"sent": sent, "failed": failed, "total": total, "error": None}
