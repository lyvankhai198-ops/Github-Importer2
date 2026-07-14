import os
import base64
from pathlib import Path
from cryptography.fernet import Fernet

_fernet = None

# Fallback used only when the ENCRYPTION_KEY env var isn't set. Without this,
# every process restart used to generate a brand-new random key in memory
# (never saved anywhere), which silently broke decrypt() for every secret
# already stored in the database — Telegram bot token, supplier API keys,
# SePay/payment credentials — forcing the admin to re-enter everything after
# every restart/redeploy. Persisting the generated key to disk here means it
# survives restarts even if ENCRYPTION_KEY was never configured. Setting the
# real env var is still the recommended, more robust option (see warning
# below) — this file is only a safety net.
_KEY_FILE = Path(__file__).resolve().parent / ".encryption_key"


def _load_or_create_persisted_key() -> str:
    if _KEY_FILE.exists():
        existing = _KEY_FILE.read_text().strip()
        if existing:
            return existing
    key = Fernet.generate_key().decode()
    try:
        _KEY_FILE.write_text(key)
        os.chmod(_KEY_FILE, 0o600)
    except Exception as e:
        print(f"[WARNING] Could not persist generated ENCRYPTION_KEY to {_KEY_FILE}: {e}")
        print("[WARNING] Every restart will now generate a new key, breaking previously-encrypted secrets.")
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        key = _load_or_create_persisted_key()
        print(f"[WARNING] ENCRYPTION_KEY env var not set. Using key persisted at {_KEY_FILE}.")
        print("[WARNING] For production, set ENCRYPTION_KEY as a real environment variable/secret instead (more robust than a file on disk).")
        os.environ["ENCRYPTION_KEY"] = key

    try:
        # Validate key
        decoded = base64.urlsafe_b64decode(key.encode())
        if len(decoded) != 32:
            raise ValueError("Invalid key length")
        _fernet = Fernet(key.encode())
    except Exception:
        # Generate a valid key from the provided string via padding/hashing
        import hashlib
        hashed = hashlib.sha256(key.encode()).digest()
        valid_key = base64.urlsafe_b64encode(hashed)
        _fernet = Fernet(valid_key)

    return _fernet


def encrypt(text: str) -> str:
    if not text:
        return ""
    f = _get_fernet()
    return f.encrypt(text.encode()).decode()


def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except Exception:
        return ""


def mask_key(text: str) -> str:
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return text[:4] + "****" + text[-4:]
