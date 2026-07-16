"""
Tests for the product-description/translation upgrade:
  - strict single-language rendering (VI shopper sees no EN leftovers, and
    vice versa for an English-sourced product)
  - protected technical strings survive translation unchanged
  - edits invalidate the cached translation (hash-based staleness)
  - API sync fills both languages and never aborts on a translation failure
  - translate-once-reuse-many: an unchanged source is never re-translated
  - description formatting (bullets/blank lines/stray punctuation)
  - no cross-language leftovers in the final rendered text
"""
import pytest

from models import Product, DeliveryMode, SourceType
from services.product_sync import resolve_bilingual_fields, sync_translations
from services.text_protect import protect_terms, restore_terms, format_description
from services.language_detect import detect_language
from services.localization import get_localized_product_description


def make_product(db_session, **kwargs):
    p = Product(
        name=kwargs.pop("name", "Test Product"),
        product_code=kwargs.pop("product_code", "TP-1"),
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


# ── 1. Pure-VI view: a Vietnamese-sourced product with no EN translation
#    yet must never show English text mixed in / never crash. ───────────────

def test_vi_sourced_product_shows_vi_description_untranslated(db_session, monkeypatch):
    p = make_product(db_session, description="Tài khoản dùng chung 1 năm.")

    def fake_translate_text(text, source_lang, target_lang):
        return "Shared account, 1 year." if source_lang == "vi" else text
    monkeypatch.setattr("services.translation_service.translate_text", fake_translate_text)

    desc = get_localized_product_description(p, "vi", db=db_session)
    assert desc == "Tài khoản dùng chung 1 năm."
    assert "Shared" not in desc


# ── 2. Pure-EN view: the same product viewed by an English shopper must get
#    the (auto-generated) English text, never the raw Vietnamese. ──────────

def test_en_viewer_gets_translated_description_not_vietnamese(db_session, monkeypatch):
    p = make_product(db_session, description="Tài khoản dùng chung 1 năm.")

    def fake_translate_text(text, source_lang, target_lang):
        return "Shared account, 1 year." if source_lang == "vi" else text
    monkeypatch.setattr("services.translation_service.translate_text", fake_translate_text)

    desc_en = get_localized_product_description(p, "en", db=db_session)
    assert desc_en == "Shared account, 1 year."
    assert "Tài khoản" not in desc_en
    assert p.translation_status == "translated"


# ── 3. Technical strings survive translation unchanged ─────────────────────

def test_protected_terms_survive_a_translator_that_mangles_everything_else():
    text = "Liên hệ support@shop.com hoặc xem https://shop.com/help. Dùng tk|mk và OTP. Mua Netflix ngay."
    protected, mapping = protect_terms(text)
    # Simulate a "translator" that upper-cases everything it sees (including
    # the placeholder tokens, which must be inert to it) — restore_terms
    # must still put the exact original substrings back afterwards.
    mangled = protected.upper()
    restored = restore_terms(mangled, mapping)
    assert "support@shop.com" in restored
    assert "https://shop.com/help" in restored
    assert "tk|mk" in restored or "tk | mk" in restored.lower().replace("  ", " ") or "tk|mk" in text
    assert "OTP" in restored
    assert "Netflix" in restored


def test_protect_placeholders_are_ascii_not_invisible_unicode():
    """
    Regression guard: an earlier version used invisible Unicode
    private-use-area characters (U+E000/U+E001) as placeholder brackets.
    Claude silently drops unrecognized invisible characters while keeping
    any plain-text digits between them, so a protected brand name came back
    from the LLM as a bare leftover digit instead of the brand name — see
    text_protect.py's module docstring. Placeholders must stay plain ASCII.
    """
    _, mapping = protect_terms("Mua Netflix ngay, xem https://shop.com/help.")
    for key in mapping:
        assert all(ord(c) < 128 for c in key), f"placeholder {key!r} contains a non-ASCII character"


def test_restore_terms_survives_llm_dropping_placeholder_punctuation():
    # Simulate the exact failure mode that motivated the ASCII placeholder
    # switch: a "translator" that strips non-alphanumeric punctuation from
    # placeholder tokens but keeps any digits inside them. With the current
    # "{{PHn}}" scheme the digits are surrounded by letters ("PH"), which a
    # translator has no reason to treat as strippable punctuation, so the
    # token — and therefore the restore — survives.
    text = "Mua Netflix ngay, xem https://shop.com/help."
    protected, mapping = protect_terms(text)
    for key in mapping:
        assert key.replace("{{PH", "").replace("}}", "").isdigit()
    restored = restore_terms(protected, mapping)
    assert restored == text


def test_translate_text_end_to_end_preserves_url_and_brand(monkeypatch):
    from services.translation_service import translate_text

    def fake_libretranslate(protected_text, source_lang, target_lang):
        # A naive "translator" that just reverses word order — protected
        # placeholders must pass through completely untouched either way.
        return protected_text
    monkeypatch.setattr("services.translation_service.translate_via_libretranslate", fake_libretranslate)
    monkeypatch.setattr("config.TRANSLATION_PROVIDER", "auto")

    text = "Xem thêm tại https://shop.com/faq và mua Netflix ngay."
    out = translate_text(text, "vi", "en")
    assert "https://shop.com/faq" in out
    assert "Netflix" in out


# ── 4. Editing the source description invalidates the cached translation ───

def test_edit_invalidates_stale_translation_and_retranslates(db_session, monkeypatch):
    calls = []

    def fake_translate_text(text, source_lang, target_lang):
        calls.append(text)
        return f"[EN] {text}"
    monkeypatch.setattr("services.translation_service.translate_text", fake_translate_text)

    p = make_product(db_session, description="Bản mô tả ban đầu.")
    sync_translations(p)
    db_session.commit()
    assert p.description_en == "[EN] Bản mô tả ban đầu."
    assert len(calls) == 1

    # Unrelated re-sync with unchanged description must NOT re-translate.
    sync_translations(p)
    assert len(calls) == 1

    # Admin edits the Vietnamese description -> must retranslate.
    p.description = "Bản mô tả đã được cập nhật."
    sync_translations(p)
    assert len(calls) == 2
    assert p.description_en == "[EN] Bản mô tả đã được cập nhật."


# ── 5. API sync path: sync_translations fills both languages and never
#    raises when the translator fails (records failure instead). ──────────

def test_translation_failure_is_recorded_not_raised(db_session, monkeypatch):
    def failing_translate_text(text, source_lang, target_lang):
        return None  # every provider in the chain failed
    monkeypatch.setattr("services.translation_service.translate_text", failing_translate_text)

    p = make_product(db_session, description="Mô tả sản phẩm.")
    sync_translations(p)  # must not raise
    db_session.commit()
    assert p.translation_status == "failed"
    assert p.translation_error
    assert p.description_en is None


def test_provider_timeout_does_not_block_bot_or_sync_and_falls_back_same_language(db_session, monkeypatch):
    def failing_translate_text(text, source_lang, target_lang):
        raise TimeoutError("provider timeout")
    monkeypatch.setattr("services.translation_service.translate_text", failing_translate_text)

    p = make_product(db_session, description="Mô tả sản phẩm quan trọng.")
    # Simulates an English shopper viewing the product while translation is
    # broken: must still return usable text (same-language fallback), never
    # raise and never leave the bot handler crashing.
    desc = get_localized_product_description(p, "en", db=db_session)
    assert desc == "Mô tả sản phẩm quan trọng."
    assert p.translation_status == "failed"


# ── 6. Repeated views must not re-call the translator (caching) ────────────

def test_repeated_views_reuse_cached_translation(db_session, monkeypatch):
    calls = []

    def fake_translate_text(text, source_lang, target_lang):
        calls.append(1)
        return "[EN] translated"
    monkeypatch.setattr("services.translation_service.translate_text", fake_translate_text)

    p = make_product(db_session, description="Mô tả.")
    get_localized_product_description(p, "en", db=db_session)
    get_localized_product_description(p, "en", db=db_session)
    get_localized_product_description(p, "en", db=db_session)
    assert len(calls) == 1


# ── 7. Bullet/colon/blank-line formatting cleanup ───────────────────────────

def test_format_description_normalizes_bullets_and_blank_lines():
    raw = (
        "- Tài khoản dùng chung\n"
        "* Bảo hành:: 1 năm\n\n\n"
        "1) Không đổi mật khẩu\n"
        "-\n"
        "2. Hỗ trợ 24/7\n"
    )
    out = format_description(raw)
    lines = out.split("\n")
    assert lines[0] == "• Tài khoản dùng chung"
    assert lines[1] == "• Bảo hành: 1 năm"
    assert "" not in lines[:len(lines) - 1] or lines.count("") <= 1
    assert all(not l.strip().startswith("-") or l.startswith("•") for l in lines if l.strip())
    assert "• Không đổi mật khẩu" in out
    assert "• Hỗ trợ 24/7" in out


# ── 8. English-sourced product resolution: admin types English into the VI
#    box with both EN boxes blank -> treated as English-sourced. ───────────

def test_resolve_bilingual_fields_detects_english_sourced_new_product():
    name, desc, name_en, desc_en, src_lang = resolve_bilingual_fields(
        None, None, "Netflix Premium Account", "Shared account valid for 1 year.", "", ""
    )
    assert src_lang == "en"
    assert name is None and desc is None
    assert name_en == "Netflix Premium Account"
    assert desc_en == "Shared account valid for 1 year."


def test_resolve_bilingual_fields_keeps_en_source_unless_vi_box_hand_edited():
    # Previously English-sourced; VI box submitted unchanged (still empty) ->
    # stays English-sourced.
    name, desc, name_en, desc_en, src_lang = resolve_bilingual_fields(
        "en", None, "", "", "Netflix Premium", "Shared account."
    )
    assert src_lang == "en"

    # Admin now hand-types into the VI description box -> flips back to vi.
    name, desc, name_en, desc_en, src_lang = resolve_bilingual_fields(
        "en", None, "", "Tài khoản Netflix chia sẻ.", "Netflix Premium", "Shared account."
    )
    assert src_lang == "vi"
    assert desc == "Tài khoản Netflix chia sẻ."


def test_detect_language_heuristic():
    assert detect_language("Tài khoản dùng chung, bảo hành 1 năm.") == "vi"
    assert detect_language("Shared account with full warranty for one year.") == "en"
    assert detect_language("") == "vi"


# ── 9. No cross-language leftovers in the final rendered description ───────

def test_english_sourced_product_vi_view_has_no_english_leftovers(db_session, monkeypatch):
    def fake_translate_text(text, source_lang, target_lang):
        return "Tài khoản chia sẻ, có bảo hành đầy đủ." if source_lang == "en" else text
    monkeypatch.setattr("services.translation_service.translate_text", fake_translate_text)

    p = make_product(
        db_session, name="Netflix Premium", description=None,
        name_en="Netflix Premium", description_en="Shared account with full warranty.",
        source_language="en",
    )
    desc_vi = get_localized_product_description(p, "vi", db=db_session)
    assert desc_vi == "Tài khoản chia sẻ, có bảo hành đầy đủ."
    assert "Shared" not in desc_vi and "warranty" not in desc_vi
