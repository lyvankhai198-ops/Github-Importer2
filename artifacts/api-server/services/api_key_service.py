"""
api_key_service.py — generation/hashing for customer programmatic API keys.

Keys are never stored raw. We use HMAC-SHA256 (keyed with SECRET_KEY as a
pepper) rather than bcrypt because every inbound API request must look the
key up by exact value (indexed equality lookup) — bcrypt's per-hash salt
would make that impossible without iterating every stored hash, which does
not scale and would be far too slow for high-frequency API auth checks.
"""
import hmac
import hashlib
import secrets

from config import SECRET_KEY

_PREFIX = "sk_live_"


def generate_api_key() -> tuple[str, str]:
    """Returns (full_key, key_prefix). full_key is shown to the customer
    exactly once and never persisted; key_prefix is safe to store/display."""
    raw = secrets.token_hex(24)  # 48 hex chars
    full_key = f"{_PREFIX}{raw}"
    key_prefix = full_key[: len(_PREFIX) + 6]  # e.g. "sk_live_ab12cd"
    return full_key, key_prefix


def hash_api_key(raw_key: str) -> str:
    if not raw_key:
        return ""
    return hmac.new(SECRET_KEY.encode(), raw_key.strip().encode(), hashlib.sha256).hexdigest()


def masked_display(key_prefix: str) -> str:
    if not key_prefix:
        return "—"
    return f"{key_prefix}{'•' * 10}"
