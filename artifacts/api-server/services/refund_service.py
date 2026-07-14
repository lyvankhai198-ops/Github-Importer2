"""
Warranty-based refund-to-wallet for order issue reports.

Formula (per product spec):
    used_days              = days since the order's purchase time
    remaining_warranty_days = max(0, total_warranty_days - used_days)
    refund_amount           = sale_price * remaining_warranty_days / total_warranty_days
    (refund_amount = 0 if total_warranty_days <= 0, i.e. no/expired warranty)

Everything is computed with Decimal (never float) and quantized to the
currency's fixed precision (VND: integer, rounded DOWN so the shop never
overpays a fraction; USDT: same precision as wallet_service, also rounded
DOWN). refund_amount is always clamped to [0, order.total_price].

Currency: mirrors wallet_service's "VND → balance_vnd, USDT → balance_usdt"
rule, using Order.payment_currency when set (crypto/Binance orders record
"VND" or "USDT" there); everything else (bank transfer, wallet, unset)
defaults to VND, since sale_price/total_price are always VND-denominated
figures in this schema.
"""

import json
import logging
from datetime import datetime
from decimal import Decimal, ROUND_DOWN

from models import WalletCurrency, WalletTxType, ActivityLog
from services import wallet_service
from services.wallet_service import AlreadyProcessedError
from services.warranty import get_order_warranty_days

logger = logging.getLogger(__name__)

_QUANT = {
    WalletCurrency.VND: Decimal("1"),
    WalletCurrency.USDT: Decimal("0.0001"),
}


class RefundNotAllowedError(Exception):
    """Raised when a refund cannot proceed (already refunded, no permission, etc)."""
    pass


def _order_currency(order) -> WalletCurrency:
    cur = (getattr(order, "payment_currency", None) or "VND").upper()
    return WalletCurrency.USDT if cur == "USDT" else WalletCurrency.VND


def _purchase_time(order) -> datetime:
    return order.paid_at or order.created_at


def compute_refund(order, at_time: datetime = None) -> dict:
    """
    Recompute the refund a given order is entitled to RIGHT NOW (or at
    `at_time`, mainly for tests). Never mutates anything.
    Returns: {amount: float, currency: WalletCurrency, used_days: int,
              total_days: int, remaining_days: int, already_refunded: bool}
    """
    now = at_time or datetime.utcnow()
    currency = _order_currency(order)

    already_refunded = bool(getattr(order, "refunded_amount", None))
    if already_refunded:
        return {
            "amount": 0.0, "currency": currency, "used_days": 0,
            "total_days": 0, "remaining_days": 0, "already_refunded": True,
        }

    total_days, _source = get_order_warranty_days(order)
    purchase_time = _purchase_time(order)
    used_days = max(0, (now - purchase_time).days)

    if total_days <= 0:
        return {
            "amount": 0.0, "currency": currency, "used_days": used_days,
            "total_days": total_days, "remaining_days": 0, "already_refunded": False,
        }

    remaining_days = max(0, total_days - used_days)

    sale_price = Decimal(str(order.total_price or 0))
    fraction = Decimal(remaining_days) / Decimal(total_days)
    raw_amount = sale_price * fraction

    quant = _QUANT[currency]
    amount = raw_amount.quantize(quant, rounding=ROUND_DOWN)

    # Clamp to [0, sale_price] — never negative, never more than was paid.
    if amount < 0:
        amount = Decimal(0)
    if amount > sale_price:
        amount = sale_price.quantize(quant, rounding=ROUND_DOWN)

    return {
        "amount": float(amount), "currency": currency, "used_days": used_days,
        "total_days": total_days, "remaining_days": remaining_days, "already_refunded": False,
    }


def perform_refund(db, issue, order, admin_identity: str) -> dict:
    """
    Admin action: "💰 Hoàn tiền về ví". Re-computes the refund at click
    time, then atomically (single wallet_service transaction):
      - locks + credits the buyer's wallet (VND or USDT balance),
      - flips order.refunded_amount/refunded_at/refunded_by (guarded so a
        second click / a concurrent click from another admin is a no-op),
      - flips order_issues.status -> refunded (guarded the same way).
    Raises AlreadyProcessedError if another call already completed this
    exact refund (safe to show the user "already refunded" and stop).
    Returns {} refund result dict (from compute_refund) plus balance info,
    or {"amount": 0.0, ...} if the warranty had already expired (nothing
    is credited in that case — caller must not treat this as an error).
    """
    result = compute_refund(order, datetime.utcnow())
    if result["already_refunded"]:
        raise AlreadyProcessedError(f"order {order.id} already refunded")

    if result["amount"] <= 0:
        return result  # caller: tell admin "warranty expired", credit nothing

    now = datetime.utcnow()
    now_iso = now.isoformat(sep=" ")
    currency = result["currency"]

    extra_updates = [
        (
            "UPDATE orders SET refunded_amount = ?, refunded_at = ?, refunded_by = ? "
            "WHERE id = ? AND (refunded_amount IS NULL OR refunded_amount = 0)",
            (result["amount"], now_iso, admin_identity, order.id),
        ),
        (
            "UPDATE order_issues SET status = 'refunded', handled_by = ?, handled_at = ? "
            "WHERE id = ? AND status != 'refunded'",
            (admin_identity, now_iso, issue.id),
        ),
    ]

    tx = wallet_service.credit_wallet(
        db, order.telegram_user_id, currency, result["amount"], WalletTxType.refund,
        order_id=order.id,
        note=f"Refund order issue #{issue.id} (order {order.order_code})",
        actor=admin_identity,
        extra_updates=extra_updates,
    )
    db.refresh(order)
    db.refresh(issue)

    result["balance_before"] = tx.balance_before
    result["balance_after"] = tx.balance_after
    result["tx_id"] = tx.id

    # Best-effort audit trail — reuses the existing generic ActivityLog
    # table rather than adding a new one. Not part of the money-moving
    # transaction above (that already committed); a crash here would only
    # lose the audit line, never the refund/state change.
    try:
        log = ActivityLog(
            action="order_issue_refund",
            description=json.dumps({
                "admin_id": admin_identity,
                "telegram_user_id": order.telegram_user_id,
                "order_id": order.id,
                "issue_id": issue.id,
                "refund_amount": result["amount"],
                "currency": currency.value,
                "balance_before": tx.balance_before,
                "balance_after": tx.balance_after,
                "reason": "order_issue_refund",
            }),
            user_type="admin",
            user_id=admin_identity,
        )
        db.add(log)
        db.commit()
    except Exception as e:
        logger.error(f"[refund_service] audit log write failed for issue={issue.id}: {e}")
        db.rollback()

    return result
