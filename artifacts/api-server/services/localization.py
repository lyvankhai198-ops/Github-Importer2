"""
localization.py — single source of truth for reading a Product's description
in the shopper's chosen language.

Replaces ad-hoc `product.description` / `product.description_en` /
`product.external_description` reads scattered across the bot with one
function so an English shopper can never see leftover Vietnamese text.
"""
import logging

logger = logging.getLogger(__name__)


def _description_needs_translation(product, vi_source: str | None) -> bool:
    """True if description_en is missing or stale relative to the current
    Vietnamese source, and nothing has locked it (admin hand-edit)."""
    if product.description_en_locked:
        return False
    if not vi_source:
        return False
    if not product.description_en:
        return True
    return vi_source != (product.description_en_source or None)


def get_localized_product_description(product, language_code: str, db=None, external_description: str | None = None) -> str | None:
    """
    Return the product description in the requested language.

    - language_code == "vi" (or anything else): product.description, falling
      back to external_description (source-API description) if blank.
    - language_code == "en": product.description_en. If it is missing or the
      Vietnamese source changed since it was last generated (and it isn't
      locked by an admin edit), it is (re)translated on the fly and
      persisted to the database — never falls back to showing the raw
      Vietnamese text to an English shopper.

    `db` is optional; when given, a freshly generated translation is
    committed immediately so it is never re-translated on the next view.
    """
    vi_source = product.description or external_description

    if language_code != "en":
        return vi_source

    if _description_needs_translation(product, vi_source):
        from services.translation_service import translate_description_with_fallback
        translated = translate_description_with_fallback(vi_source)
        if translated:
            product.description_en = translated
            product.description_en_source = vi_source
            if db is not None:
                try:
                    db.commit()
                except Exception:
                    logger.exception("[localization] failed to persist description_en")
                    db.rollback()

    return product.description_en or None
