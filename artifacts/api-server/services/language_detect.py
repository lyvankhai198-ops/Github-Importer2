"""
language_detect.py — lightweight vi/en heuristic detector for short
admin-entered product text (name/description). Not a general-purpose
language detector: it is tuned for this shop's actual input shapes
(Vietnamese with occasional accent-free typing, or plain English) so
product_sync.resolve_bilingual_fields can tell which language box the
admin actually filled in.
"""
import re

_VI_DIACRITICS_RE = re.compile(
    "[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]",
    re.IGNORECASE,
)

_VI_COMMON_WORDS = {
    "và", "không", "tài", "khoản", "bảo", "hành", "hạn", "dụng", "mua", "về",
    "sử", "đăng", "nhập", "gói", "sản", "phẩm", "vui", "lòng", "đọc", "kỹ",
    "trước", "khi", "đổi", "hoặc", "gỡ", "thiết", "bị", "định", "dạng",
    "tháng", "ngày", "năm", "giờ", "kể", "từ", "lúc", "hướng", "dẫn", "cần",
    "la", "khong", "tai", "khoan", "bao", "hanh", "han", "dung", "doi",
    "dang", "nhap", "goi", "san", "pham", "vui", "long", "doc", "ky",
    "truoc", "gio", "thang", "nam",
}


def detect_language(text: str) -> str:
    """
    Heuristic vi/en detector. Returns "vi" if the text contains Vietnamese
    diacritics or common (accented or de-accented) Vietnamese words, "en"
    if it looks like plain English prose with none of those markers.
    Defaults to "vi" for empty/ambiguous input since the shop's primary
    market and existing data are Vietnamese.
    """
    if not text or not text.strip():
        return "vi"
    if _VI_DIACRITICS_RE.search(text):
        return "vi"
    tokens = set(re.split(r"[^a-zA-Z]+", text.lower()))
    if tokens & _VI_COMMON_WORDS:
        return "vi"
    ascii_words = re.findall(r"[a-zA-Z]{3,}", text)
    if len(ascii_words) >= 2:
        return "en"
    return "vi"
