"""
localization.py — single source of truth for reading a Product's description
in the shopper's chosen language.

Replaces ad-hoc `product.description` / `product.description_en` /
`product.external_description` reads scattered across the bot with one
function so an English shopper can never see leftover Vietnamese text (and
vice versa for an English-sourced product's Vietnamese shoppers).

Translate-once-reuse-many: this only ever calls the translator when there is
no cached translation yet for the requested language (see
services.product_sync.sync_translations for the hash/status bookkeeping that
makes that decision) — normal views just read the already-stored value.
"""
import logging

logger = logging.getLogger(__name__)


def get_localized_product_description(product, language_code: str, db=None, external_description: str | None = None) -> str | None:
    """
    Return the product description in the requested language, formatted for
    Telegram display.

    - language_code == "vi" (or anything else): product.description if the
      product is Vietnamese-sourced, or the cached Vietnamese translation if
      it's English-sourced (product.source_language == "en") — falling back
      to external_description (raw source-API description) only when
      neither exists yet.
    - language_code == "en": product.description_en (or product.description
      itself if the product is already English-sourced). If it is missing
      or stale relative to the current source text (and not locked by an
      admin edit), it is (re)translated on the fly via
      services.product_sync.sync_translations and persisted immediately —
      never falls back to showing the other language's raw text.

    `db` is optional; when given, a freshly generated translation is
    committed immediately so it is never re-translated on the next view.
    Never raises — a translation failure here is recorded on the product
    (translation_status/translation_error) and this still returns whatever
    best-effort text is available (falling back to external_description or
    the other language's text) rather than blocking the product card.
    """
    from services.product_sync import sync_translations
    from services.text_protect import format_description

    src_lang = getattr(product, "source_language", None) or "vi"
    wants_source_lang = (language_code == "vi") == (src_lang == "vi")

    if wants_source_lang:
        text = product.description if src_lang == "vi" else product.description_en
        text = text or external_description
        return format_description(text) if text else None

    # Requesting the non-source language: translate-once-reuse-many.
    target_field = "description_en" if src_lang == "vi" else "description"
    already_cached = bool(getattr(product, target_field, None)) and product.translation_status in ("translated", "manual")
    if not already_cached:
        try:
            changed = sync_translations(product)
            if changed and db is not None:
                db.commit()
        except Exception:
            logger.exception(f"[localization] on-demand translation failed for product {getattr(product, 'id', '?')}")
            if db is not None:
                db.rollback()

    text = getattr(product, target_field, None)
    if not text:
        # No cached translation and translation just failed/unavailable —
        # same-language fallback so the shopper never sees a mixed string.
        source_text = product.description if src_lang == "vi" else product.description_en
        text = source_text or external_description
    return format_description(text) if text else None
