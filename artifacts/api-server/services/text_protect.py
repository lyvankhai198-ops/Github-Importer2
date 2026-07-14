"""
text_protect.py — protects technical/brand substrings from mistranslation,
and formats a description into clean bulleted Telegram text.

protect_terms()/restore_terms() swap out URLs, emails, "tk|mk"-style
shorthand, 2FA/OTP, warranty codes (BHF/KBH), long API-key-looking tokens,
and known brand names for private-use-area placeholder tokens before the
text is handed to any translator (LibreTranslate or the LLM), then restore
the exact original substrings afterwards — so none of them can be
mistranslated or reworded, regardless of which provider ran.

format_description() runs at render time (bot/handlers.py) on both
languages' stored descriptions: it strips excess blank lines, normalizes
"-"/"*"/numbered/dotted markers into "•" bullets, drops now-empty bullets,
and collapses stray repeated punctuation — never touching the actual words.
"""
import re

# Private-use-area brackets — vanishingly unlikely to appear in real product
# text or survive being echoed back unchanged by a translation provider.
_PH_OPEN = "\uE000"
_PH_CLOSE = "\uE001"

_BRAND_NAMES = [
    "ChatGPT Plus", "ChatGPT", "OpenAI", "Grok", "Gemini", "Claude", "Canva",
    "CapCut", "Adobe", "Cursor", "Veo", "Kling", "Microsoft", "Netflix",
    "Spotify", "YouTube", "Binance", "Google", "TikTok", "Zalo", "Facebook",
    "Instagram", "Twitter", "Midjourney", "Perplexity", "Notion",
]

_PROTECT_PATTERNS = [
    re.compile(r"https?://\S+"),                              # URLs
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),               # emails
    re.compile(r"tk\s*\|\s*mk", re.IGNORECASE),                # tk|mk shorthand
    re.compile(r"\b2FA\b", re.IGNORECASE),
    re.compile(r"\bOTP\b", re.IGNORECASE),
    re.compile(r"\bBHF\b", re.IGNORECASE),
    re.compile(r"\bKBH\b", re.IGNORECASE),
] + [re.compile(re.escape(b), re.IGNORECASE) for b in _BRAND_NAMES] + [
    re.compile(r"\b[A-Za-z0-9]{16,}\b"),                        # long codes/keys
]


def protect_terms(text: str) -> tuple[str, dict]:
    """Replace protected substrings with placeholder tokens. Returns
    (protected_text, mapping) — pass mapping to restore_terms() afterwards."""
    if not text:
        return text, {}
    mapping: dict[str, str] = {}
    counter = [0]

    def _sub(m):
        key = f"{_PH_OPEN}{counter[0]}{_PH_CLOSE}"
        mapping[key] = m.group(0)
        counter[0] += 1
        return key

    protected = text
    for pattern in _PROTECT_PATTERNS:
        protected = pattern.sub(_sub, protected)
    return protected, mapping


def restore_terms(text: str, mapping: dict) -> str:
    """Reverse protect_terms(): swap placeholder tokens back to the exact
    original substrings. Safe to call with an empty/None mapping."""
    if not text or not mapping:
        return text
    for key, val in mapping.items():
        text = text.replace(key, val)
    return text


# ── Description formatting (bullets/blank lines/colons) ────────────────────

_BULLET_LEAD_RE = re.compile(r"^\s*(?:[-*–—•]|\d+[.)])\s*")
_REPEAT_PUNCT_RE = re.compile(r"([:\-–—]){2,}")


def format_description(text: str | None) -> str | None:
    """
    Clean up a stored description for Telegram display:
    - collapses runs of blank lines into a single blank line
    - normalizes any "-", "*", "–", "—", "•", or numbered/dotted ("1.", "2)")
      list marker into a single "• " bullet
    - drops bullets that end up with no content
    - collapses stray repeated punctuation (e.g. "::", "--")
    - trims leading/trailing blank lines
    Never touches word content/order — safe to call on already-clean text.
    """
    if not text:
        return text
    lines = [l.rstrip() for l in text.split("\n")]
    out: list[str] = []
    blank_pending = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if out:
                blank_pending = True
            continue
        if blank_pending:
            out.append("")
            blank_pending = False
        if _BULLET_LEAD_RE.match(line):
            content = _BULLET_LEAD_RE.sub("", line).strip()
            if not content:
                continue
            out.append(f"• {_REPEAT_PUNCT_RE.sub(lambda m: m.group(1), content)}")
        else:
            out.append(_REPEAT_PUNCT_RE.sub(lambda m: m.group(1), stripped))
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)
