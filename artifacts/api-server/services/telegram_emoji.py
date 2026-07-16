"""
Telegram custom emoji import — pulls the full list of custom emoji IDs out
of a Telegram custom emoji sticker pack (e.g.
https://t.me/addemoji/IconsEmoji_JABA) via the Bot API's getStickerSet
method, so the admin doesn't have to type in every emoji ID by hand.

getStickerSet works for any sticker set type (regular / mask / custom_emoji)
without the bot needing to be a member of anything — it just needs a valid
bot token. Each Sticker object in a custom_emoji set includes a
custom_emoji_id field (the same value used in the <tg-emoji emoji-id="..">
HTML tag) plus the pack's own fallback "emoji" field.

If the bot token isn't configured yet, or Telegram is unreachable, or the
pack name is wrong, this raises TelegramEmojiImportError with a message
safe to show the admin — the caller (routers/emoji_icons.py) falls back to
manual entry per-icon in that case (name + custom_emoji_id + fallback emoji).
"""
import html
import re
import httpx
from sqlalchemy.orm import Session

from crypto import decrypt
from models import TelegramBotConfig

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramEmojiImportError(Exception):
    pass


def render_icon_html(fallback_emoji: str | None, custom_emoji_id: str | None) -> str:
    """
    Shared renderer for a product's Telegram icon, used everywhere a bot
    message shows it (bot/handlers.py product detail, services/
    broadcast_service.py new-product/restock announcements). Requires the
    caller to send with parse_mode="HTML".

    - custom_emoji_id set -> <tg-emoji emoji-id="..">fallback</tg-emoji>
      (Telegram renders the actual custom emoji graphic to Premium users;
      everyone else sees the escaped fallback character inside the tag).
    - custom_emoji_id blank -> just the escaped fallback character
      (defaults to 📦 if fallback_emoji is also blank).
    """
    fallback = html.escape((fallback_emoji or "").strip() or "📦")
    custom_emoji_id = (custom_emoji_id or "").strip()
    if custom_emoji_id:
        return f'<tg-emoji emoji-id="{html.escape(custom_emoji_id)}">{fallback}</tg-emoji>'
    return fallback


def render_description_blockquote(header_html: str, escaped_desc: str) -> str:
    """
    Wraps a product's description in Telegram's native <blockquote> HTML tag
    so it renders as a distinct tinted card with its own quote-mark icon
    (drawn by the Telegram client, not something further style-able from
    here) instead of a plain wall of text — this is what the admin asked
    for after showing a reference screenshot of another bot's product card.

    `header_html` should already be a pre-rendered i18n string (may contain
    <b> tags); `escaped_desc` must already be html.escape()'d by the caller
    (this function does no escaping itself, since callers may need to place
    HTML like <tg-emoji> elsewhere in the surrounding message).

    Descriptions longer than ~400 characters get the `expandable` attribute
    so a long ruleset collapses behind Telegram's "Show more" toggle instead
    of pushing the buy button off-screen on a short product card.
    """
    body = f"{header_html}\n{escaped_desc}"
    expandable = " expandable" if len(body) > 400 else ""
    return f"<blockquote{expandable}>{body}</blockquote>"


def parse_sticker_set_name(link_or_name: str) -> str:
    """
    Accepts either a bare sticker set short name ("IconsEmoji_JABA") or a
    full t.me link (https://t.me/addemoji/IconsEmoji_JABA, with or without
    query string) and returns just the short name Telegram's API expects.
    """
    value = (link_or_name or "").strip()
    if not value:
        return ""
    # Strip query string / fragment first (e.g. ?ref=...)
    value = value.split("?")[0].split("#")[0]
    match = re.search(r"t\.me/addemoji/([A-Za-z0-9_]+)", value)
    if match:
        return match.group(1)
    match = re.search(r"t\.me/([A-Za-z0-9_]+)", value)
    if match:
        return match.group(1)
    return value.strip("/")


def _get_bot_token(db: Session) -> str:
    cfg = db.query(TelegramBotConfig).first()
    if not cfg or not cfg.bot_token_encrypted:
        raise TelegramEmojiImportError(
            "Chưa cấu hình Telegram Bot Token (mục Cài đặt) — không thể tự nhập icon từ bộ pack."
        )
    token = decrypt(cfg.bot_token_encrypted)
    if not token:
        raise TelegramEmojiImportError(
            "Telegram Bot Token không hợp lệ — không thể tự nhập icon từ bộ pack."
        )
    return token


async def fetch_custom_emoji_stickers(sticker_set_name: str, db: Session) -> list[dict]:
    """
    Calls Telegram's getStickerSet for `sticker_set_name` and returns a list
    of {"name": str, "custom_emoji_id": str, "fallback_emoji": str} dicts,
    one per custom emoji sticker in the pack, in the pack's own order.

    Raises TelegramEmojiImportError (safe to show the admin) on any failure:
    no token configured, network error, pack not found, or pack is not a
    custom-emoji sticker set.
    """
    set_name = parse_sticker_set_name(sticker_set_name)
    if not set_name:
        raise TelegramEmojiImportError("Vui lòng nhập tên bộ pack hoặc link (VD: https://t.me/addemoji/IconsEmoji_JABA).")

    token = _get_bot_token(db)
    url = f"{TELEGRAM_API_BASE}/bot{token}/getStickerSet"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params={"name": set_name})
        data = resp.json()
    except Exception as e:
        raise TelegramEmojiImportError(f"Không thể kết nối tới Telegram: {e}")

    if not data.get("ok"):
        desc = data.get("description", "Lỗi không xác định từ Telegram.")
        raise TelegramEmojiImportError(f"Telegram trả lỗi: {desc}")

    result = data.get("result") or {}
    stickers = result.get("stickers") or []
    if not stickers:
        raise TelegramEmojiImportError(f"Bộ pack \"{set_name}\" không có icon nào (hoặc không phải bộ custom emoji).")

    icons = []
    for idx, sticker in enumerate(stickers, start=1):
        custom_emoji_id = sticker.get("custom_emoji_id")
        if not custom_emoji_id:
            # Not a custom_emoji-type sticker set (e.g. a regular sticker pack) —
            # skip silently rather than fail the whole import on a mixed pack.
            continue
        fallback = sticker.get("emoji") or "⭐"
        icons.append({
            "name": f"{set_name} #{idx}",
            "custom_emoji_id": str(custom_emoji_id),
            "fallback_emoji": fallback,
        })

    if not icons:
        raise TelegramEmojiImportError(
            f"Bộ pack \"{set_name}\" không phải bộ custom emoji (không có custom_emoji_id) — "
            "vui lòng thêm icon bằng tay ở form bên dưới."
        )
    return icons


def import_icons_from_entities(db: Session, entities_map: dict) -> dict:
    """
    Bulk-add EmojiIcon rows from a Telegram message's custom_emoji entities.

    Covers the case getStickerSet can't handle: an admin pastes/forwards a
    message that mixes individual custom emoji pulled from many different
    sticker packs (not one single named pack) — there is no Bot API call to
    list "every custom emoji in an arbitrary message" except reading the
    entities of a message the bot actually received. So the admin forwards
    that message to the bot (see bot/handlers.py message_handler), which
    calls this with `msg.parse_entities(types=["custom_emoji"])` — a dict of
    {MessageEntity: substring}, substring already correctly sliced by the
    library despite Telegram's UTF-16 offset/length quirks.

    Returns {"added": int, "skipped_duplicate": int} and commits.
    """
    from models import EmojiIcon

    existing_ids = {row[0] for row in db.query(EmojiIcon.custom_emoji_id).all()}
    max_sort = db.query(EmojiIcon).count()
    added = 0
    skipped = 0
    seen_in_batch = set()
    for entity, fallback_text in entities_map.items():
        custom_emoji_id = str(getattr(entity, "custom_emoji_id", "") or "")
        if not custom_emoji_id or custom_emoji_id in existing_ids or custom_emoji_id in seen_in_batch:
            skipped += 1
            continue
        seen_in_batch.add(custom_emoji_id)
        max_sort += 1
        db.add(EmojiIcon(
            name=f"Icon nhập {max_sort}",
            custom_emoji_id=custom_emoji_id,
            fallback_emoji=(fallback_text or "⭐").strip() or "⭐",
            sticker_set_name=None,
            sort_order=max_sort,
            is_active=True,
        ))
        added += 1
    db.commit()
    return {"added": added, "skipped_duplicate": skipped}
