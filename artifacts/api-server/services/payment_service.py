"""
Payment service — multi-method.

Responsibilities:
  - Payment code generation (SePay)
  - VietQR URL construction
  - Pending payment order creation (draft → method selection)
  - SePay webhook transaction normalization + matching
  - process_paid_order (idempotent — all methods share this)
  - Expiry background loop
  - Safe message deletion helpers

Security rules:
  - api_token / webhook_secret / crypto keys NEVER written to logs.
  - process_paid_order is idempotent: checks order.status before acting.
  - payment_status ONLY set by webhook/worker — never by user callbacks.
  - Crypto txid used exactly once (unique DB constraint).
"""
import json
import logging
import asyncio
import uuid
import re
from datetime import datetime, timedelta
from urllib.parse import quote
from sqlalchemy.orm import Session

from services.normalize import format_vnd
from models import (
    Order, OrderStatus, PaymentStatus, PaymentTransaction,
    SepayConfig, TelegramBotConfig, User, SourceType, AdminUser,
)
from services.wallet_service import InsufficientBalanceError, AlreadyProcessedError

logger = logging.getLogger(__name__)

# Prevent concurrent process_paid_order calls for the same order
_processing_paid: set = set()


# ── Config ─────────────────────────────────────────────────────────────────────

def get_sepay_config(db: Session):
    return db.query(SepayConfig).first()


def get_or_create_sepay_config(db: Session) -> SepayConfig:
    cfg = db.query(SepayConfig).first()
    if not cfg:
        cfg = SepayConfig()
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def is_sepay_enabled(db: Session) -> bool:
    cfg = get_sepay_config(db)
    return bool(cfg and cfg.is_enabled)


def get_enabled_payment_methods(db: Session) -> list[str]:
    """Return list of enabled method_codes. Always includes 'bank_transfer' if SePay is enabled."""
    from models import PaymentMethod
    methods = []
    if is_sepay_enabled(db):
        methods.append("bank_transfer")
    pm_rows = db.query(PaymentMethod).filter(PaymentMethod.is_active == True).all()
    for pm in pm_rows:
        if pm.method_code not in methods:
            methods.append(pm.method_code)
    return methods


# ── Payment code (SePay) ───────────────────────────────────────────────────────

def generate_payment_code(order_code: str, prefix: str = "AIC") -> str:
    import hashlib
    seed = order_code + uuid.uuid4().hex
    hex_part = hashlib.md5(seed.encode()).hexdigest()[:8].upper()
    return f"{prefix}{hex_part}"


# ── VietQR ─────────────────────────────────────────────────────────────────────

def generate_vietqr_url(
    bank_bin: str,
    account_number: str,
    amount: float,
    payment_code: str,
    account_name: str = "",
    shop_name: str = "",
) -> str:
    from urllib.parse import urlencode
    params = {
        "acc": account_number,
        "bank": bank_bin,
        "amount": int(amount),
        "des": payment_code,
        "template": "compact",
        "download": "0",
        "showinfo": "1",
        "fullacc": "0",
        "holder": account_name,
        "store": shop_name,
    }
    return "https://vietqr.app/img?" + urlencode(params)


# ── Create pending payment order (draft — method not yet selected) ─────────────

def create_pending_payment_order(
    db: Session,
    telegram_user_id: str,
    product_id: int,
    quantity: int,
    payment_method: str = "bank_transfer",
) -> Order:
    """
    Create an order in pending_payment state.
    payment_method defaults to bank_transfer for backward compat;
    the new flow passes the chosen method.
    Does NOT call API source — payment must arrive first.
    """
    from models import Product
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise ValueError("Product not found")

    cfg = get_sepay_config(db)
    prefix = (cfg.payment_prefix or "AIC") if cfg else "AIC"
    timeout = cfg.payment_timeout_minutes if cfg else 15

    order_code = "ORD-" + uuid.uuid4().hex[:8].upper()
    total = product.sale_price * quantity

    from services.warranty import parse_warranty_to_days
    order = Order(
        order_code=order_code,
        telegram_user_id=telegram_user_id,
        product_id=product_id,
        quantity=quantity,
        unit_price=product.sale_price,
        total_price=total,
        expected_amount=total,
        paid_amount=0.0,
        status=OrderStatus.pending_payment,
        payment_status=PaymentStatus.pending,
        payment_method=payment_method,
        payment_currency="VND",
        payment_expires_at=datetime.utcnow() + timedelta(minutes=timeout),
        warranty_days=parse_warranty_to_days(product.warranty),
    )

    if payment_method == "bank_transfer":
        payment_code = generate_payment_code(order_code, prefix)
        order.payment_code = payment_code

    db.add(order)
    db.commit()
    db.refresh(order)

    # Update user activity
    user = db.query(User).filter(User.telegram_id == telegram_user_id).first()
    if user:
        user.last_active_at = datetime.utcnow()
        db.commit()

    return order


def create_crypto_payment_order(
    db: Session,
    telegram_user_id: str,
    product_id: int,
    quantity: int,
    payment_method: str,  # usdt_bep20 | usdt_trc20
    wallet_address: str,
    expected_crypto_amount: float,
    exchange_rate: float,
    required_confirmations: int,
    network: str,
    timeout_minutes: int = 60,
) -> Order:
    """Create an order for BEP20 or TRC20 USDT payment."""
    from models import Product
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise ValueError("Product not found")

    order_code = "ORD-" + uuid.uuid4().hex[:8].upper()
    total = product.sale_price * quantity

    from services.warranty import parse_warranty_to_days
    order = Order(
        order_code=order_code,
        telegram_user_id=telegram_user_id,
        product_id=product_id,
        quantity=quantity,
        unit_price=product.sale_price,
        total_price=total,
        expected_amount=total,
        paid_amount=0.0,
        status=OrderStatus.pending_payment,
        payment_status=PaymentStatus.pending,
        payment_method=payment_method,
        payment_currency="USDT",
        exchange_rate=exchange_rate,
        expected_crypto_amount=expected_crypto_amount,
        payment_address=wallet_address,
        payment_network=network,
        required_confirmations=required_confirmations,
        confirmations=0,
        payment_expires_at=datetime.utcnow() + timedelta(minutes=timeout_minutes),
        warranty_days=parse_warranty_to_days(product.warranty),
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    user = db.query(User).filter(User.telegram_id == telegram_user_id).first()
    if user:
        user.last_active_at = datetime.utcnow()
        db.commit()

    return order


def create_binance_order(
    db: Session,
    telegram_user_id: str,
    product_id: int,
    quantity: int,
    expected_crypto_amount: float,
    exchange_rate: float,
    timeout_minutes: int = 30,
) -> Order:
    """
    Create an order for Binance Pay. Verification happens later against the
    shop's own Binance API Management Pay History (see
    services.crypto_monitor.verify_binance_payment) once the shopper submits
    a TXID — there is no merchant checkout order to create up front.
    """
    from models import Product
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise ValueError("Product not found")

    order_code = "ORD-" + uuid.uuid4().hex[:8].upper()
    total = product.sale_price * quantity

    from services.warranty import parse_warranty_to_days
    order = Order(
        order_code=order_code,
        telegram_user_id=telegram_user_id,
        product_id=product_id,
        quantity=quantity,
        unit_price=product.sale_price,
        total_price=total,
        expected_amount=total,
        paid_amount=0.0,
        status=OrderStatus.pending_payment,
        payment_status=PaymentStatus.pending,
        payment_method="binance_pay",
        payment_currency="USDT",
        exchange_rate=exchange_rate,
        expected_crypto_amount=expected_crypto_amount,
        payment_network="BINANCE",
        payment_expires_at=datetime.utcnow() + timedelta(minutes=timeout_minutes),
        warranty_days=parse_warranty_to_days(product.warranty),
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    user = db.query(User).filter(User.telegram_id == telegram_user_id).first()
    if user:
        user.last_active_at = datetime.utcnow()
        db.commit()

    return order


# ── SePay Webhook transaction processing ───────────────────────────────────────

def _normalize_sepay_transaction(raw: dict) -> dict:
    return {
        "transaction_id": str(raw.get("id") or raw.get("transactionId") or ""),
        "gateway": raw.get("gateway", ""),
        "transaction_date": raw.get("transactionDate") or raw.get("transaction_date") or "",
        "account_number": raw.get("accountNumber") or raw.get("account_number") or "",
        "transfer_content": raw.get("transferContent") or raw.get("transfer_content") or raw.get("content") or "",
        "amount_in": float(raw.get("transferAmount") or raw.get("amount_in") or 0),
        "amount_out": float(raw.get("deductionAmount") or raw.get("amount_out") or 0),
        "reference_code": raw.get("referenceCode") or raw.get("reference_code") or "",
    }


def _find_payment_code(content: str, prefix: str = "AIC") -> str | None:
    pattern = re.compile(rf"({re.escape(prefix)}[0-9A-Fa-f]{{8}})", re.IGNORECASE)
    match = pattern.search(content or "")
    return match.group(1).upper() if match else None


_DEPOSIT_REF_RE = re.compile(r"(DEP[0-9A-Fa-f]{8})", re.IGNORECASE)
_MARKET_DEP_REF_RE = re.compile(r"(MWDEP[0-9A-Fa-f]{8})", re.IGNORECASE)


def _find_deposit_reference(content: str) -> str | None:
    """Wallet-deposit reference codes look like DEP-1A2B3C4D; bank transfer
    content often strips punctuation, so match with or without the dash."""
    match = _DEPOSIT_REF_RE.search((content or "").replace("-", "").replace(" ", ""))
    return f"DEP-{match.group(1)[3:].upper()}" if match else None


def _find_market_deposit_reference(content: str) -> str | None:
    """Market-wallet (ví chợ) bank-transfer deposit codes look like MWDEP-A1B2C3D4.
    Same punctuation-stripping as the customer wallet variant above."""
    match = _MARKET_DEP_REF_RE.search((content or "").replace("-", "").replace(" ", ""))
    return f"MWDEP-{match.group(1)[5:].upper()}" if match else None


def _try_credit_vnd_deposit(db: Session, tx_id: str, amount_in: float, content: str, raw: dict, tx_date) -> dict | None:
    """
    Attempt to match a SePay bank-transfer webhook event to a pending VND
    wallet deposit via its reference code in the transfer content. Returns a
    result dict if a deposit was matched (whether credited or not), else
    None so the caller falls through to "unmatched".
    """
    from models import WalletDeposit, WalletDepositStatus, WalletCurrency, WalletTxType
    from services import wallet_service
    from services.wallet_service import AlreadyProcessedError

    ref = _find_deposit_reference(content)
    if not ref:
        return None
    deposit = db.query(WalletDeposit).filter(
        WalletDeposit.reference_code == ref,
        WalletDeposit.currency == WalletCurrency.VND,
    ).first()
    if not deposit:
        return None

    if deposit.status == WalletDepositStatus.credited:
        # Already accounted for — nothing left to reconcile.
        return {"success": True, "action": "deposit_already_done", "deposit_id": deposit.id}

    if deposit.status in (WalletDepositStatus.expired, WalletDepositStatus.cancelled,
                           WalletDepositStatus.failed, WalletDepositStatus.manual_review):
        # Money that matches a reference code but arrives after the deposit
        # left the "still trackable" states must NEVER be silently dropped —
        # a late bank transfer is real customer money that needs a human to
        # reconcile it, not a no-op. Escalate to manual_review (idempotent:
        # once there, later replays of the same tx just no-op below).
        if deposit.status == WalletDepositStatus.manual_review:
            return {"success": True, "action": "deposit_needs_review", "deposit_id": deposit.id}
        from sqlalchemy import text as _sql_text
        rows = db.execute(
            _sql_text(
                "UPDATE wallet_deposits SET status='manual_review', "
                "external_transaction_id=:txid, raw_transaction_data=:raw, "
                "failed_reason=:reason WHERE id=:id AND status=:prev_status"
            ),
            {
                "txid": str(tx_id), "raw": json.dumps(raw, ensure_ascii=False)[:5000],
                "reason": f"Chuyển khoản khớp mã {ref} sau khi yêu cầu đã {deposit.status.value} — cần admin kiểm tra.",
                "id": deposit.id, "prev_status": deposit.status.value,
            },
        )
        db.commit()
        if rows.rowcount == 0:
            # Someone else (admin action or another webhook replay) already
            # moved it in the meantime — treat as already handled.
            return {"success": True, "action": "deposit_already_done", "deposit_id": deposit.id}
        db.refresh(deposit)
        _notify_admin_deposit_needs_review(db, deposit)
        return {"success": True, "action": "deposit_needs_review", "deposit_id": deposit.id}

    # Bank transfer must cover at least the requested amount (small rounding
    # tolerance for bank fee quirks); credit exactly the requested amount,
    # any surplus stays with the shop (mirrors overpay handling on orders).
    if amount_in < deposit.amount - 1:
        return {"success": True, "action": "deposit_insufficient", "deposit_id": deposit.id}

    now_iso = datetime.utcnow().isoformat(sep=" ")
    try:
        wallet_service.credit_wallet(
            db, deposit.telegram_user_id, WalletCurrency.VND, deposit.amount,
            WalletTxType.deposit, deposit_id=deposit.id,
            note=f"Auto-credited SePay transfer (txid={tx_id})",
            actor="system",
            extra_updates=[(
                "UPDATE wallet_deposits SET status='credited', external_transaction_id=?, "
                "verified_at=?, credited_at=?, raw_transaction_data=? "
                "WHERE id=? AND status NOT IN ('credited','failed','expired','cancelled')",
                (tx_id, now_iso, now_iso, json.dumps(raw, ensure_ascii=False)[:5000], deposit.id),
            )],
        )
    except AlreadyProcessedError:
        return {"success": True, "action": "deposit_already_done", "deposit_id": deposit.id}

    db.refresh(deposit)
    asyncio.create_task(_notify_deposit_credited_async(deposit.id))
    return {"success": True, "action": "deposit_credited", "deposit_id": deposit.id}


def _try_credit_market_vnd_deposit(
    db: Session, tx_id: str, amount_in: float, content: str, raw: dict, tx_date
) -> dict | None:
    """
    Attempt to match a SePay bank-transfer to a pending ví chợ (market wallet)
    VND deposit via its MWDEP-XXXXXXXX reference code in the transfer content.
    Mirrors _try_credit_vnd_deposit — same idempotency / escalation logic,
    crediting via market_wallet_service instead of wallet_service.
    """
    from models import MarketWalletDeposit, WalletDepositStatus, WalletCurrency, WalletTxType
    from services import market_wallet_service

    ref = _find_market_deposit_reference(content)
    if not ref:
        return None
    deposit = db.query(MarketWalletDeposit).filter(
        MarketWalletDeposit.reference_code == ref,
        MarketWalletDeposit.currency == WalletCurrency.VND,
    ).first()
    if not deposit:
        return None

    if deposit.status == WalletDepositStatus.credited:
        return {"success": True, "action": "market_deposit_already_done", "deposit_id": deposit.id}

    if deposit.status in (WalletDepositStatus.expired, WalletDepositStatus.cancelled,
                           WalletDepositStatus.failed, WalletDepositStatus.manual_review):
        if deposit.status == WalletDepositStatus.manual_review:
            return {"success": True, "action": "market_deposit_needs_review", "deposit_id": deposit.id}
        from sqlalchemy import text as _sql_text
        rows = db.execute(
            _sql_text(
                "UPDATE market_wallet_deposits SET status='manual_review', "
                "external_transaction_id=:txid, raw_transaction_data=:raw, "
                "failed_reason=:reason WHERE id=:id AND status=:prev_status"
            ),
            {
                "txid": str(tx_id), "raw": json.dumps(raw, ensure_ascii=False)[:5000],
                "reason": f"Chuyển khoản khớp mã {ref} sau khi yêu cầu đã {deposit.status.value} — cần admin kiểm tra.",
                "id": deposit.id, "prev_status": deposit.status.value,
            },
        )
        db.commit()
        if rows.rowcount == 0:
            return {"success": True, "action": "market_deposit_already_done", "deposit_id": deposit.id}
        return {"success": True, "action": "market_deposit_needs_review", "deposit_id": deposit.id}

    # Credit the VND amount requested (not the bank-transfer amount, which may
    # differ by rounding or fees). A 1đ tolerance handles minor bank fee quirks.
    credit_amount = deposit.vnd_credit_amount or deposit.amount
    if amount_in < credit_amount - 1:
        return {"success": True, "action": "market_deposit_insufficient", "deposit_id": deposit.id}

    now_iso = datetime.utcnow().isoformat(sep=" ")
    try:
        market_wallet_service.credit_market_wallet(
            db, deposit.admin_user_id, WalletCurrency.VND, credit_amount,
            WalletTxType.deposit, deposit_id=deposit.id,
            note=f"Auto-credited SePay bank transfer — Ví chợ (txid={tx_id})",
            actor="system",
            extra_updates=[(
                "UPDATE market_wallet_deposits SET status='credited', external_transaction_id=?, "
                "verified_at=?, credited_at=?, raw_transaction_data=? "
                "WHERE id=? AND status NOT IN ('credited','failed','expired','cancelled')",
                (tx_id, now_iso, now_iso, json.dumps(raw, ensure_ascii=False)[:5000], deposit.id),
            )],
        )
    except market_wallet_service.AlreadyProcessedError:
        return {"success": True, "action": "market_deposit_already_done", "deposit_id": deposit.id}

    logger.info(f"[sepay] market wallet deposit credited: deposit_id={deposit.id} amount={credit_amount} tx_id={tx_id}")
    return {"success": True, "action": "market_deposit_credited", "deposit_id": deposit.id}


def _notify_admin_deposit_needs_review(db: Session, deposit) -> None:
    """Fire-and-forget admin alert for a deposit escalated to manual_review
    (e.g. a late transfer matched after the deposit had already expired)."""
    try:
        asyncio.create_task(_notify_admin_deposit_needs_review_async(deposit.id))
    except RuntimeError:
        # No running event loop (e.g. a script/cron context) — log instead
        # of losing the alert silently.
        logger.warning(f"[wallet] deposit {deposit.id} needs manual review but no event loop to notify admin")


async def _notify_admin_deposit_needs_review_async(deposit_id: int):
    try:
        from database import SessionLocal
        from models import WalletDeposit
        from services.bot_service import bot_manager
        from bot.handlers import _get_admin_id
        db = SessionLocal()
        try:
            deposit = db.query(WalletDeposit).filter(WalletDeposit.id == deposit_id).first()
            if not deposit:
                return
            admin_id = _get_admin_id(db)
            if not admin_id or not bot_manager.is_running():
                return
            from bot.notifier import notify_admin_wallet_deposit_request
            await notify_admin_wallet_deposit_request(bot_manager._application.bot, deposit, admin_id)
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[wallet] _notify_admin_deposit_needs_review_async error: {e}")


async def _notify_deposit_credited_async(deposit_id: int):
    try:
        from database import SessionLocal
        from models import WalletDeposit, User
        from services.bot_service import bot_manager
        from services import wallet_service
        if not bot_manager.is_running():
            return
        db = SessionLocal()
        try:
            deposit = db.query(WalletDeposit).filter(WalletDeposit.id == deposit_id).first()
            if not deposit:
                return
            from bot.notifier import notify_user_wallet_deposit_confirmed
            from bot.i18n import get_user_lang
            lang = get_user_lang(db, deposit.telegram_user_id)
            chat_id = deposit.chat_id or deposit.telegram_user_id
            user = db.query(User).filter(User.telegram_id == deposit.telegram_user_id).first()
            new_balance = wallet_service.get_balance(user, deposit.currency) if user else None
            await notify_user_wallet_deposit_confirmed(
                bot_manager._application.bot, chat_id, deposit, lang=lang, new_balance=new_balance,
            )
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[wallet] _notify_deposit_credited_async error: {e}")


def _parse_tx_date(raw_date: str) -> datetime | None:
    if not raw_date:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            return datetime.strptime(str(raw_date), fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(str(raw_date).replace("Z", ""))
    except Exception:
        return datetime.utcnow()


def process_webhook_transaction(db: Session, raw: dict) -> dict:
    """
    Save and match a SePay webhook event.
    Idempotent: duplicate tx_id → ignored via unique constraint check.
    """
    tx_data = _normalize_sepay_transaction(raw)
    tx_id = tx_data["transaction_id"]
    if not tx_id:
        return {"success": False, "reason": "missing_transaction_id"}

    amount_in = tx_data["amount_in"]
    if amount_in <= 0:
        return {"success": True, "action": "ignored_outgoing"}

    existing = db.query(PaymentTransaction).filter_by(
        provider="sepay", external_transaction_id=tx_id
    ).first()
    if existing:
        return {"success": True, "action": "duplicate_ignored"}

    tx_date = _parse_tx_date(tx_data["transaction_date"])

    tx = PaymentTransaction(
        provider="sepay",
        external_transaction_id=tx_id,
        gateway=tx_data["gateway"],
        transaction_date=tx_date,
        account_number=tx_data["account_number"],
        transfer_content=tx_data["transfer_content"],
        amount_in=amount_in,
        amount_out=tx_data["amount_out"],
        reference_code=tx_data["reference_code"],
        match_status="unmatched",
        raw_json=json.dumps(raw, ensure_ascii=False)[:10000],
    )

    sepay_cfg = get_sepay_config(db)
    prefix = (sepay_cfg.payment_prefix or "AIC") if sepay_cfg else "AIC"
    payment_code = _find_payment_code(tx_data["transfer_content"], prefix)

    order = None
    if payment_code:
        order = db.query(Order).filter(Order.payment_code == payment_code).first()

    if not order:
        deposit_result = _try_credit_vnd_deposit(
            db, tx_id, amount_in, tx_data["transfer_content"], raw, tx_date,
        )
        if deposit_result:
            tx.matched_deposit_id = deposit_result.get("deposit_id")
            tx.match_status = deposit_result.get("action")
            db.add(tx)
            db.commit()
            return deposit_result
        # If no customer wallet deposit matched, try ví chợ (market wallet) bank deposit
        market_deposit_result = _try_credit_market_vnd_deposit(
            db, tx_id, amount_in, tx_data["transfer_content"], raw, tx_date,
        )
        if market_deposit_result:
            tx.matched_market_deposit_id = market_deposit_result.get("deposit_id")
            tx.match_status = market_deposit_result.get("action")
            db.add(tx)
            db.commit()
            return market_deposit_result
        db.add(tx)
        db.commit()
        return {"success": True, "action": "unmatched"}

    tx.matched_order_id = order.id

    if order.status == OrderStatus.payment_expired:
        tx.match_status = "late_payment"
        db.add(tx)
        db.commit()
        return {"success": True, "action": "late_payment", "order_id": order.id}

    if order.status in (OrderStatus.completed, OrderStatus.cancelled,
                         OrderStatus.api_failed, OrderStatus.failed):
        tx.match_status = "late_payment"
        db.add(tx)
        db.commit()
        return {"success": True, "action": "order_already_done", "order_id": order.id}

    current_paid = order.paid_amount or 0.0
    new_paid = current_paid + amount_in
    expected = order.expected_amount or order.total_price
    allow_overpay = sepay_cfg.allow_overpay if sepay_cfg else True

    order.paid_amount = new_paid
    order.payment_transaction_id = tx_id

    if new_paid < expected:
        order.payment_status = PaymentStatus.partial
        tx.match_status = "partial"
        action = "partial"
    elif abs(new_paid - expected) < 1:
        order.payment_status = PaymentStatus.paid
        order.paid_at = datetime.utcnow()
        tx.match_status = "matched"
        action = "paid"
    else:
        if allow_overpay:
            order.payment_status = PaymentStatus.overpaid
            order.paid_at = datetime.utcnow()
            tx.match_status = "matched"
            action = "overpaid"
        else:
            order.payment_status = PaymentStatus.paid
            order.paid_at = datetime.utcnow()
            surplus = new_paid - expected
            order.notes = (order.notes or "") + f"\nThừa {format_vnd(surplus)}đ — chờ hoàn tiền."
            tx.match_status = "matched"
            action = "paid"

    order.updated_at = datetime.utcnow()
    db.add(tx)
    db.commit()
    db.refresh(order)

    return {
        "success": True,
        "action": action,
        "order_id": order.id,
        "order": order,
        "new_paid": new_paid,
        "expected": expected,
    }


# ── Process paid order (idempotent — shared by all payment methods) ────────────

async def process_paid_order(order_id: int):
    """
    Background task: call API source and deliver after payment confirmed.
    Idempotent — safe to call multiple times.
    POST /buy is called exactly once per order.
    """
    from database import SessionLocal
    from services.order_service import _poll_source_order, _processing_keys
    from services.normalize import normalize_delivery_items
    from services.product_service import get_best_source, get_product_sources_count
    from integrations.manager import api_manager
    from models import Product, DeliveryMode, OrderSourceAttempt

    if order_id in _processing_paid:
        return
    _processing_paid.add(order_id)

    db = SessionLocal()
    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            return

        # Gate: only process orders that are pending_payment or waiting_manual_verification
        if order.status not in (OrderStatus.pending_payment, OrderStatus.waiting_manual_verification):
            logger.info(f"[payment] order {order_id} status={order.status} — skip")
            return

        # Gate: payment must be confirmed
        if order.payment_status not in (PaymentStatus.paid, PaymentStatus.overpaid):
            logger.warning(f"[payment] order {order_id} payment_status={order.payment_status} — not ready")
            return

        # Membership rank recompute — fires exactly once per order thanks to
        # the gates above + the _processing_paid guard. Never blocks payment
        # processing on failure (see rank_service.recompute_user_rank).
        try:
            from services.rank_service import recompute_user_rank
            from services.bot_service import bot_manager
            bot = bot_manager._application.bot if bot_manager.is_running() else None
            await recompute_user_rank(db, order.telegram_user_id, bot=bot)
        except Exception as e:
            logger.error(f"[payment] rank recompute failed for order {order_id}: {e}")

        # Transition to processing
        order.status = OrderStatus.processing_api
        db.commit()

        # Delete QR + old messages, send "acquiring items" interim
        await _send_payment_confirmed_interim(order, db)

        product = db.query(Product).filter(Product.id == order.product_id).first()

        # manual_stock → deliver automatically from local inventory ("kho tài khoản")
        if product and product.delivery_mode == DeliveryMode.manual_stock:
            from services.inventory_service import deliver_from_local_inventory
            # deliver_from_local_inventory manages its own _processing_paid guard;
            # release it here so that inner guard doesn't self-block.
            _processing_paid.discard(order_id)
            await deliver_from_local_inventory(order_id)
            return

        # manual_admin (and legacy "manual") → admin handles delivery by hand
        if not product or product.delivery_mode != DeliveryMode.api_auto:
            order.status = OrderStatus.pending_manual
            db.commit()
            await _notify_admin_manual_needed(order, db)
            return

        # Check if any sources exist
        sources_count = get_product_sources_count(db, order.product_id)
        source = get_best_source(db, order.product_id)

        if not source:
            if sources_count > 0:
                # Sources exist but all out of stock
                order.status = OrderStatus.paid_waiting_stock
                db.commit()
                await _notify_paid_waiting_stock(order, db)
            else:
                # No sources configured at all
                order.status = OrderStatus.api_failed
                db.commit()
                await _notify_paid_api_failed(order, db, "Không tìm thấy nguồn hàng")
            return

        idem_key = order.order_code
        if idem_key in _processing_keys:
            return
        _processing_keys.add(idem_key)

        try:
            from services.shared_catalog import resolve_api_product, resolve_api_connection
            src_api_product = resolve_api_product(db, source)
            src_connection = resolve_api_connection(db, src_api_product)
            adapter = api_manager.get_adapter(src_connection)
            # AI Center Buyer (and any other email-requiring supplier) needs
            # a buyer email on every purchase; the bot doesn't
            # collect one from shoppers, so a deterministic per-user
            # placeholder is used. Adapters that don't need it (Zampto/Custom)
            # simply ignore it.
            buyer_email = f"tguser{order.telegram_user_id}@aicenter-orders.local"
            buy_result = await adapter.buy_product(
                product_id=src_api_product.external_product_id,
                quantity=order.quantity,
                idempotency_key=idem_key,
                buyer_email=buyer_email,
                requires_customer_email=bool(src_api_product.external_requires_customer_email),
                requires_slot_months=bool(src_api_product.external_requires_slot_months),
            )

            attempt = OrderSourceAttempt(
                order_id=order.id,
                product_source_id=source.id,
                attempt_number=1,
                status="success" if buy_result.get("success") else "failed",
                error_message=(buy_result.get("message") or "")[:500] if not buy_result.get("success") else None,
                external_order_id=buy_result.get("order_id"),
            )
            db.add(attempt)
            db.commit()

            if not buy_result.get("success"):
                # "Cannot buy your own product" (CanBoSo's OWN_PRODUCT code)
                # means the connection's API key is the same seller account
                # that listed this exact item on the marketplace — a
                # permanent, supplier-side condition that can never succeed,
                # not a transient stockout. Left active, this source would
                # keep getting picked for every future order of this
                # product, charging the customer and forcing the admin to
                # manually fulfill every single time. Disable it so
                # get_best_source() skips straight to another source (or
                # correctly reports "no source" instead of silently
                # re-trying a source that is broken forever).
                buy_error_msg = buy_result.get("message") or ""
                if "own_product" in buy_error_msg.lower() or "cannot buy your own product" in buy_error_msg.lower():
                    source.is_active = False
                    logger.warning(
                        f"[payment] disabling product_source_id={source.id} — supplier rejected as own-seller product: {buy_error_msg[:200]}"
                    )
                # Check stock again — might have run out between check and buy
                source_check = get_best_source(db, order.product_id)
                if not source_check:
                    order.status = OrderStatus.paid_waiting_stock
                    db.commit()
                    await _notify_paid_waiting_stock(order, db)
                else:
                    order.status = OrderStatus.api_failed
                    db.commit()
                    await _notify_paid_api_failed(order, db, (buy_result.get("message") or "API error")[:200])
                return

            raw_data = buy_result.get("data", {})

            # "slot"-type items are never delivered instantly — the purchase just files a request the
            # seller must fulfill by hand. Skip item extraction/polling and
            # go straight to the seller-pending status. Any supplier without
            # this concept (external_item_type is None) keeps the original
            # "account" behavior below, unchanged.
            is_slot_item = (src_api_product.external_item_type or "").lower() == "slot"

            if is_slot_item:
                items = []
                order_data = raw_data.get("order", raw_data)
                external_order_code = (
                    order_data.get("order_code") or
                    order_data.get("order_id") or
                    buy_result.get("order_id") or ""
                )
                order.status = OrderStatus.pending_seller_fulfillment
                order.api_connection_id = src_api_product.api_connection_id
                order.external_order_id = buy_result.get("order_id")
                order.external_order_code = external_order_code
                order.source_unit_price = src_api_product.external_price
                safe_data = {k: v for k, v in raw_data.items() if k not in ("balance_after", "balance")}
                order.delivery_data = json.dumps(safe_data, ensure_ascii=False)
                order.delivery_items = json.dumps([], ensure_ascii=False)
                order.updated_at = datetime.utcnow()
                db.commit()
                db.refresh(order)
            else:
                items = normalize_delivery_items(raw_data)

                if not items and buy_result.get("order_id"):
                    polled_data, items = await _poll_source_order(adapter, buy_result["order_id"])
                    if polled_data:
                        raw_data = polled_data

                order_data = raw_data.get("order", raw_data)
                external_order_code = (
                    order_data.get("order_code") or
                    order_data.get("order_id") or
                    buy_result.get("order_id") or ""
                )

                if items and len(items) < order.quantity:
                    order.status = OrderStatus.partial_delivery
                    order.partial_count = len(items)
                elif items:
                    order.status = OrderStatus.completed
                else:
                    order.status = OrderStatus.pending_manual

                order.api_connection_id = src_api_product.api_connection_id
                order.external_order_id = buy_result.get("order_id")
                order.external_order_code = external_order_code
                order.source_unit_price = src_api_product.external_price
                safe_data = {k: v for k, v in raw_data.items() if k not in ("balance_after", "balance")}
                order.delivery_data = json.dumps(safe_data, ensure_ascii=False)
                order.delivery_items = json.dumps(items, ensure_ascii=False)
                order.updated_at = datetime.utcnow()
                db.commit()
                db.refresh(order)

        finally:
            _processing_keys.discard(idem_key)

        if product:
            product.sold_count = (product.sold_count or 0) + order.quantity
            db.commit()

        if order.status == OrderStatus.completed:
            await _debit_market_wallet_for_sale(order, product, db)

        if order.status == OrderStatus.pending_seller_fulfillment:
            await _notify_pending_seller_fulfillment(order, db)
            return

        await _deliver_to_user(order, db)

    except Exception as e:
        logger.error(f"[payment] process_paid_order {order_id} error: {e}")
        try:
            order = db.query(Order).filter(Order.id == order_id).first()
            if order and order.status == OrderStatus.processing_api:
                order.status = OrderStatus.api_failed
                db.commit()
                await _notify_paid_api_failed(order, db, str(e)[:200])
        except Exception:
            pass
    finally:
        _processing_paid.discard(order_id)
        db.close()


async def _deliver_to_user(order: Order, db: Session):
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        cfg = db.query(TelegramBotConfig).first()
        support = cfg.support_username if cfg else ""
        admin_id = cfg.admin_telegram_id if cfg else ""
        bot = bot_manager._application.bot

        # The order's outcome is about to be sent — safe to remove the QR/instruction
        # message now (order is guaranteed Paid/Overpaid by process_paid_order's gate).
        await cleanup_payment_qr(bot, order, db)

        sv = order.status.value if hasattr(order.status, "value") else str(order.status)

        if sv == "completed":
            from bot.notifier import notify_user_delivery, notify_admin_payment_success
            await notify_user_delivery(bot, order.telegram_user_id, order, support_username=support, db=db)
            if admin_id:
                await notify_admin_payment_success(bot, order, admin_id)
        elif sv == "partial_delivery":
            items_list = json.loads(order.delivery_items) if order.delivery_items else []
            from bot.notifier import notify_admin_partial_delivery
            if admin_id:
                await notify_admin_partial_delivery(bot, order, admin_id, len(items_list))
        elif sv in ("api_failed", "pending_manual"):
            from bot.notifier import notify_user_api_failed_after_payment, notify_admin_api_failed_after_payment
            await notify_user_api_failed_after_payment(bot, order.telegram_user_id, order)
            if admin_id:
                await notify_admin_api_failed_after_payment(bot, order, admin_id)
    except Exception as e:
        logger.error(f"[payment] _deliver_to_user error: {e}")


async def _notify_paid_api_failed(order: Order, db: Session, reason: str = ""):
    try:
        from services.wallet_service import refund_order_to_wallet
        await refund_order_to_wallet(db, order, reason="API nguồn lỗi sau khi thanh toán")

        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        cfg = db.query(TelegramBotConfig).first()
        admin_id = cfg.admin_telegram_id if cfg else ""
        bot = bot_manager._application.bot
        from bot.notifier import notify_user_api_failed_after_payment, notify_admin_api_failed_after_payment
        await cleanup_payment_qr(bot, order, db)
        await notify_user_api_failed_after_payment(bot, order.telegram_user_id, order)
        if admin_id:
            await notify_admin_api_failed_after_payment(bot, order, admin_id, reason)
    except Exception as e:
        logger.error(f"[payment] _notify_paid_api_failed error: {e}")


async def _notify_paid_waiting_stock(order: Order, db: Session):
    """Payment received but source ran out of stock unexpectedly."""
    try:
        from services.wallet_service import refund_order_to_wallet
        await refund_order_to_wallet(db, order, reason="Nguồn hết hàng sau khi thanh toán")

        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        from bot.i18n import t, get_user_lang
        cfg = db.query(TelegramBotConfig).first()
        admin_id = cfg.admin_telegram_id if cfg else ""
        bot = bot_manager._application.bot
        lang = get_user_lang(db, order.telegram_user_id)
        chat_id = order.payment_chat_id or order.telegram_user_id
        await cleanup_payment_qr(bot, order, db)
        await bot.send_message(
            chat_id=int(chat_id),
            text=t(lang, "paid_waiting_stock_user"),
            parse_mode="HTML",
        )
        if admin_id:
            import html
            product_name = order.product.name if order.product else str(order.product_id)
            await bot.send_message(
                chat_id=int(admin_id),
                text=(
                    f"⚠️ <b>ĐÃ NHẬN TIỀN — NGUỒN HẾT HÀNG!</b>\n\n"
                    f"📋 Đơn: <code>{order.order_code}</code>\n"
                    f"📦 Sản phẩm: {html.escape(product_name)}\n"
                    f"👤 User: <code>{order.telegram_user_id}</code>\n"
                    f"💰 Đã nhận: {format_vnd((order.paid_amount or 0))}đ\n\n"
                    "Cần giao thủ công, đổi nguồn hoặc hoàn tiền."
                ),
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"[payment] _notify_paid_waiting_stock error: {e}")


async def _debit_market_wallet_for_sale(order: Order, product, db: Session):
    """
    Ví chợ bookkeeping for a successfully completed chợ-sourced sale: debits
    cost-of-goods + the platform fee (% configurable via
    services/market_pricing.py, default 3%) from the selling tenant's market
    wallet, atomically and exactly once per order (guarded by
    orders.market_wallet_debited). No-op for the owner's own sales and for
    non-chợ (source_type != api) products — see services/market_stock_service.py
    for which products are gated in the first place.

    The pre-purchase gate in services/product_service.get_best_source already
    stops a new order from reaching this point once the wallet budget is
    exhausted, so InsufficientBalanceError here should only ever happen from a
    genuine race between two orders draining the last of the budget
    concurrently — logged as a critical alert for the owner to reconcile
    manually rather than silently swallowed or allowed to go negative.
    """
    try:
        if not product or product.source_type != SourceType.api:
            return
        if order.market_wallet_debited:
            return
        admin = db.query(AdminUser).filter(AdminUser.id == order.tenant_id).first()
        if not admin or admin.is_owner:
            return

        from services.market_pricing import get_platform_fee_percent
        cost = (order.source_unit_price or product.source_price or 0.0) * (order.quantity or 1)
        fee = round((order.total_price or 0.0) * (get_platform_fee_percent(db) / 100.0))
        if cost <= 0 and fee <= 0:
            return

        from services.market_wallet_service import debit_for_sale
        debit_for_sale(db, admin.id, order.id, cost, fee)
        logger.info(f"[market_wallet] debited order={order.order_code} tenant={admin.id} cost={cost} fee={fee}")
    except AlreadyProcessedError:
        pass
    except InsufficientBalanceError as e:
        logger.error(
            f"[market_wallet] INSUFFICIENT BALANCE debiting order={order.order_code} "
            f"tenant_id={order.tenant_id} — {e}. Sale already delivered; balance NOT taken negative. "
            "Owner should reconcile via /market-wallet/admin."
        )
    except Exception as e:
        logger.exception(f"[market_wallet] unexpected error debiting order={order.order_code}: {e}")


async def _notify_pending_seller_fulfillment(order: Order, db: Session):
    """Payment received; this is a slot-type item (e.g. AI Center Buyer) —
    the purchase just filed a request the seller still has to fulfill."""
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        from bot.i18n import t, get_user_lang
        cfg = db.query(TelegramBotConfig).first()
        admin_id = cfg.admin_telegram_id if cfg else ""
        bot = bot_manager._application.bot
        lang = get_user_lang(db, order.telegram_user_id)
        chat_id = order.payment_chat_id or order.telegram_user_id
        await cleanup_payment_qr(bot, order, db)
        await bot.send_message(
            chat_id=int(chat_id),
            text=t(lang, "pending_seller_fulfillment_user"),
            parse_mode="HTML",
        )
        if admin_id:
            import html
            product_name = order.product.name if order.product else str(order.product_id)
            await bot.send_message(
                chat_id=int(admin_id),
                text=(
                    f"⏳ <b>ĐƠN SLOT CHỜ SELLER XỬ LÝ</b>\n\n"
                    f"📋 Đơn: <code>{order.order_code}</code>\n"
                    f"📦 Sản phẩm: {html.escape(product_name)}\n"
                    f"👤 User: <code>{order.telegram_user_id}</code>\n"
                    f"🔗 Mã đơn nguồn: <code>{order.external_order_code or order.external_order_id or '—'}</code>"
                ),
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"[payment] _notify_pending_seller_fulfillment error: {e}")


async def _notify_admin_manual_needed(order: Order, db: Session):
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        cfg = db.query(TelegramBotConfig).first()
        admin_id = cfg.admin_telegram_id if cfg else ""
        bot = bot_manager._application.bot
        # Payment is confirmed and admin has been handed the order — the QR/
        # instruction message is no longer needed.
        await cleanup_payment_qr(bot, order, db)
        if admin_id:
            from bot.notifier import notify_admin_new_payment_pending
            await notify_admin_new_payment_pending(bot, order, admin_id)
    except Exception as e:
        logger.error(f"[payment] _notify_admin_manual_needed error: {e}")


# ── Safe message deletion ──────────────────────────────────────────────────────

async def safe_delete_message(bot, chat_id, message_id):
    if not message_id or not chat_id:
        return
    try:
        await bot.delete_message(chat_id=int(chat_id), message_id=int(message_id))
    except Exception as e:
        err = str(e).lower()
        if any(x in err for x in ("not found", "message_id_invalid", "can't be deleted",
                                   "message to delete", "badrequest")):
            return
        logger.debug(f"[safe_delete] chat={chat_id} msg={message_id}: {e}")


async def _delete_all_payment_messages(bot, order: Order, db: Session):
    """Delete the pre-purchase browsing messages (product card, quantity prompt).
    Does NOT touch the payment QR/instruction message — that one must survive
    until the order is actually delivered; see cleanup_payment_qr()."""
    chat_id = order.payment_chat_id or order.telegram_user_id
    await safe_delete_message(bot, chat_id, order.product_message_id)
    await safe_delete_message(bot, chat_id, order.quantity_prompt_message_id)
    order.product_message_id = None
    order.quantity_prompt_message_id = None
    db.commit()


async def delete_order_thread_messages(bot, order: Order, db: Session):
    """
    Delete every message tracked for this order — product card, quantity
    prompt, payment QR/instructions, and delivery result (text + file) —
    so "🛍 Mua tiếp" can clear the whole completed-purchase thread before
    showing a fresh product list, instead of leaving it to pile up in the
    chat. Best-effort: safe_delete_message() silently ignores messages that
    are already gone or too old to delete.
    """
    chat_id = order.payment_chat_id or order.telegram_user_id
    for attr in (
        "product_message_id", "quantity_prompt_message_id", "payment_message_id",
        "delivery_message_id", "delivery_file_message_id",
    ):
        await safe_delete_message(bot, chat_id, getattr(order, attr))
        setattr(order, attr, None)
    db.commit()


async def cleanup_payment_qr(bot, order: Order, db: Session):
    """
    Delete the payment QR/instruction message.

    Call this ONLY once the order has reached Paid/Overpaid status AND its
    outcome (delivery + invoice, a paid-but-out-of-stock notice, an API-failed
    notice, or a manual-handoff notice to the admin) is about to be sent to the
    user — never on a status-check button press, a regen-QR request, or a
    timeout while the order is still unpaid.
    """
    try:
        chat_id = order.payment_chat_id or order.telegram_user_id
        await safe_delete_message(bot, chat_id, order.payment_message_id)
        order.payment_message_id = None
        db.commit()
    except Exception as e:
        logger.error(f"[payment] cleanup_payment_qr error: {e}")


async def _send_payment_confirmed_interim(order: Order, db: Session):
    """
    After confirming payment: clean up the pre-purchase browsing messages and
    edit the existing QR/instruction message in place to show a "confirmed,
    processing" state. The QR/instruction message itself is intentionally left
    on screen (not deleted) — it is only removed later, right before the final
    delivery/invoice or outcome notice is sent (see cleanup_payment_qr()).
    Called *before* the API buy call.
    """
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        from bot.i18n import t, get_user_lang
        bot = bot_manager._application.bot
        lang = get_user_lang(db, order.telegram_user_id)
        await _delete_all_payment_messages(bot, order, db)
        chat_id = order.payment_chat_id or order.telegram_user_id
        text = t(lang, "payment_confirmed_interim")
        msg_id = order.payment_message_id
        edited = False
        if msg_id:
            try:
                if order.payment_message_type == "photo":
                    await bot.edit_message_caption(chat_id=int(chat_id), message_id=msg_id, caption=text)
                else:
                    await bot.edit_message_text(chat_id=int(chat_id), message_id=msg_id, text=text)
                edited = True
            except Exception:
                edited = False
        if not edited:
            await bot.send_message(chat_id=int(chat_id), text=text)
    except Exception as e:
        logger.error(f"[payment] _send_payment_confirmed_interim error: {e}")


# ── Expiry loop ────────────────────────────────────────────────────────────────

async def expire_payment_orders_loop():
    """Background loop: every 60 s, mark overdue pending_payment orders as expired."""
    from database import SessionLocal
    while True:
        await asyncio.sleep(60)
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            expired = (
                db.query(Order)
                .filter(
                    Order.status == OrderStatus.pending_payment,
                    Order.payment_expires_at < now,
                )
                .all()
            )
            for o in expired:
                o.status = OrderStatus.payment_expired
                o.payment_status = PaymentStatus.expired
                o.updated_at = now
                logger.info(f"[payment] expired order {o.order_code}")
            if expired:
                db.commit()
        except Exception as e:
            logger.error(f"[payment] expiry loop error: {e}")
        finally:
            db.close()
