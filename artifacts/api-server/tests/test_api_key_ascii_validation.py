"""
Guards against the "'ascii' codec can't encode character ..." failure mode:
a Vietnamese phone keyboard silently autocorrecting a character while the
admin types/pastes an API key or base URL corrupts it beyond use — it saves
fine, then every later sync/test call fails with a cryptic UnicodeEncodeError
instead of a clear message at the moment the bad value was entered.
"""
from routers.api_connections import _non_ascii_error
from services.api_service import _friendly_error_message


def test_non_ascii_error_flags_vietnamese_autocorrect_character():
    err = _non_ascii_error("gAAAAABưbcdef", "API Key")
    assert err is not None
    assert "ư" in err


def test_non_ascii_error_allows_plain_ascii_key():
    assert _non_ascii_error("gAAAAABabcdef123==", "API Key") is None


def test_non_ascii_error_allows_empty_value():
    assert _non_ascii_error("", "API Key") is None


def test_friendly_error_message_explains_unicode_encode_error():
    raw = "'ascii' codec can't encode character '\\u01b0' in position 2: ordinal not in range(128)"
    friendly = _friendly_error_message(raw)
    assert raw in friendly
    assert "ký tự không hợp lệ" in friendly


def test_friendly_error_message_passes_through_other_errors_unchanged():
    raw = "HTTP 401: Unauthorized"
    assert _friendly_error_message(raw) == raw
