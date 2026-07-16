"""
Tests for the ENCRYPTION_KEY fallback in crypto.py.

Without a persisted fallback, not setting the ENCRYPTION_KEY env var meant
every process restart generated a brand-new random key in memory only —
silently breaking decrypt() for every secret already stored in the database
(Telegram bot token, supplier API keys, payment credentials), which forced
the admin to redo setup after every restart/redeploy.
"""
import importlib
import os

import pytest


@pytest.fixture()
def isolated_crypto(tmp_path, monkeypatch):
    """
    Reloads the crypto module with ENCRYPTION_KEY unset and its key-file
    fallback pointed at a scratch directory, so this test never touches the
    project's real .encryption_key file or a real ENCRYPTION_KEY env var.
    """
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    import crypto
    importlib.reload(crypto)
    crypto._fernet = None
    crypto._KEY_FILE = tmp_path / ".encryption_key"
    yield crypto
    crypto._fernet = None
    importlib.reload(crypto)


def test_generated_key_is_persisted_to_disk(isolated_crypto):
    assert not isolated_crypto._KEY_FILE.exists()
    isolated_crypto._get_fernet()
    assert isolated_crypto._KEY_FILE.exists()
    assert isolated_crypto._KEY_FILE.read_text().strip()


def test_key_survives_simulated_restart(isolated_crypto):
    """
    Encrypt with one "process" (fresh Fernet instance from a clean
    in-memory state), then simulate a restart by resetting the cached
    Fernet and re-reading the key — decrypt must still succeed using the
    same persisted key file, exactly like a VPS redeploy would.
    """
    ciphertext = isolated_crypto.encrypt("my-secret-token")

    # Simulate a full process restart: drop the cached Fernet and the
    # in-process env var, as a fresh Python process would start.
    isolated_crypto._fernet = None
    os.environ.pop("ENCRYPTION_KEY", None)

    assert isolated_crypto.decrypt(ciphertext) == "my-secret-token"


def test_second_boot_reuses_existing_key_file_instead_of_generating_new_one(isolated_crypto):
    isolated_crypto._get_fernet()
    first_key = isolated_crypto._KEY_FILE.read_text()

    isolated_crypto._fernet = None
    os.environ.pop("ENCRYPTION_KEY", None)
    isolated_crypto._get_fernet()
    second_key = isolated_crypto._KEY_FILE.read_text()

    assert first_key == second_key
