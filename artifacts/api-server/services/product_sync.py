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
