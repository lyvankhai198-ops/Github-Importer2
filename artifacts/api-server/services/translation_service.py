"""
translation_service.py — LLM-based Vietnamese -> English translation for
product descriptions, via the Replit AI Integrations Anthropic proxy
(AI_INTEGRATIONS_ANTHROPIC_BASE_URL / AI_INTEGRATIONS_ANTHROPIC_API_KEY).

This replaces raw/fixed-pattern translation as the primary path for
description_en so English shoppers never see leftover Vietnamese words.
The deterministic regex-based translator in services.normalize is kept only
as a last-resort fallback if the AI integration is unavailable or errors —
it must never be the only thing running in normal operation.
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


def translate_description_with_fallback(description: str) -> str | None:
    """
    Best-effort English translation of a product description: tries the LLM
    translator first (natural, complete), falls back to the deterministic
    regex-based translator in services.normalize if the AI integration is
    unavailable or errors, so description_en is never left stale/blank when
    a Vietnamese source is available.
    """
    if not description or not description.strip():
        return description
    translated = translate_description_to_english(description)
    if translated:
        return translated
    from services.normalize import normalize_and_translate_description
    logger.warning("[translation] falling back to rule-based translator")
    return normalize_and_translate_description(description)
