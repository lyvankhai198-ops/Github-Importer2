"""
Wallet service — decimal-safe, atomic balance mutations for the customer
wallet (deposit / pay-with-wallet / admin credit-debit / auto-refund).

Storage stays as SQLAlchemy Float (consistent with the rest of the schema,
which has no Numeric/Decimal precedent), but every arithmetic step here goes
through Decimal and is quantized to a fixed precision before being written
back — VND: 0 decimal places, USDT: 2 decimal places — so repeated
credit/debit cycles never drift from floating-point rounding error.

Atomicity: the balance update, the ledger insert, AND the caller's related
business-state change (e.g. "mark this WalletDeposit confirmed", "mark this
order paid", "mark this order refunded_to_wallet") all happen inside a
SINGLE raw-connection transaction under BEGIN IMMEDIATE — never as separate
commits. This is the critical property for money-moving code: if any part
fails, the whole thing rolls back, so a balance change can never be
persisted while its paired business-state change is lost (which would let
an admin/webhook retry double-credit or double-debit).

Idempotency is enforced by the caller supplying a guarded UPDATE as part of
`extra_updates` (e.g. `... WHERE status = 'pending'` or
`... WHERE refunded_to_wallet = 0`). If that guarded UPDATE affects zero
rows — meaning another call already completed the same business transition
— the whole operation raises AlreadyProcessedError and rolls back without
touching the balance at all.
"""

import logging
import uuid
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime

from models import User, WalletCurrency, WalletTransaction, WalletTxType

logger = logging.getLogger(__name__)

_QUANT = {
    WalletCurrency.VND: Decimal("1"),
    WalletCurrency.USDT: Decimal("0.01"),
}


class InsufficientBalanceError(Exception):
    """Raised when a debit would take a wallet balance below zero."""
    def __init__(self, currency, balance: float, amount: float):
        self.currency = currency
        self.balance = balance
        self.amount = amount
        super().__init__(f"Insufficient {currency} balance: have {balance}, need {amount}")


class AlreadyProcessedError(Exception):
    """
    Raised when a guarded business-state UPDATE (part of extra_updates)
    affected zero rows — the operation this call represents (confirming a
    deposit, refunding an order, debiting for a purchase, ...) was already
    completed by a previous call. Nothing was mutated; safe to ignore or
    surface as a no-op to the caller.
    """
    pass


def _currency_enum(currency) -> WalletCurrency:
    if isinstance(currency, WalletCurrency):
        return currency
    return WalletCurrency(str(currency).upper())


def _balance_column(currency: WalletCurrency) -> str:
    return "wallet_vnd" if currency == WalletCurrency.VND else "wallet_usdt"


def quantize_amount(currency, value) -> float:
    """Round a raw float/Decimal to the currency's fixed precision."""
    cur = _currency_enum(currency)
    d = Decimal(str(value)).quantize(_QUANT[cur], rounding=ROUND_HALF_UP)
    return float(d)


def get_balance(user: User, currency) -> float:
    cur = _currency_enum(currency)
    return getattr(user, _balance_column(cur)) or 0.0


def generate_deposit_reference() -> str:
    return "DEP-" + uuid.uuid4().hex[:8].upper()


def _atomic_wallet_txn(telegram_user_id: str, currency: WalletCurrency, delta: Decimal,
                        tx_type: WalletTxType, order_id: int = None, deposit_id: int = None,
                        note: str = "", actor: str = "system", allow_negative: bool = False,
                        extra_updates: list = None):
    """
    Single BEGIN IMMEDIATE transaction that:
      1. Locks + reads the user's balance column.
      2. Computes + validates the new balance (delta: +credit / -debit).
      3. Runs every (sql, params) in extra_updates — each MUST include its
         own idempotency guard in the WHERE clause. If any of them affects
         zero rows, the whole transaction rolls back and
         AlreadyProcessedError is raised (nothing — not even the balance —
         is changed).
      4. Writes the balance UPDATE and the wallet_transactions ledger INSERT.
      5. Commits everything together.
    Returns (transaction_id, balance_before, balance_after) as native types.
    """
    from database import engine

    col = _balance_column(currency)
    quant = _QUANT[currency]
    amt = float(abs(delta).quantize(quant, rounding=ROUND_HALF_UP))
    extra_updates = extra_updates or []

    raw_conn = engine.raw_connection()
    try:
        raw_conn.isolation_level = None  # manual transaction control
        cur = raw_conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            cur.execute(f"SELECT {col} FROM users WHERE telegram_id = ?", (telegram_user_id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"User {telegram_user_id} not found")
            before = Decimal(str(row[0] if row[0] is not None else 0.0)).quantize(quant, rounding=ROUND_HALF_UP)
            after = (before + delta).quantize(quant, rounding=ROUND_HALF_UP)
            if after < 0 and not allow_negative:
                raise InsufficientBalanceError(currency.value, float(before), float(-delta))

            # Idempotency guards first — if any fails to match a row, bail
            # out before the balance or ledger are touched at all.
            for sql, params in extra_updates:
                cur.execute(sql, params)
                if cur.rowcount == 0:
                    raise AlreadyProcessedError(
                        f"guarded update affected 0 rows for user={telegram_user_id} "
                        f"order_id={order_id} deposit_id={deposit_id} tx_type={tx_type}"
                    )

            now_iso = datetime.utcnow().isoformat(sep=" ")
            cur.execute(
                f"UPDATE users SET {col} = ?, updated_at = ? WHERE telegram_id = ?",
                (float(after), now_iso, telegram_user_id),
            )
            cur.execute(
                """INSERT INTO wallet_transactions
                   (telegram_user_id, currency, tx_type, amount, balance_before, balance_after,
                    order_id, deposit_id, note, actor, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (telegram_user_id, currency.value, tx_type.value, amt, float(before), float(after),
                 order_id, deposit_id, note, actor, now_iso),
            )
            tx_id = cur.lastrowid
            raw_conn.commit()
        except Exception:
            raw_conn.rollback()
            raise
    finally:
        raw_conn.close()

    return tx_id, float(before), float(after)


def credit_wallet(db, telegram_user_id: str, currency, amount: float, tx_type: WalletTxType,
                   order_id: int = None, deposit_id: int = None, note: str = "",
                   actor: str = "system", extra_updates: list = None) -> WalletTransaction:
    """
    Atomically add `amount` to the user's wallet, record a ledger row, and
    apply any caller-supplied guarded business-state updates — all in one
    transaction. Raises AlreadyProcessedError (no-op, nothing changed) if a
    guard in extra_updates matched zero rows.
    `db` is only used to look up and return the resulting ORM row; the
    actual mutation happens on a separate locked raw connection.
    """
    cur = _currency_enum(currency)
    amt = quantize_amount(cur, amount)
    if amt <= 0:
        raise ValueError("credit amount must be positive")
    tx_id, before, after = _atomic_wallet_txn(
        telegram_user_id, cur, Decimal(str(amt)), tx_type,
        order_id=order_id, deposit_id=deposit_id, note=note, actor=actor,
        allow_negative=True, extra_updates=extra_updates,
    )
    db.expire_all()
    return db.query(WalletTransaction).filter(WalletTransaction.id == tx_id).first()


def debit_wallet(db, telegram_user_id: str, currency, amount: float, tx_type: WalletTxType,
                  order_id: int = None, deposit_id: int = None, note: str = "",
                  actor: str = "system", extra_updates: list = None) -> WalletTransaction:
    """
    Atomically subtract `amount` from the user's wallet, record a ledger
    row, and apply any caller-supplied guarded business-state updates — all
    in one transaction. Raises InsufficientBalanceError if the balance is
    too low (no partial spend), or AlreadyProcessedError if a guard in
    extra_updates matched zero rows. Either way, nothing is changed.
    """
    cur = _currency_enum(currency)
    amt = quantize_amount(cur, amount)
    if amt <= 0:
        raise ValueError("debit amount must be positive")
    tx_id, before, after = _atomic_wallet_txn(
        telegram_user_id, cur, Decimal(str(-amt)), tx_type,
        order_id=order_id, deposit_id=deposit_id, note=note, actor=actor,
        allow_negative=False, extra_updates=extra_updates,
    )
    db.expire_all()
    return db.query(WalletTransaction).filter(WalletTransaction.id == tx_id).first()


def list_wallet_transactions(db, telegram_user_id: str, limit: int = 50):
    return (
        db.query(WalletTransaction)
        .filter(WalletTransaction.telegram_user_id == telegram_user_id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(limit)
        .all()
    )


async def refund_order_to_wallet(db, order, reason: str = ""):
    """
    Auto-refund hook: called from the fulfillment-failure branches
    (api_failed / paid_waiting_stock / delivery_failed) whenever the order
    was paid via the wallet. The credit and the `refunded_to_wallet` flag
    flip happen in one atomic transaction (via extra_updates), so a crash
    between them can never leave one done without the other — the WHERE
    refunded_to_wallet = 0 guard means a retry after a real failure is safe,
    and a retry after a completed refund is a guaranteed no-op.
    """
    if order.payment_method != "wallet":
        return
    if getattr(order, "refunded_to_wallet", False):
        return
    try:
        credit_wallet(
            db, order.telegram_user_id, WalletCurrency.VND, order.total_price,
            WalletTxType.refund, order_id=order.id,
            note=f"Auto refund — {reason}" if reason else "Auto refund (fulfillment failed)",
            actor="system",
            extra_updates=[(
                "UPDATE orders SET refunded_to_wallet = 1 WHERE id = ? AND refunded_to_wallet = 0",
                (order.id,),
            )],
        )
        db.refresh(order)

        from services.bot_service import bot_manager
        if bot_manager.is_running():
            from bot.notifier import notify_user_wallet_refund
            from bot.i18n import get_user_lang
            lang = get_user_lang(db, order.telegram_user_id)
            chat_id = order.payment_chat_id or order.telegram_user_id
            await notify_user_wallet_refund(bot_manager._application.bot, chat_id, order, lang=lang)
    except AlreadyProcessedError:
        logger.info(f"[wallet] refund_order_to_wallet order={order.id} already refunded — skipped")
    except Exception as e:
        logger.error(f"[wallet] refund_order_to_wallet order={order.id} error: {e}")
