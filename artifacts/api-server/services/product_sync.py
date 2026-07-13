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
import logging

from services.normalize import translate_product_name_to_en
from services.translation_service import translate_description_with_fallback

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


def ensure_en_fields(product) -> bool:
    """
    Keep Product.name_en/description_en auto-translated from the
    Vietnamese name/description, but only for fields NOT locked by an
    admin edit. name_en is cheap (regex table) and re-derived every call so
    an improved translator automatically fixes previously auto-generated
    text. description_en goes through the LLM translator (see
    translation_service), which is not free — it is only (re)generated when
    it is missing or the Vietnamese source has actually changed since the
    last translation (tracked via description_en_source), so a periodic API
    sync never re-translates unchanged descriptions. An admin edit is the
    only thing that freezes a field (see apply_admin_en_edit). Safe to call
    on every sync. Returns True if anything changed.
    """
    changed = False
    if not product.name_en_locked and product.name:
        translated = translate_product_name_to_en(product.name)
        if translated and translated != product.name_en:
            product.name_en = translated
            changed = True
    if not product.description_en_locked and product.description:
        needs_translation = (
            not product.description_en
            or product.description != (product.description_en_source or None)
        )
        if needs_translation:
            translated = translate_description_with_fallback(product.description)
            if translated and translated != product.description_en:
                product.description_en = translated
                product.description_en_source = product.description
                changed = True
    return changed


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
