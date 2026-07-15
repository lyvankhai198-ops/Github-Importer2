"""
Ví chợ ("market wallet") service — decimal-safe, atomic balance mutations for
the AdminUser.market_wallet_balance column shared by every tenant (and the
owner's own row, which instead tracks how much the owner has prepaid to the
real upstream supplier — see models.AdminUser.market_wallet_balance).

Mirrors services/wallet_service.py's atomicity model exactly:
  - Every mutation runs on a raw connection under BEGIN IMMEDIATE so the
    balance UPDATE, the ledger INSERT, and any caller-supplied guarded
    business-state UPDATE (idempotency) commit or roll back together.
  - Balance is quantized (VND: 0dp, USDT: 4dp) via the same _QUANT table
    wallet_service uses, imported from there rather than duplicated.
  - Debits never take the balance below zero (allow_negative=False by
    default) — "không cho phép ví chợ âm" per spec.

`admin_user_id` is always an AdminUser.id — the same integer used elsewhere
in the codebase as `tenant_id` (see tenancy.py / TenantScopedMixin).
"""
import logging
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime

from models import AdminUser, MarketWalletTransaction, WalletCurrency, WalletTxType
from services.wallet_service import (
    _QUANT,
    _currency_enum,
    quantize_amount,
    InsufficientBalanceError,
    AlreadyProcessedError,
)

logger = logging.getLogger(__name__)


def get_balance(admin: AdminUser, currency=WalletCurrency.VND) -> float:
    return admin.market_wallet_balance or 0.0


def _atomic_market_wallet_txn(admin_user_id: int, currency: WalletCurrency, delta: Decimal,
                               tx_type: WalletTxType, order_id: int = None, deposit_id: int = None,
                               withdrawal_id: int = None, note: str = "", actor: str = "system",
                               allow_negative: bool = False, extra_updates: list = None):
    """Same locking/idempotency contract as wallet_service._atomic_wallet_txn,
    but against admin_users.market_wallet_balance instead of users.wallet_*."""
    from database import engine

    quant = _QUANT[currency]
    extra_updates = extra_updates or []

    raw_conn = engine.raw_connection()
    try:
        raw_conn.isolation_level = None
        cur = raw_conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            cur.execute("SELECT market_wallet_balance FROM admin_users WHERE id = ?", (admin_user_id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"AdminUser {admin_user_id} not found")
            before = Decimal(str(row[0] if row[0] is not None else 0.0)).quantize(quant, rounding=ROUND_HALF_UP)
            after = (before + delta).quantize(quant, rounding=ROUND_HALF_UP)
            if after < 0 and not allow_negative:
                raise InsufficientBalanceError(currency.value, float(before), float(-delta))

            for sql, params in extra_updates:
                cur.execute(sql, params)
                if cur.rowcount == 0:
                    raise AlreadyProcessedError(
                        f"guarded update affected 0 rows for admin_user={admin_user_id} "
                        f"order_id={order_id} deposit_id={deposit_id} withdrawal_id={withdrawal_id} tx_type={tx_type}"
                    )

            now_iso = datetime.utcnow().isoformat(sep=" ")
            cur.execute(
                "UPDATE admin_users SET market_wallet_balance = ?, updated_at = ? WHERE id = ?",
                (float(after), now_iso, admin_user_id),
            )
            amt = float(abs(delta).quantize(quant, rounding=ROUND_HALF_UP))
            cur.execute(
                """INSERT INTO market_wallet_transactions
                   (admin_user_id, currency, tx_type, amount, balance_before, balance_after,
                    order_id, deposit_id, withdrawal_id, note, actor, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (admin_user_id, currency.value, tx_type.value, amt, float(before), float(after),
                 order_id, deposit_id, withdrawal_id, note, actor, now_iso),
            )
            tx_id = cur.lastrowid
            raw_conn.commit()
        except Exception:
            raw_conn.rollback()
            raise
    finally:
        raw_conn.close()

    return tx_id, float(before), float(after)


def credit_market_wallet(db, admin_user_id: int, currency, amount: float, tx_type: WalletTxType,
                          deposit_id: int = None, note: str = "", actor: str = "system",
                          extra_updates: list = None) -> MarketWalletTransaction:
    cur = _currency_enum(currency)
    amt = quantize_amount(cur, amount)
    if amt <= 0:
        raise ValueError("credit amount must be positive")
    tx_id, before, after = _atomic_market_wallet_txn(
        admin_user_id, cur, Decimal(str(amt)), tx_type,
        deposit_id=deposit_id, note=note, actor=actor,
        allow_negative=True, extra_updates=extra_updates,
    )
    db.expire_all()
    return db.query(MarketWalletTransaction).filter(MarketWalletTransaction.id == tx_id).first()


def debit_market_wallet(db, admin_user_id: int, currency, amount: float, tx_type: WalletTxType,
                         order_id: int = None, withdrawal_id: int = None, note: str = "",
                         actor: str = "system", extra_updates: list = None) -> MarketWalletTransaction:
    cur = _currency_enum(currency)
    amt = quantize_amount(cur, amount)
    if amt <= 0:
        raise ValueError("debit amount must be positive")
    tx_id, before, after = _atomic_market_wallet_txn(
        admin_user_id, cur, Decimal(str(-amt)), tx_type,
        order_id=order_id, withdrawal_id=withdrawal_id, note=note, actor=actor,
        allow_negative=False, extra_updates=extra_updates,
    )
    db.expire_all()
    return db.query(MarketWalletTransaction).filter(MarketWalletTransaction.id == tx_id).first()


def debit_for_sale(db, admin_user_id: int, order_id: int, cost_amount: float, fee_amount: float,
                    currency=WalletCurrency.VND) -> MarketWalletTransaction:
    """
    Single atomic debit covering both the cost-of-goods and the 2% platform
    fee for one successfully-fulfilled chợ-sourced order — spec explicitly
    says the fee is deducted "on top of" the cost, not tracked as separate
    debt, so one combined ledger row (note breaks down the split) is enough.
    Guarded by orders.market_wallet_debited so a retry can never double-debit.
    Raises InsufficientBalanceError (never lets the balance go negative) or
    AlreadyProcessedError (already debited by a previous call).
    """
    total = quantize_amount(currency, (cost_amount or 0.0) + (fee_amount or 0.0))
    if total <= 0:
        raise ValueError("debit_for_sale total must be positive")
    return debit_market_wallet(
        db, admin_user_id, currency, total, WalletTxType.purchase,
        order_id=order_id,
        note=f"Bán hàng nguồn chợ — vốn {cost_amount:,.0f} + phí nền tảng 2% {fee_amount:,.0f}".replace(",", "."),
        actor="system",
        extra_updates=[(
            "UPDATE orders SET market_wallet_debited = 1 WHERE id = ? AND market_wallet_debited = 0",
            (order_id,),
        )],
    )


def list_market_wallet_transactions(db, admin_user_id: int, limit: int = 50):
    return (
        db.query(MarketWalletTransaction)
        .filter(MarketWalletTransaction.admin_user_id == admin_user_id)
        .order_by(MarketWalletTransaction.created_at.desc())
        .limit(limit)
        .all()
    )


def get_pending_withdrawal_total(db, admin_user_id: int, currency=WalletCurrency.VND) -> float:
    """Sum of withdrawal requests still pending/approved (not yet paid or
    rejected/cancelled) — subtracted from balance to get "khả dụng để rút"."""
    from models import MarketWalletWithdrawal, MarketWalletWithdrawalStatus
    from sqlalchemy import func
    total = (
        db.query(func.sum(MarketWalletWithdrawal.amount))
        .filter(
            MarketWalletWithdrawal.admin_user_id == admin_user_id,
            MarketWalletWithdrawal.currency == _currency_enum(currency),
            MarketWalletWithdrawal.status.in_([
                MarketWalletWithdrawalStatus.pending,
                MarketWalletWithdrawalStatus.approved,
            ]),
        )
        .scalar()
    )
    return float(total or 0.0)
