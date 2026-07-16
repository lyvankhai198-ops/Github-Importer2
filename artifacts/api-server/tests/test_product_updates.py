"""
Tests for the "batch 1: bot & products" update:
  - telegram_icon auto-assignment from name keywords + manual-edit lock
  - brand_key grouping/sorting in get_active_products_for_bot
"""
from models import Product, DeliveryMode, SourceType
from services.normalize import auto_assign_emoji, compute_brand_key
from services.product_sync import apply_admin_icon_edit, auto_assign_icon_if_unlocked
from services.product_service import get_active_products_for_bot


# ── auto_assign_emoji ────────────────────────────────────────────────────────

def test_auto_assign_emoji_matches_known_brands():
    assert auto_assign_emoji("Grok Super 1 Year") == "🤖"
    assert auto_assign_emoji("ChatGPT Plus 1 Month") == "🟢"
    assert auto_assign_emoji("OpenAI API Key") == "🟢"  # openai matches before api/key
    assert auto_assign_emoji("Claude Pro") == "🧠"
    assert auto_assign_emoji("Gemini Advanced") == "✨"
    assert auto_assign_emoji("Canva Pro 1 Year") == "🎨"
    assert auto_assign_emoji("CapCut Pro") == "🎬"
    assert auto_assign_emoji("Adobe Creative Cloud") == "🅰️"
    assert auto_assign_emoji("Cursor Pro") == "🖥️"
    assert auto_assign_emoji("Veo 3") == "🎥"
    assert auto_assign_emoji("Kling AI") == "🎞️"
    assert auto_assign_emoji("Microsoft Office 365") == "🪟"
    assert auto_assign_emoji("Binance Pay Account") == "🟡"
    assert auto_assign_emoji("Generic License Code") == "🔑"


def test_auto_assign_emoji_falls_back_to_box():
    assert auto_assign_emoji("Random Unmatched Product") == "📦"
    assert auto_assign_emoji("") == "📦"
    assert auto_assign_emoji(None) == "📦"


# ── icon manual-edit lock ────────────────────────────────────────────────────

def test_admin_icon_lock_prevents_overwrite():
    p = Product(name="Grok Super 1 Year", product_code="P1")
    # Admin manually picks an unrelated icon.
    apply_admin_icon_edit(p, "⭐")
    assert p.telegram_icon == "⭐"
    # Auto-assignment must never overwrite a manually-locked icon.
    changed = auto_assign_icon_if_unlocked(p)
    assert changed is False
    assert p.telegram_icon == "⭐"


def test_blank_icon_gets_auto_assigned_and_stays_unlocked():
    p = Product(name="Grok Super 1 Year", product_code="P2")
    apply_admin_icon_edit(p, "")  # left blank
    changed = auto_assign_icon_if_unlocked(p)
    assert changed is True
    assert p.telegram_icon == "🤖"
    # Re-running (e.g. on next API sync) must not be treated as a manual lock.
    changed_again = auto_assign_icon_if_unlocked(p)
    assert changed_again is False
    assert p.telegram_icon == "🤖"


def test_clearing_icon_unlocks_it_again():
    p = Product(name="Canva Pro", product_code="P3")
    apply_admin_icon_edit(p, "⭐")
    assert p.telegram_icon == "⭐"
    apply_admin_icon_edit(p, "")  # admin clears the field
    auto_assign_icon_if_unlocked(p)
    assert p.telegram_icon == "🎨"


# ── brand_key ────────────────────────────────────────────────────────────────

def test_compute_brand_key_groups_variants():
    assert compute_brand_key("GROK SUPER 1 YEAR") == "grok"
    assert compute_brand_key("Grok Super 3 Months") == "grok"
    assert compute_brand_key("ChatGPT Plus") == "chatgpt"


def test_product_list_groups_same_brand_contiguously(db_session):
    names = [
        "Grok Super 1 Year",
        "ChatGPT Plus 1 Month",
        "Grok Super 3 Months",
        "Canva Pro 1 Year",
        "Grok Basic 1 Month",
    ]
    for i, name in enumerate(names):
        db_session.add(Product(
            name=name,
            product_code=f"CODE-{i}",
            sale_price=10000,
            is_active=True,
            delivery_mode=DeliveryMode.manual_admin,
            source_type=SourceType.manual,
        ))
    db_session.commit()

    result = get_active_products_for_bot(db_session, show_out_of_stock=True)
    ordered_names = [item["product"].name for item in result]

    grok_positions = [i for i, n in enumerate(ordered_names) if n.lower().startswith("grok")]
    # All Grok variants must be contiguous — no other brand interleaved.
    assert grok_positions == list(range(grok_positions[0], grok_positions[0] + len(grok_positions)))
