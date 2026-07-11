import os
import base64
from cryptography.fernet import Fernet

_fernet = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        key = Fernet.generate_key().decode()
        print(f"[WARNING] ENCRYPTION_KEY not set. Generated key: {key}")
        print("[WARNING] Set ENCRYPTION_KEY env var to persist encrypted data across restarts.")
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
