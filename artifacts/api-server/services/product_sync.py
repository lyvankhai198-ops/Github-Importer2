"""
Manual-edit-safe propagation of API source data onto Product rows.

API-linked products (source_type=api, delivery_mode=api_auto) keep
auto-updating their image/description/warranty/duration from the linked
ApiProduct on every sync — UNLESS an admin has manually edited that specific
field via the dashboard, in which case it is frozen and must never be
silently overwritten again. Stock is not tracked here: it is always
computed live from ProductSource/ApiProduct, never stored/copied onto
Product, so there is nothing to "protect" for it.
"""
import hashlib
import logging
from datetime import datetime

from services.normalize import translate_product_name_to_en, translate_product_name_to_vi
from services.language_detect import detect_language

logger = logging.getLogger(__name__)

# The only Product columns this task protects. Anything not in this set
# (name, pricing, etc.) is out of scope and is never auto-synced from source.
TRACKED_SYNC_FIELDS = {"description", "image_path", "warranty", "duration"}


def parse_edited_fields(raw: str | None) -> set:
    if not raw:
        return set()
    return {f.strip() for f in raw.split(",") if f.strip()}


def serialize_edited_fields(fields: set) -> str | None:
    return ",".join(sorted(fields)) if fields else None


def mark_fields_edited(product, changed_field_names: set):
    """Merge newly-changed field names into product.manually_edited_fields."""
    current = parse_edited_fields(product.manually_edited_fields)
    current |= (changed_field_names & TRACKED_SYNC_FIELDS)
    product.manually_edited_fields = serialize_edited_fields(current)


def apply_admin_edit(product, new_values: dict) -> set:
    """
    Apply admin-submitted values for tracked fields onto `product`, marking
    any field whose value actually changed as manually edited so future API
    syncs skip it. Returns the set of field names that changed this call.
    `new_values` keys must be a subset of TRACKED_SYNC_FIELDS.
    """
    changed = set()
    for field, new_val in new_values.items():
        if field not in TRACKED_SYNC_FIELDS:
            continue
        old_val = getattr(product, field, None) or ""
        normalized_new = (new_val or "").strip() if isinstance(new_val, str) else new_val
        if (normalized_new or "") != old_val:
            changed.add(field)
        setattr(product, field, normalized_new or None)
    if changed:
        mark_fields_edited(product, changed)
    return changed


def apply_admin_en_edit(product, name_en: str | None, description_en: str | None) -> set:
    """
    Apply admin-submitted name_en/description_en, freezing whichever one
    actually changed (name_en_locked / description_en_locked) so future
    auto-translation (ensure_en_fields) never overwrites it again. Clearing
    a field back to blank unlocks it, allowing auto-translation to fill it
    in again.
    """
    changed = set()
    new_name_en = (name_en or "").strip() or None
    if new_name_en != (product.name_en or None):
        changed.add("name_en")
        product.name_en = new_name_en
        product.name_en_locked = bool(new_name_en)
    new_desc_en = (description_en or "").strip() or None
    if new_desc_en != (product.description_en or None):
        changed.add("description_en")
        product.description_en = new_desc_en
        product.description_en_locked = bool(new_desc_en)
    return changed


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def resolve_bilingual_fields(prior_source_language, prior_description, name, description, name_en, description_en):
    """
    Decide which language box the admin actually supplied given the raw
    submitted form values, returning
    (name, description, name_en, description_en, source_language) with the
    swap applied when needed:

    - If the product was previously English-sourced (prior_source_language
      == "en"), it stays that way UNLESS the admin's submitted Vietnamese
      `description` actually differs from what's currently stored there —
      an explicit hand-edit of the Vietnamese box switches the source back
      to "vi".
    - Otherwise (new product, or previously Vietnamese-sourced): if the
      admin typed English-looking text into the Vietnamese name/description
      box and left both English boxes blank, treat that as an
      English-sourced product — move the text into the English slot and
      clear the Vietnamese slot so translation regenerates it. Any other
      combination (including both sides explicitly filled) keeps the
      normal Vietnamese-source flow unchanged.
    """
    name = (name or "").strip() or None
    description = (description or "").strip() or None
    name_en = (name_en or "").strip() or None
    description_en = (description_en or "").strip() or None

    if prior_source_language == "en":
        if description is not None and description != (prior_description or None):
            return name, description, name_en, description_en, "vi"
        return None, None, name_en, description_en, "en"

    probe = description or name or ""
    lang = detect_language(probe) if probe else "vi"
    if lang == "en" and not description_en and not name_en:
        return None, None, name, description, "en"
    return name, description, name_en, description_en, "vi"


def sync_translations(product) -> bool:
    """
    Keep the "other" language in sync with product.source_language
    ("vi" default, or "en" when the admin/API source supplied English
    text first — see resolve_bilingual_fields). Replaces the old
    one-directional ensure_en_fields().

    - name/name_en: cheap, deterministic (shorthand table), re-derived
      every call in whichever direction is NOT the source — never blocks,
      never calls a network translator.
    - description/description_en: the expensive, semantically-rich side.
      Only (re)translated when the source text's hash actually changed
      since the last successful translation (translation_source_hash), so
      an unchanged description never re-calls the translator. Frozen by
      description_en_locked when source_language == "vi" (an admin
      hand-edit of description_en). Failures are recorded on the product
      (translation_status/translation_error) and never raise — callers
      must never let a translation failure block a save or an API sync.

    Returns True if anything on `product` changed.
    """
    changed = False
    src_lang = product.source_language or "vi"

    # ---- name (cheap, deterministic, always safe) ----
    if src_lang == "vi":
        if not product.name_en_locked and product.name:
            translated = translate_product_name_to_en(product.name)
            if translated and translated != product.name_en:
                product.name_en = translated
                changed = True
    else:
        if product.name_en:
            translated = translate_product_name_to_vi(product.name_en)
            if translated and translated != product.name:
                product.name = translated
                changed = True

    # ---- description (expensive, hash-tracked, status-tracked) ----
    if src_lang == "vi":
        source_text = product.description
        locked = bool(product.description_en_locked)
    else:
        source_text = product.description_en
        locked = False  # the vi side has no admin-lock flag of its own

    if locked:
        if product.translation_status != "manual":
            product.translation_status = "manual"
            changed = True
        return changed

    if not source_text:
        return changed

    current_hash = _hash_text(source_text)
    target_has_value = (
        product.description_en if src_lang == "vi" else product.description
    )
    already_current = (
        product.translation_source_hash == current_hash
        and product.translation_status == "translated"
        and bool(target_has_value)
    )
    if already_current:
        return changed

    from services.translation_service import translate_text
    from services.text_protect import format_description

    target_lang = "en" if src_lang == "vi" else "vi"
    try:
        translated = translate_text(source_text, src_lang, target_lang)
        if not translated:
            raise RuntimeError("translator returned no result")
        translated = format_description(translated)
        if src_lang == "vi":
            product.description_en = translated
            product.description_en_source = source_text
        else:
            product.description = translated
        product.translation_source_hash = current_hash
        product.translated_at = datetime.utcnow()
        product.translation_status = "translated"
        product.translation_error = None
    except Exception as e:
        product.translation_status = "failed"
        product.translation_error = str(e)[:500]
        logger.error(f"[translation] product {getattr(product, 'id', '?')} ({src_lang}->{target_lang}) failed: {e}")
    changed = True
    return changed


def ensure_en_fields(product) -> bool:
    """Backward-compatible alias for sync_translations() — kept so any
    caller that imports the old name keeps working unchanged."""
    return sync_translations(product)


def apply_admin_icon_edit(product, new_icon: str | None, new_custom_emoji_id: str | None = None) -> bool:
    """
    Apply an admin-submitted icon selection: `new_icon` is the plain
    fallback emoji character (shown to non-Premium users and anywhere HTML
    entities aren't supported, e.g. inline keyboard button text) and
    `new_custom_emoji_id` is the optional Telegram custom emoji document ID
    chosen from the icon library (services/telegram_emoji.py +
    routers/emoji_icons.py) — when set, the bot renders
    <tg-emoji emoji-id="..."> with this as the fallback text.

    The two fields are locked/unlocked together as a single unit: setting
    either one locks both against future keyword-based auto-assignment
    (auto_assign_icon_if_unlocked below); clearing both back to blank
    unlocks auto-assignment again on the next save/sync.
    Returns True if either stored value changed.
    """
    edited = parse_edited_fields(product.manually_edited_fields)
    new_icon = (new_icon or "").strip() or None
    new_custom_emoji_id = (new_custom_emoji_id or "").strip() or None
    changed = (
        new_icon != (product.telegram_icon or None)
        or new_custom_emoji_id != (getattr(product, "telegram_custom_emoji_id", None) or None)
    )
    product.telegram_icon = new_icon
    product.telegram_custom_emoji_id = new_custom_emoji_id
    if new_icon or new_custom_emoji_id:
        edited.add("telegram_icon")
    else:
        edited.discard("telegram_icon")
    product.manually_edited_fields = serialize_edited_fields(edited)
    return changed


def auto_assign_icon_if_unlocked(product) -> bool:
    """
    Fill product.telegram_icon from the name-keyword mapping unless the
    admin has manually chosen one (locked via apply_admin_icon_edit above).
    Safe to call on every product save/API sync — never overwrites a
    manually-set emoji. Returns True if the icon changed.
    """
    from services.normalize import auto_assign_emoji
    edited = parse_edited_fields(product.manually_edited_fields)
    if "telegram_icon" in edited:
        return False
    assigned = auto_assign_emoji(product.name)
    if assigned != (product.telegram_icon or None):
        product.telegram_icon = assigned
        return True
    return False


def sync_product_from_api_product(product, ap) -> bool:
    """
    Copy image/description/warranty/duration from `ap` (ApiProduct) onto
    `product` (Product), skipping any field the admin has manually edited.
    Returns True if anything actually changed.
    """
    edited = parse_edited_fields(product.manually_edited_fields)
    source_map = {
        "description": ap.external_description or "",
        "image_path": ap.external_image_url or None,
        "warranty": ap.external_warranty or None,
        "duration": ap.external_duration or None,
    }
    changed = False
    for field, new_val in source_map.items():
        if field in edited:
            continue  # admin-protected — never overwrite
        current_val = getattr(product, field, None)
        if field == "description":
            new_val = new_val or ""
        if current_val != new_val:
            setattr(product, field, new_val)
            changed = True
    return changed
