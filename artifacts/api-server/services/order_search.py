"""
"🔍 Tìm đơn hàng" — find orders by delivered email/account.

Searches, in order of preference:
  1. Order.delivery_items (JSON list of {username, password, value, note})
     — the normalized "what we actually sent the customer" record.
  2. Order.delivery_data (raw text, legacy/API-source deliveries).
  3. InventoryItem rows sold from local stock (manual_stock), joined back
     to their order via InventoryItem.sold_order_id.

Input is normalized before comparing: trimmed, and if it contains "|"
(e.g. "email|password"), only the part before "|" is used. Exact matches
(after normalization) are preferred; if none, falls back to a substring
("fuzzy") match. Access control (own orders only vs. admin-sees-all) is
enforced by the caller via `is_admin` / `telegram_user_id`.
"""

import json
import logging
from sqlalchemy import or_

from models import Order, InventoryItem, InventoryStatus

logger = logging.getLogger(__name__)


def normalize_query(raw: str) -> str:
    q = (raw or "").strip()
    if "|" in q:
        q = q.split("|", 1)[0].strip()
    return q


def _candidate_strings_for_order(order) -> set:
    """Every delivered-account-ish string tied to this order, for exact-match ranking."""
    cands = set()
    if order.delivery_items:
        try:
            items = json.loads(order.delivery_items)
            if isinstance(items, list):
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    for key in ("username", "value"):
                        v = it.get(key)
                        if v:
                            v = str(v).strip()
                            cands.add(v)
                            if "|" in v:
                                cands.add(v.split("|", 1)[0].strip())
        except Exception:
            pass
    if order.delivery_data:
        cands.add(str(order.delivery_data).strip())
    return cands


def find_orders(db, raw_query: str, telegram_user_id: str = None, is_admin: bool = False) -> list:
    """
    Returns a list[Order]: exact matches if any exist, else fuzzy matches,
    else []. Never returns another user's orders unless is_admin=True.
    """
    q = normalize_query(raw_query)
    if not q:
        return []
    q_lower = q.lower()
    like = f"%{q}%"

    order_query = db.query(Order)
    if not is_admin:
        if not telegram_user_id:
            return []
        order_query = order_query.filter(Order.telegram_user_id == str(telegram_user_id))

    candidates = (
        order_query.filter(
            or_(Order.delivery_data.ilike(like), Order.delivery_items.ilike(like))
        )
        .order_by(Order.created_at.desc())
        .all()
    )
    candidate_ids = {o.id for o in candidates}

    # Local-inventory sold accounts (manual_stock products) — delivery_data
    # for these may just be a generic confirmation, so the real credential
    # lives on InventoryItem instead.
    inv_matches = (
        db.query(InventoryItem)
        .filter(
            InventoryItem.status == InventoryStatus.sold,
            InventoryItem.sold_order_id.isnot(None),
            or_(
                InventoryItem.email.ilike(like),
                InventoryItem.username.ilike(like),
                InventoryItem.raw_value.ilike(like),
            ),
        )
        .all()
    )
    extra_ids = [inv.sold_order_id for inv in inv_matches if inv.sold_order_id not in candidate_ids]
    if extra_ids:
        extra_q = db.query(Order).filter(Order.id.in_(extra_ids))
        if not is_admin:
            extra_q = extra_q.filter(Order.telegram_user_id == str(telegram_user_id))
        extra_orders = extra_q.all()
        candidates.extend(extra_orders)
        candidate_ids.update(o.id for o in extra_orders)

    # Rank: exact match (against normalized candidate strings, including
    # per-item and per-inventory-row fields) beats a bare substring hit.
    exact, fuzzy = [], []
    inv_by_order = {}
    for inv in inv_matches:
        inv_by_order.setdefault(inv.sold_order_id, []).append(inv)

    for o in candidates:
        cands = _candidate_strings_for_order(o)
        for inv in inv_by_order.get(o.id, []):
            for f in (inv.email, inv.username, inv.raw_value):
                if f:
                    cands.add(str(f).strip())

        is_exact = any(c.lower() == q_lower for c in cands)
        (exact if is_exact else fuzzy).append(o)

    return exact if exact else fuzzy
