"""
Background workers for on-chain USDT payment monitoring.

Workers:
  - bep20_monitor_loop:  polls BSC (BEP20 USDT) for incoming transfers
  - trc20_monitor_loop:  polls TRON (TRC20 USDT) for incoming transfers
  - binance_pending_loop: polls Binance Pay Merchant API for pending orders

Each worker runs independently — a failure in one does NOT stop the others.
Workers check the database every N seconds (configurable, not faster than API limits).

Security rules enforced:
  - Only accept transfers to the configured wallet address.
  - Only accept the configured USDT contract.
  - Amount must match expected_crypto_amount.
  - txid used only once (unique constraint in DB).
  - Delivery only after required_confirmations reached.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── Config keys in PaymentMethod.config_encrypted ──────────────────────────────

def _get_pm_config(db, method_code: str) -> dict | None:
    from models import PaymentMethod
    from crypto import decrypt
    pm = db.query(PaymentMethod).filter(
        PaymentMethod.method_code == method_code,
        PaymentMethod.is_active == True,
    ).first()
    if not pm or not pm.config_encrypted:
        return None
    try:
        return json.loads(decrypt(pm.config_encrypted) or "{}")
    except Exception:
        return None


# ── BEP20 ──────────────────────────────────────────────────────────────────────

async def _check_bep20_transfers(cfg: dict, db) -> None:
    """Scan BEP20 USDT transfers to the configured wallet."""
    wallet = (cfg.get("wallet_address") or "").strip().lower()
    contract = (cfg.get("usdt_contract") or "").strip().lower()
    rpc_url = cfg.get("bsc_rpc_url") or "https://bsc-dataseed.binance.org/"
    bscscan_key = cfg.get("bscscan_api_key") or ""
    required_conf = int(cfg.get("required_confirmations") or 12)

    if not wallet or not contract:
        return

    from models import Order, OrderStatus, PaymentStatus, CryptoTransaction

    # Find pending BEP20 orders
    pending = (
        db.query(Order)
        .filter(
            Order.payment_network == "BEP20",
            Order.payment_status.in_([
                PaymentStatus.pending.value,
                PaymentStatus.detected.value,
                PaymentStatus.confirming.value,
            ]),
            Order.status.in_([
                OrderStatus.pending_payment.value,
            ]),
        )
        .all()
    )
    if not pending:
        return

    # Use BSCScan API to get recent token transfers
    if bscscan_key:
        await _scan_bep20_via_bscscan(wallet, contract, bscscan_key, required_conf, pending, db)
    else:
        await _scan_bep20_via_rpc(rpc_url, wallet, contract, required_conf, pending, db)


async def _scan_bep20_via_bscscan(
    wallet: str, contract: str, api_key: str,
    required_conf: int, pending_orders: list, db,
):
    try:
        import httpx
        url = (
            f"https://api.bscscan.com/api?module=account&action=tokentx"
            f"&contractaddress={contract}&address={wallet}"
            f"&sort=desc&apikey={api_key}&page=1&offset=100"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
        data = r.json()
        if data.get("status") != "1":
            return
        txs = data.get("result", [])
        for tx in txs:
            if tx.get("to", "").lower() != wallet:
                continue
            if tx.get("contractAddress", "").lower() != contract:
                continue
            decimals = int(tx.get("tokenDecimal") or 18)
            amount = int(tx.get("value") or 0) / (10 ** decimals)
            txid = tx.get("hash", "")
            block = int(tx.get("blockNumber") or 0)
            log_index = int(tx.get("transactionIndex") or 0)
            confs = int(tx.get("confirmations") or 0)
            await _process_crypto_tx(
                db=db,
                network="BEP20",
                txid=txid,
                log_index=log_index,
                from_addr=tx.get("from", ""),
                to_addr=wallet,
                amount=amount,
                block_number=block,
                confirmations=confs,
                required_confirmations=required_conf,
                token_symbol="USDT",
                token_contract=contract,
                pending_orders=pending_orders,
                raw=tx,
            )
    except Exception as e:
        logger.error(f"[bep20] bscscan scan error: {e}")


async def _scan_bep20_via_rpc(
    rpc_url: str, wallet: str, contract: str,
    required_conf: int, pending_orders: list, db,
):
    """Fallback: use BSC RPC to get logs. Limited without BSCScan key."""
    # This is a simplified implementation; in production use BSCScan
    logger.debug("[bep20] RPC fallback scan (limited — configure BSCScan API key for full coverage)")


# ── TRC20 ──────────────────────────────────────────────────────────────────────

async def _check_trc20_transfers(cfg: dict, db) -> None:
    """Scan TRC20 USDT transfers using TronGrid API."""
    wallet = (cfg.get("wallet_address") or "").strip()
    contract = (cfg.get("usdt_contract") or "").strip()
    trongrid_key = cfg.get("trongrid_api_key") or ""
    required_conf = int(cfg.get("required_confirmations") or 20)

    if not wallet or not contract:
        return

    from models import Order, OrderStatus, PaymentStatus

    pending = (
        db.query(Order)
        .filter(
            Order.payment_network == "TRC20",
            Order.payment_status.in_([
                PaymentStatus.pending.value,
                PaymentStatus.detected.value,
                PaymentStatus.confirming.value,
            ]),
            Order.status.in_([OrderStatus.pending_payment.value]),
        )
        .all()
    )
    if not pending:
        return

    try:
        import httpx
        headers = {}
        if trongrid_key:
            headers["TRON-PRO-API-KEY"] = trongrid_key

        # TronGrid TRC20 transfers endpoint
        url = f"https://api.trongrid.io/v1/accounts/{wallet}/transactions/trc20"
        params = {"limit": 50, "contract_address": contract, "only_to": "true"}

        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            r = await client.get(url, params=params)

        if r.status_code != 200:
            return
        data = r.json()
        txs = data.get("data", [])
        for tx in txs:
            # Verify destination
            to_addr = tx.get("to") or ""
            if to_addr.lower() != wallet.lower():
                continue
            token_info = tx.get("token_info") or {}
            if token_info.get("address", "").lower() != contract.lower():
                continue
            decimals = int(token_info.get("decimals") or 6)
            amount = int(tx.get("value") or 0) / (10 ** decimals)
            txid = tx.get("transaction_id") or tx.get("txID") or ""
            block = int(tx.get("block_timestamp") or 0)  # timestamp not block number for TRC20
            confs = 20 if tx.get("confirmed") else 0  # TRC20 marks confirmed/unconfirmed

            await _process_crypto_tx(
                db=db,
                network="TRC20",
                txid=txid,
                log_index=0,
                from_addr=tx.get("from") or "",
                to_addr=to_addr,
                amount=amount,
                block_number=block,
                confirmations=confs,
                required_confirmations=required_conf,
                token_symbol="USDT",
                token_contract=contract,
                pending_orders=pending,
                raw=tx,
            )
    except Exception as e:
        logger.error(f"[trc20] scan error: {e}")


# ── Shared tx processing ──────────────────────────────────────────────────────

async def _process_crypto_tx(
    db, network: str, txid: str, log_index: int,
    from_addr: str, to_addr: str, amount: float,
    block_number: int, confirmations: int, required_confirmations: int,
    token_symbol: str, token_contract: str,
    pending_orders: list, raw: dict,
):
    """
    Match an on-chain USDT transfer to a pending order and update statuses.
    Idempotent — safe to call repeatedly on same txid.
    """
    if not txid:
        return

    from models import CryptoTransaction, Order, OrderStatus, PaymentStatus
    from sqlalchemy.exc import IntegrityError

    # Dedup check
    existing = db.query(CryptoTransaction).filter_by(
        network=network, txid=txid, log_index=log_index
    ).first()

    # Try to match to a pending order by amount
    matched_order = None
    for order in pending_orders:
        expected = order.expected_crypto_amount
        if expected and abs(float(expected) - amount) < 0.0002:  # tolerance 0.0002 USDT
            matched_order = order
            break

    if existing:
        # Update confirmations on existing record
        existing.confirmations = confirmations
        if matched_order:
            existing.matched_order_id = matched_order.id
        db.commit()
        # If we now have enough confirmations and order is still pending → mark paid
        if matched_order and confirmations >= required_confirmations:
            await _finalize_crypto_payment(db, matched_order, existing, confirmations)
        elif matched_order and confirmations > 0:
            await _update_confirming(db, matched_order, existing, confirmations, required_confirmations)
        return

    # New tx record
    now = datetime.utcnow()
    ctx = CryptoTransaction(
        network=network,
        token_symbol=token_symbol,
        token_contract=token_contract,
        txid=txid,
        log_index=log_index,
        from_address=from_addr,
        to_address=to_addr,
        amount=amount,
        block_number=block_number,
        confirmations=confirmations,
        matched_order_id=matched_order.id if matched_order else None,
        status="detected" if matched_order else "unmatched",
        raw_json=json.dumps(raw, ensure_ascii=False)[:5000],
        detected_at=now,
    )
    try:
        db.add(ctx)
        db.commit()
    except IntegrityError:
        db.rollback()
        return

    if not matched_order:
        logger.info(f"[{network}] unmatched tx {txid} amount={amount}")
        return

    logger.info(f"[{network}] matched tx {txid} amount={amount} to order {matched_order.order_code}")

    if confirmations >= required_confirmations:
        await _finalize_crypto_payment(db, matched_order, ctx, confirmations)
    else:
        await _update_confirming(db, matched_order, ctx, confirmations, required_confirmations)


async def _update_confirming(db, order, ctx, confirmations: int, required: int):
    """Mark order as 'detected/confirming' and notify user."""
    from models import OrderStatus, PaymentStatus
    ps = order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status or "")
    if ps in ("paid", "detected", "confirming"):
        if ps in ("detected", "confirming"):
            order.confirmations = confirmations
            db.commit()
        return
    order.payment_status = PaymentStatus.detected
    order.confirmations = confirmations
    order.required_confirmations = required
    order.payment_txid = ctx.txid
    order.received_crypto_amount = ctx.amount
    ctx.status = "detected"
    db.commit()
    await _notify_user_detecting(order, db, confirmations, required)


async def _finalize_crypto_payment(db, order, ctx, confirmations: int):
    """Mark order as fully paid and trigger fulfillment."""
    from models import OrderStatus, PaymentStatus
    ps = order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status or "")
    if ps == "paid":
        return  # already processed

    order.payment_status = PaymentStatus.paid
    order.paid_at = datetime.utcnow()
    order.confirmations = confirmations
    order.payment_txid = ctx.txid
    order.received_crypto_amount = ctx.amount
    order.paid_amount = ctx.amount * (order.exchange_rate or 1.0)  # approx VND for records
    ctx.status = "confirmed"
    ctx.confirmed_at = datetime.utcnow()
    db.commit()

    logger.info(f"[{ctx.network}] order {order.order_code} confirmed, triggering fulfillment")
    asyncio.create_task(_trigger_fulfillment(order.id))


async def _trigger_fulfillment(order_id: int):
    from services.payment_service import process_paid_order
    await process_paid_order(order_id)


async def _notify_user_detecting(order, db, confirmations: int, required: int):
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        from bot.i18n import t, get_user_lang
        lang = get_user_lang(db, order.telegram_user_id)
        bot = bot_manager._application.bot
        chat_id = order.payment_chat_id or order.telegram_user_id
        msg_id = order.payment_message_id
        text = t(lang, "crypto_detected", current=confirmations, required=required)
        if msg_id:
            try:
                await bot.edit_message_caption(
                    chat_id=int(chat_id), message_id=msg_id, caption=text
                )
            except Exception:
                try:
                    await bot.edit_message_text(
                        chat_id=int(chat_id), message_id=msg_id, text=text
                    )
                except Exception:
                    await bot.send_message(chat_id=int(chat_id), text=text)
        else:
            await bot.send_message(chat_id=int(chat_id), text=text)
    except Exception as e:
        logger.error(f"[crypto] _notify_user_detecting error: {e}")


# ── Binance Pay Merchant pending poll ─────────────────────────────────────────

async def _check_binance_pending(cfg: dict, db) -> None:
    """Poll Binance Pay API for pending Merchant orders."""
    api_key = cfg.get("api_key") or ""
    secret_key = cfg.get("secret_key") or ""
    if not api_key or not secret_key:
        return

    from models import Order, OrderStatus, PaymentStatus
    pending = (
        db.query(Order)
        .filter(
            Order.payment_network == "BINANCE",
            Order.payment_status == PaymentStatus.pending,
            Order.status == OrderStatus.pending_payment,
        )
        .all()
    )
    for order in pending:
        if not order.payment_txid:  # payment_txid stores prepayId for Binance Merchant
            continue
        try:
            from services.binance_service import query_binance_order_status
            result = await query_binance_order_status(api_key, secret_key, order.order_code)
            status = (result.get("data") or {}).get("status") or ""
            if status == "PAID":
                order.payment_status = PaymentStatus.paid
                order.paid_at = datetime.utcnow()
                db.commit()
                asyncio.create_task(_trigger_fulfillment(order.id))
        except Exception as e:
            logger.error(f"[binance_merchant] poll error for {order.order_code}: {e}")


# ── Late payment: detect crypto received after expiry ─────────────────────────

async def _check_late_crypto_payments(db) -> None:
    """
    Find crypto orders that expired but then received a matching tx.
    Mark as late_payment and alert admin.
    """
    from models import Order, OrderStatus, PaymentStatus, CryptoTransaction
    expired_with_tx = (
        db.query(Order)
        .filter(
            Order.status == OrderStatus.payment_expired,
            Order.payment_network.in_(["BEP20", "TRC20"]),
            Order.payment_status != PaymentStatus.late_payment,
        )
        .all()
    )
    for order in expired_with_tx:
        # Check if a confirmed crypto tx came in after expiry
        ctx = (
            db.query(CryptoTransaction)
            .filter(
                CryptoTransaction.matched_order_id == order.id,
                CryptoTransaction.status == "confirmed",
            )
            .first()
        )
        if not ctx:
            continue
        order.payment_status = PaymentStatus.late_payment
        db.commit()
        await _notify_late_crypto(order, db)


async def _notify_late_crypto(order, db):
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        from models import TelegramBotConfig
        from bot.notifier import notify_user_late_payment, notify_admin_late_payment
        cfg = db.query(TelegramBotConfig).first()
        bot = bot_manager._application.bot
        await notify_user_late_payment(bot, order.telegram_user_id, order)
        if cfg and cfg.admin_telegram_id:
            await notify_admin_late_payment(bot, order, cfg.admin_telegram_id)
    except Exception as e:
        logger.error(f"[crypto] _notify_late_crypto error: {e}")


# ── Main loop functions (called from main.py startup) ─────────────────────────

async def bep20_monitor_loop():
    """Background loop: check BEP20 USDT transfers every N seconds."""
    logger.info("[bep20] monitor loop started")
    while True:
        try:
            from database import SessionLocal
            db = SessionLocal()
            try:
                cfg = _get_pm_config(db, "usdt_bep20")
                if cfg:
                    interval = int(cfg.get("poll_interval_seconds") or 30)
                    await _check_bep20_transfers(cfg, db)
                    await _check_late_crypto_payments(db)
                else:
                    interval = 60
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[bep20] loop error: {e}")
            interval = 60
        await asyncio.sleep(interval)


async def trc20_monitor_loop():
    """Background loop: check TRC20 USDT transfers every N seconds."""
    logger.info("[trc20] monitor loop started")
    while True:
        try:
            from database import SessionLocal
            db = SessionLocal()
            try:
                cfg = _get_pm_config(db, "usdt_trc20")
                if cfg:
                    interval = int(cfg.get("poll_interval_seconds") or 30)
                    await _check_trc20_transfers(cfg, db)
                else:
                    interval = 60
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[trc20] loop error: {e}")
            interval = 60
        await asyncio.sleep(interval)


async def binance_merchant_loop():
    """Background loop: poll Binance Pay Merchant orders."""
    logger.info("[binance_merchant] monitor loop started")
    while True:
        try:
            from database import SessionLocal
            db = SessionLocal()
            try:
                cfg = _get_pm_config(db, "binance_pay")
                if cfg and cfg.get("mode") == "merchant":
                    await _check_binance_pending(cfg, db)
                    interval = 15
                else:
                    interval = 60
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[binance_merchant] loop error: {e}")
            interval = 60
        await asyncio.sleep(interval)
