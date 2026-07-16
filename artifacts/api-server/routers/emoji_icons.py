"""
Icon library management — "Kho icon Telegram" admin page.

Admins can populate this library two ways:
1. Import an entire Telegram custom emoji sticker pack in one click (e.g.
   https://t.me/addemoji/IconsEmoji_JABA) via services/telegram_emoji.py,
   which calls Telegram's getStickerSet Bot API method.
2. Add a single icon by hand (name + custom_emoji_id + fallback emoji) —
   the required fallback for when auto-import isn't possible (no bot token
   configured yet, pack unreachable, or the admin only has the ID from
   somewhere else).

Once in the library, active icons appear in the "Chọn icon sản phẩm" picker
on the product add/edit pages (templates/products.html).
"""
from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import EmojiIcon
from services.telegram_emoji import fetch_custom_emoji_stickers, TelegramEmojiImportError

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def check_auth(request: Request):
    return request.session.get("admin_id")


def flash(request: Request, msg: str, type: str = "success"):
    request.session["flash"] = {"type": type, "msg": msg}


@router.get("/emoji-icons", response_class=HTMLResponse)
async def emoji_icons_list(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    icons = db.query(EmojiIcon).order_by(EmojiIcon.sort_order.asc(), EmojiIcon.id.asc()).all()
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "emoji_icons.html", {
        "icons": icons,
        "flash": flash_msg,
    })


@router.post("/emoji-icons/import")
async def import_emoji_icons(
    request: Request,
    db: Session = Depends(get_db),
    sticker_set: str = Form(...),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    try:
        fetched = await fetch_custom_emoji_stickers(sticker_set, db)
    except TelegramEmojiImportError as e:
        flash(request, str(e), "error")
        return RedirectResponse(url="/emoji-icons", status_code=302)

    max_order = db.query(EmojiIcon).count()
    added, skipped = 0, 0
    set_name = fetched[0]["name"].rsplit(" #", 1)[0] if fetched else ""
    for item in fetched:
        exists = db.query(EmojiIcon).filter(EmojiIcon.custom_emoji_id == item["custom_emoji_id"]).first()
        if exists:
            skipped += 1
            continue
        db.add(EmojiIcon(
            name=item["name"],
            custom_emoji_id=item["custom_emoji_id"],
            fallback_emoji=item["fallback_emoji"],
            sticker_set_name=set_name,
            sort_order=max_order,
            is_active=True,
        ))
        max_order += 1
        added += 1
    db.commit()
    if added:
        flash(request, f"Đã nhập {added} icon từ bộ pack \"{set_name}\"" + (f" ({skipped} icon đã có, bỏ qua)." if skipped else "."))
    else:
        flash(request, f"Không có icon mới nào để nhập (tất cả {skipped} icon đã có trong kho).", "error")
    return RedirectResponse(url="/emoji-icons", status_code=302)


@router.post("/emoji-icons/add")
async def add_emoji_icon(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    custom_emoji_id: str = Form(...),
    fallback_emoji: str = Form("⭐"),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    name = name.strip()
    custom_emoji_id = custom_emoji_id.strip()
    if not name or not custom_emoji_id:
        flash(request, "Vui lòng nhập tên icon và Custom Emoji ID.", "error")
        return RedirectResponse(url="/emoji-icons", status_code=302)
    exists = db.query(EmojiIcon).filter(EmojiIcon.custom_emoji_id == custom_emoji_id).first()
    if exists:
        flash(request, "Custom Emoji ID này đã có trong kho.", "error")
        return RedirectResponse(url="/emoji-icons", status_code=302)
    max_order = db.query(EmojiIcon).count()
    db.add(EmojiIcon(
        name=name,
        custom_emoji_id=custom_emoji_id,
        fallback_emoji=(fallback_emoji.strip() or "⭐"),
        sort_order=max_order,
        is_active=True,
    ))
    db.commit()
    flash(request, f"Đã thêm icon \"{name}\".")
    return RedirectResponse(url="/emoji-icons", status_code=302)


@router.post("/emoji-icons/{icon_id}/toggle")
async def toggle_emoji_icon(icon_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    icon = db.query(EmojiIcon).filter(EmojiIcon.id == icon_id).first()
    if icon:
        icon.is_active = not icon.is_active
        db.commit()
    return RedirectResponse(url="/emoji-icons", status_code=302)


@router.post("/emoji-icons/{icon_id}/delete")
async def delete_emoji_icon(icon_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    icon = db.query(EmojiIcon).filter(EmojiIcon.id == icon_id).first()
    if icon:
        # Products that already reference this icon's custom_emoji_id keep
        # showing it fine (Telegram doesn't need our DB row to render it) —
        # they just won't be re-selectable from the picker anymore, and a
        # fresh "Xóa icon" + auto-assign would clear them off it.
        db.delete(icon)
        db.commit()
        flash(request, "Đã xóa icon khỏi kho.")
    return RedirectResponse(url="/emoji-icons", status_code=302)
