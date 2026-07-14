"""
translation_service.py — pluggable product-description translator.

Provider chain (see config.TRANSLATION_PROVIDER):
  1. LibreTranslate (if LIBRETRANSLATE_URL is configured) — 2 retries,
     config-driven timeout, never raises.
  2. The Anthropic LLM translator below (existing, unchanged prompt/model),
     via the Replit AI Integrations proxy
     (AI_INTEGRATIONS_ANTHROPIC_BASE_URL / AI_INTEGRATIONS_ANTHROPIC_API_KEY).
  3. The deterministic regex/dictionary translator in services.normalize
     (vi->en only) as a final fallback so description_en is never left
     blank when a Vietnamese source is available.

Every step protects technical strings (URLs, emails, brand names, 2FA/OTP,
tk|mk shorthand, etc. — see services.text_protect) so they survive
translation unchanged regardless of which provider actually ran, and never
raises — callers (services.product_sync) are responsible for recording
success/failure on the product and alerting the admin.
"""
import os
import logging

import httpx

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5"
_ANTHROPIC_VERSION = "2023-06-01"

_SYSTEM_PROMPT = (
    "You translate Vietnamese e-commerce product descriptions into natural, "
    "fluent English for an online shop selling digital subscription accounts "
    "and licenses (streaming, AI tools, software, etc.).\n\n"
    "Rules — follow ALL of them exactly:\n"
    "1. Translate every Vietnamese word or phrase into natural English. Do not "
    "leave a single Vietnamese word in the output — this includes short words "
    "like \"nhận\", \"hạn\", \"mua về\", \"sử dụng\", \"trường hợp\", \"giờ\", "
    "\"tháng\", \"kể từ lúc mua\", \"hướng dẫn sử dụng\", \"không cần\".\n"
    "2. If a line is already in English, keep it as-is (only fix obvious typos).\n"
    "3. Preserve brand/product names exactly as written (e.g. Gemini, Netflix, "
    "ChatGPT, ChatGPT Plus).\n"
    "4. Preserve every URL exactly, character-for-character, including any "
    "surrounding label such as \"User guide:\".\n"
    "5. Preserve numbers exactly, translating only the surrounding Vietnamese "
    "unit words naturally (e.g. \"12 giờ\" -> \"12 hours\", \"3 tháng\" -> "
    "\"3 months\", \"18 Months\" stays \"18 Months\").\n"
    "6. Preserve the exact line breaks and bullet characters (•, -, *) of the "
    "original text. Translate line by line — never merge, reorder, or drop a "
    "line.\n"
    "7. Rephrase awkward literal translations into natural English sentences a "
    "native speaker would write (not a word-for-word calque).\n"
    "8. Output ONLY the translated description text — no commentary, no "
    "notes, no headers, no surrounding quotation marks."
)


def _client_config():
    base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
    api_key = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")
    if not base_url or not api_key:
        return None
    return base_url, api_key


def translate_description_to_english(description: str) -> str | None:
    """
    Translate a Vietnamese (or mixed VI/EN) product description into a
    natural, complete English description via the Anthropic AI integration.

    Returns None if the integration is unavailable or the call fails —
    callers must fall back to the deterministic rule-based translator in
    that case rather than leaving the field blank or raising.
    """
    if not description or not description.strip():
        return description
    cfg = _client_config()
    if not cfg:
        logger.warning("[translation] AI integration not configured; skipping LLM translation")
        return None
    base_url, api_key = cfg
    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": _ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": _MODEL,
                "max_tokens": 2048,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": description}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        return text or None
    except Exception:
        logger.exception("[translation] LLM description translation failed")
        return None


_SYSTEM_PROMPT_EN_TO_VI = (
    "You translate English e-commerce product descriptions into natural, "
    "fluent Vietnamese for an online shop selling digital subscription "
    "accounts and licenses (streaming, AI tools, software, etc.).\n\n"
    "Rules — follow ALL of them exactly:\n"
    "1. Translate every English word or phrase into natural Vietnamese. Do "
    "not leave English sentences untranslated.\n"
    "2. If a line is already in Vietnamese, keep it as-is (only fix obvious typos).\n"
    "3. Preserve brand/product names exactly as written (e.g. Gemini, Netflix, "
    "ChatGPT, ChatGPT Plus).\n"
    "4. Preserve every URL exactly, character-for-character, including any "
    "surrounding label.\n"
    "5. Preserve numbers exactly, translating only the surrounding English "
    "unit words naturally (e.g. \"12 hours\" -> \"12 giờ\", \"3 months\" -> "
    "\"3 tháng\").\n"
    "6. Preserve the exact line breaks and bullet characters (•, -, *) of the "
    "original text. Translate line by line — never merge, reorder, or drop a "
    "line.\n"
    "7. Rephrase awkward literal translations into natural Vietnamese a "
    "native speaker would write (not a word-for-word calque).\n"
    "8. Output ONLY the translated description text — no commentary, no "
    "notes, no headers, no surrounding quotation marks."
)


def translate_description_to_vietnamese(description: str) -> str | None:
    """
    Translate an English (or mixed EN/VI) product description into natural
    Vietnamese via the Anthropic AI integration — the reverse-direction
    counterpart of translate_description_to_english. Returns None if the
    integration is unavailable or the call fails.
    """
    if not description or not description.strip():
        return description
    cfg = _client_config()
    if not cfg:
        logger.warning("[translation] AI integration not configured; skipping LLM translation")
        return None
    base_url, api_key = cfg
    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": _ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": _MODEL,
                "max_tokens": 2048,
                "system": _SYSTEM_PROMPT_EN_TO_VI,
                "messages": [{"role": "user", "content": description}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        return text or None
    except Exception:
        logger.exception("[translation] LLM description translation (en->vi) failed")
        return None


def _libretranslate_config():
    from config import LIBRETRANSLATE_URL, LIBRETRANSLATE_API_KEY, TRANSLATION_TIMEOUT_SECONDS
    if not LIBRETRANSLATE_URL:
        return None
    return LIBRETRANSLATE_URL, LIBRETRANSLATE_API_KEY, TRANSLATION_TIMEOUT_SECONDS


def translate_via_libretranslate(text: str, source_lang: str, target_lang: str) -> str | None:
    """
    Translate `text` via a self-hosted/managed LibreTranslate instance
    (POST {url}/translate). Retries once on failure with a short backoff,
    logs clearly, and never raises — returns None so callers fall through
    to the LLM/regex translators instead of blocking on a dead provider.
    """
    cfg = _libretranslate_config()
    if not cfg:
        return None
    if not text or not text.strip():
        return text
    url, api_key, timeout = cfg
    payload = {"q": text, "source": source_lang, "target": target_lang, "format": "text"}
    if api_key:
        payload["api_key"] = api_key
    last_err = None
    for attempt in range(1, 3):
        try:
            resp = httpx.post(f"{url.rstrip('/')}/translate", json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            translated = (data.get("translatedText") or "").strip()
            return translated or None
        except Exception as e:
            last_err = e
            logger.warning(f"[translation] LibreTranslate attempt {attempt}/2 failed: {e}")
    logger.error(f"[translation] LibreTranslate unavailable after retries ({source_lang}->{target_lang}): {last_err}")
    return None


def translate_text(text: str, source_lang: str, target_lang: str) -> str | None:
    """
    Unified translation entry point for any vi<->en direction. Protects
    technical/brand strings, then tries LibreTranslate (if configured) ->
    the Anthropic LLM translator -> (vi->en only) the deterministic
    regex/dictionary translator. Returns None only if every available
    provider failed/is unconfigured — callers must treat that as a
    recorded failure, never as "leave the field blank" or "show a mixed
    language string".
    """
    if not text or not text.strip():
        return text
    from services.text_protect import protect_terms, restore_terms
    from config import TRANSLATION_PROVIDER

    protected, mapping = protect_terms(text)
    provider = (TRANSLATION_PROVIDER or "auto").lower()
    translated = None

    if provider in ("auto", "libretranslate"):
        translated = translate_via_libretranslate(protected, source_lang, target_lang)

    if not translated and provider in ("auto", "llm", "libretranslate"):
        if target_lang == "en":
            translated = translate_description_to_english(protected)
        else:
            translated = translate_description_to_vietnamese(protected)

    if not translated and target_lang == "en":
        from services.normalize import normalize_and_translate_description
        logger.warning("[translation] falling back to rule-based translator")
        translated = normalize_and_translate_description(protected)

    if not translated:
        return None
    return restore_terms(translated, mapping)


def translate_description_with_fallback(description: str) -> str | None:
    """
    Best-effort English translation of a product description (vi->en) —
    kept for existing callers (services.localization, routers.products
    preview endpoint). Delegates to translate_text().
    """
    return translate_text(description, "vi", "en")


def translate_description_to_vi_with_fallback(description: str) -> str | None:
    """Best-effort Vietnamese translation of a product description (en->vi).
    Delegates to translate_text()."""
    return translate_text(description, "en", "vi")
