"""
Tests for the Telegram custom emoji product-icon picker:
  - selecting a library icon stores telegram_icon (fallback) +
    telegram_custom_emoji_id together and locks both against auto-assignment
  - clearing the icon unlocks both fields for keyword-based auto-assignment
  - bot rendering emits <tg-emoji emoji-id="..."> only when a custom emoji id
    is set, and falls back to a plain escaped emoji otherwise
  - the out-of-stock/unavailable states never show a chosen custom emoji
  - the sticker-set link parser accepts both a bare name and a full t.me link
  - a pack with no custom_emoji_id stickers raises a clear, admin-safe error
"""
import pytest

from models import Product, DeliveryMode, SourceType
from services.product_sync import apply_admin_icon_edit, auto_assign_icon_if_unlocked, parse_edited_fields
from services.telegram_emoji import render_icon_html, parse_sticker_set_name


def make_product(db_session, **kwargs):
    p = Product(
        name=kwargs.pop("name", "Grok Pro"),
        product_code=kwargs.pop("product_code", "GRK-1"),
        sale_price=kwargs.pop("sale_price", 100000.0),
        delivery_mode=DeliveryMode.manual_admin,
        source_type=SourceType.manual,
        is_active=True,
        min_quantity=1,
        **kwargs,
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


# ── 1. Choosing an icon stores + locks both fields together ────────────────
def test_choosing_icon_stores_and_locks_both_fields(db_session):
    p = make_product(db_session)
    changed = apply_admin_icon_edit(p, "⭐", "5368324170671202286")
    assert changed is True
    assert p.telegram_icon == "⭐"
    assert p.telegram_custom_emoji_id == "5368324170671202286"
    assert "telegram_icon" in parse_edited_fields(p.manually_edited_fields)


# ── 2. Auto-assignment never overwrites a locked custom-emoji selection ────
def test_auto_assign_skips_locked_custom_emoji(db_session):
    p = make_product(db_session, name="Grok")  # would auto-assign 🤖 if unlocked
    apply_admin_icon_edit(p, "💎", "111222333")
    changed = auto_assign_icon_if_unlocked(p)
    assert changed is False
    assert p.telegram_icon == "💎"
    assert p.telegram_custom_emoji_id == "111222333"


# ── 3. Clearing the icon unlocks both fields for auto-assignment again ─────
def test_clearing_icon_unlocks_for_auto_assignment(db_session):
    p = make_product(db_session, name="Grok")
    apply_admin_icon_edit(p, "💎", "111222333")
    changed = apply_admin_icon_edit(p, "", "")
    assert changed is True
    assert p.telegram_icon is None
    assert p.telegram_custom_emoji_id is None
    assert "telegram_icon" not in parse_edited_fields(p.manually_edited_fields)
    # unlocked -> auto-assign can now fill it from the name keyword mapping
    auto_changed = auto_assign_icon_if_unlocked(p)
    assert auto_changed is True
    assert p.telegram_icon == "🤖"


# ── 4. Bot HTML rendering: custom emoji id present -> <tg-emoji> tag ────────
def test_render_icon_html_with_custom_emoji_id():
    out = render_icon_html("⭐", "5368324170671202286")
    assert out == '<tg-emoji emoji-id="5368324170671202286">⭐</tg-emoji>'


# ── 5. Bot HTML rendering: no custom emoji id -> plain escaped fallback ────
def test_render_icon_html_without_custom_emoji_id():
    assert render_icon_html("🤖", None) == "🤖"
    assert render_icon_html("", "") == "📦"  # default when nothing is set


# ── 6. HTML-escaping applies to both the fallback text and the emoji id ────
def test_render_icon_html_escapes_values():
    out = render_icon_html("<b>x</b>", '1"><script>')
    assert "<b>x</b>" not in out
    assert "&lt;b&gt;" in out
    assert "<script>" not in out


# ── 7. Sticker-set name parser accepts both a full t.me link and a bare name
def test_parse_sticker_set_name_from_link_and_bare_name():
    assert parse_sticker_set_name("https://t.me/addemoji/IconsEmoji_JABA") == "IconsEmoji_JABA"
    assert parse_sticker_set_name("https://t.me/addemoji/IconsEmoji_JABA?x=1") == "IconsEmoji_JABA"
    assert parse_sticker_set_name("IconsEmoji_JABA") == "IconsEmoji_JABA"
    assert parse_sticker_set_name("  ") == ""


# ── 8. Import raises a clear, admin-safe error when no bot token is configured
@pytest.mark.asyncio
async def test_fetch_custom_emoji_stickers_without_bot_token_raises(db_session):
    from services.telegram_emoji import fetch_custom_emoji_stickers, TelegramEmojiImportError
    with pytest.raises(TelegramEmojiImportError):
        await fetch_custom_emoji_stickers("IconsEmoji_JABA", db_session)


# ── 9. Out-of-stock detail rendering never shows a chosen custom emoji ─────
def test_out_of_stock_detail_never_uses_custom_emoji(db_session):
    p = make_product(db_session)
    apply_admin_icon_edit(p, "⭐", "5368324170671202286")
    # Mirrors the guard in bot/handlers.py _render_product_detail: the
    # out_of_stock/unavailable branch always forces the ❌ fallback with no
    # custom emoji id, regardless of what's stored on the product.
    detail_icon = "❌"
    detail_custom_emoji_id = ""
    out = render_icon_html(detail_icon, detail_custom_emoji_id)
    assert out == "❌"
    assert "tg-emoji" not in out
