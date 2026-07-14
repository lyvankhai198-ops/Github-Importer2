"""
Warranty-days parsing + resolution for order refunds.

Product.warranty is a free-text admin-typed field (e.g. "BHF", "KBH",
"BH 30D", "3 Tháng", "1 Năm", "Trọn đời") — there is no numeric column for
it. To compute a refund we need a day count, so this module converts that
text into an integer once, at order-creation time, and the result is
snapshotted onto Order.warranty_days (see models.Order). Refund math must
always prefer that snapshot over re-parsing the product's *current*
warranty text, since an admin may have edited the product after the order
shipped (see services/refund_service.py).
"""

import re
import logging

logger = logging.getLogger(__name__)

# A warranty considered "no expiry" for refund purposes (full/lifetime
# warranty). Chosen large enough that no realistic order will ever exceed
# it, so `remaining_days` never hits zero from full-warranty products.
LIFETIME_DAYS = 36500  # ~100 years

# Vietnamese/English "no warranty" markers → 0 days (refund always 0).
_NO_WARRANTY_RE = re.compile(r"\bKBH\b|kh[oô]ng\s*b[aả]o\s*h[aà]nh|no\s*warranty", re.IGNORECASE)
# Full/lifetime warranty markers → LIFETIME_DAYS.
_FULL_WARRANTY_RE = re.compile(
    r"\bBHF\b|b[aả]o\s*h[aà]nh\s*full|full\s*warranty|tr[oọ]n\s*đ[oờ]i|v[iĩ]nh\s*vi[eễ]n|lifetime",
    re.IGNORECASE,
)
# "BH 30D" / "BH 3M" / "BH 1Y" / "30D" / "3M" / "1Y" shorthand.
_SHORTHAND_RE = re.compile(r"(\d+)\s*([DdMmYy])\b")
# "30 ngày" / "3 tháng" / "1 năm" (accepts with/without diacritics-safe fragments).
_VI_UNIT_RE = re.compile(r"(\d+)\s*(ng[aà]y|th[aá]ng|n[aă]m)", re.IGNORECASE)
# "30 days" / "3 months" / "1 year"
_EN_UNIT_RE = re.compile(r"(\d+)\s*(day|month|year)s?", re.IGNORECASE)

_UNIT_DAYS = {
    "d": 1, "day": 1, "ngày": 1, "ngay": 1,
    "m": 30, "month": 30, "tháng": 30, "thang": 30,
    "y": 365, "year": 365, "năm": 365, "nam": 365,
}


def _unit_to_days(unit: str) -> int:
    key = unit.strip().lower()
    return _UNIT_DAYS.get(key, 0)


def parse_warranty_to_days(text: str) -> int:
    """
    Best-effort parse of a free-text warranty string into a day count.
    Returns 0 (no warranty / unparseable) if nothing matches — refund math
    then correctly yields 0 rather than guessing a positive number.
    """
    if not text:
        return 0
    s = str(text).strip()
    if not s:
        return 0

    if _NO_WARRANTY_RE.search(s):
        return 0
    if _FULL_WARRANTY_RE.search(s):
        return LIFETIME_DAYS

    m = _SHORTHAND_RE.search(s)
    if m:
        n, unit = m.groups()
        days = int(n) * _unit_to_days(unit)
        if days > 0:
            return days

    m = _VI_UNIT_RE.search(s) or _EN_UNIT_RE.search(s)
    if m:
        n, unit = m.groups()
        days = int(n) * _unit_to_days(unit)
        if days > 0:
            return days

    logger.warning(f"[warranty] could not parse warranty text into days: {s!r} — treating as 0")
    return 0


def get_order_warranty_days(order) -> tuple:
    """
    Resolve the warranty day-count to use for refund math on this order.
    Returns (days: int, source: str) where source is one of:
      "snapshot"       — order.warranty_days was set at purchase time (preferred)
      "product_fallback" — no snapshot; fell back to the product's CURRENT
                            warranty text (may be wrong if edited since purchase)
      "none"           — no snapshot and no product to fall back to
    """
    if order.warranty_days is not None:
        return int(order.warranty_days), "snapshot"

    product = getattr(order, "product", None)
    if product is not None and getattr(product, "warranty", None):
        logger.warning(
            f"[warranty] order {getattr(order, 'id', '?')} has no warranty_days snapshot — "
            f"falling back to product.warranty (current, may differ from purchase time)"
        )
        return parse_warranty_to_days(product.warranty), "product_fallback"

    logger.warning(f"[warranty] order {getattr(order, 'id', '?')} has no warranty snapshot or product — 0 days")
    return 0, "none"
