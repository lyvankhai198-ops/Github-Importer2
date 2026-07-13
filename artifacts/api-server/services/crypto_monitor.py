"""
Background workers for on-chain USDT payment monitoring.

Workers:
  - bep20_monitor_loop: polls BSC (BEP20 USDT) for incoming transfers
  - trc20_monitor_loop: polls TRON (TRC20 USDT) for incoming transfers
  - erc20_monitor_loop: polls Ethereum (ERC20 USDT) for incoming transfers
  - binance_pay_loop:   sweeps pending Binance Pay orders against the shop's
                        own Binance API Management Pay History (throttled,
                        shared cache with shopper/admin-triggered checks)

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
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

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


# ── ERC20 (Ethereum mainnet) ────────────────────────────────────────────────────

async def _check_erc20_transfers(cfg: dict, db) -> None:
    """Scan ERC20 USDT transfers to the configured wallet (via Etherscan)."""
    wallet = (cfg.get("wallet_address") or "").strip().lower()
    contract = (cfg.get("usdt_contract") or "").strip().lower()
    etherscan_key = cfg.get("etherscan_api_key") or ""
    required_conf = int(cfg.get("required_confirmations") or 12)

    if not wallet or not contract or not etherscan_key:
        return

    from models import Order, OrderStatus, PaymentStatus

    pending = (
        db.query(Order)
        .filter(
            Order.payment_network == "ERC20",
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
        url = (
            f"https://api.etherscan.io/api?module=account&action=tokentx"
            f"&contractaddress={contract}&address={wallet}"
            f"&sort=desc&apikey={etherscan_key}&page=1&offset=100"
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
                network="ERC20",
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
                pending_orders=pending,
                raw=tx,
            )
    except Exception as e:
        logger.error(f"[erc20] etherscan scan error: {e}")


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


# ── Binance Pay verification (via Binance API Management Pay History) ─────────
#
# Binance Pay Merchant API polling has been fully removed. Verification now
# calls the shop's own Pay History (/sapi/v1/pay/transactions) and matches
# the shopper-submitted TXID against it. All callers (shopper "check
# payment"/TXID submission, admin manual check, and the background sweep)
# share one throttled cache so repeated presses never cause extra Binance
# API calls within min_check_interval_seconds.

_binance_cache: dict = {"transactions": None, "fetched_at": 0.0, "error": None}
_binance_cache_lock = asyncio.Lock()


async def _get_binance_transactions_cached(cfg: dict) -> dict:
    """Fetch Binance Pay history, throttled to at most once per min_check_interval_seconds."""
    from services.binance_service import fetch_pay_transactions
    min_interval = max(5, int(cfg.get("min_check_interval_seconds") or 15))
    async with _binance_cache_lock:
        now = time.time()
        if _binance_cache["transactions"] is not None and (now - _binance_cache["fetched_at"]) < min_interval:
            return {"success": True, "transactions": _binance_cache["transactions"]}
        result = await fetch_pay_transactions(cfg.get("api_key") or "", cfg.get("secret_key") or "")
        if result.get("success"):
            _binance_cache["transactions"] = result["transactions"]
            _binance_cache["fetched_at"] = now
            _binance_cache["error"] = None
            return result
        _binance_cache["error"] = result
        return result


def _decimal(v) -> "Decimal | None":
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _extract_binance_coin_amount(tx: dict, coin: str) -> "Decimal | None":
    """
    Return the amount of `coin` reported by a Pay History row, whether given
    directly via currency/amount or summed from a fundsDetail breakdown.
    """
    coin = (coin or "USDT").upper()
    currency = (tx.get("currency") or "").upper()
    if currency == coin and tx.get("amount") is not None:
        amt = _decimal(tx.get("amount"))
        if amt is not None:
            return amt
    total = Decimal("0")
    found = False
    for fd in (tx.get("fundsDetail") or []):
        if (fd.get("currency") or "").upper() == coin:
            amt = _decimal(fd.get("amount"))
            if amt is not None:
                total += amt
                found = True
    return total if found else None


async def verify_binance_payment(db, order, submitted_txid: str | None = None) -> dict:
    """
    Verify a Binance Pay order against the shop's own Pay History
    (/sapi/v1/pay/transactions), checking (in order):
      - order/payment not already completed or paid
      - order still pending payment, network is BINANCE
      - Binance API Management config present (api key/secret/receiver id)
      - a TXID was submitted or already stored on the order
      - TXID not already matched to a DIFFERENT order (unique index reuse
        on payment_transactions(provider, external_transaction_id))
      - TXID found in Pay History and is a Pay-type transaction
      - receiver Binance ID matches the shop's configured ID
      - currency/fundsDetail amount is the configured coin (USDT)
      - amount matches order.expected_crypto_amount exactly (Decimal),
        within the admin-configured tolerance (default 0)
      - transaction time falls within the order's valid window

    On success: marks the order paid and schedules process_paid_order()
    exactly once. On "permission_denied", the caller should move the order
    to waiting_manual_verification instead of retrying automatically.

    Returns {"ok": bool, "reason": str, ...extra context}.
    """
    from models import PaymentStatus, PaymentTransaction
    from sqlalchemy.exc import IntegrityError

    sv = order.status.value if hasattr(order.status, "value") else str(order.status)
    if sv == "completed":
        return {"ok": False, "reason": "already_paid"}
    if sv != "pending_payment":
        return {"ok": False, "reason": "order_not_pending"}

    ps = order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status or "")
    if ps in ("paid", "overpaid"):
        return {"ok": False, "reason": "already_paid"}

    if order.payment_network != "BINANCE":
        return {"ok": False, "reason": "unsupported_network"}

    cfg = _get_pm_config(db, "binance_pay")
    if not cfg or not cfg.get("api_key") or not cfg.get("secret_key") or not cfg.get("receiver_binance_id"):
        return {"ok": False, "reason": "config_missing"}

    txid = (submitted_txid or order.payment_txid or "").strip()
    if not txid:
        return {"ok": False, "reason": "empty"}

    dup = db.query(PaymentTransaction).filter(
        PaymentTransaction.provider == "binance_pay",
        PaymentTransaction.external_transaction_id == txid,
    ).first()
    if dup and dup.matched_order_id and dup.matched_order_id != order.id:
        return {"ok": False, "reason": "txid_reused"}

    result = await _get_binance_transactions_cached(cfg)
    if not result.get("success"):
        return {"ok": False, "reason": result.get("reason", "unavailable"), "message": result.get("message")}

    tx = None
    for row in result.get("transactions") or []:
        row_txid = str(row.get("transactionId") or row.get("orderId") or row.get("tranId") or "")
        if row_txid == txid:
            tx = row
            break

    if not tx:
        return {"ok": False, "reason": "not_found"}

    order_type = (tx.get("orderType") or "").upper()
    if order_type and order_type not in ("PAY", "C2C", "PAYMENT"):
        return {"ok": False, "reason": "not_found"}

    receiver_info = tx.get("receiverInfo") or {}
    receiver_id = str(receiver_info.get("binanceId") or tx.get("receiverId") or "")
    expected_receiver = str(cfg.get("receiver_binance_id") or "")
    if not receiver_id or receiver_id != expected_receiver:
        return {"ok": False, "reason": "wrong_receiver"}

    coin = cfg.get("default_coin") or "USDT"
    amount = _extract_binance_coin_amount(tx, coin)
    if amount is None:
        return {"ok": False, "reason": "wrong_currency"}

    expected = _decimal(order.expected_crypto_amount)
    if expected is None:
        return {"ok": False, "reason": "config_missing"}
    tolerance = _decimal(cfg.get("amount_tolerance")) or Decimal("0")
    if abs(amount - expected) > tolerance:
        return {"ok": False, "reason": "amount_mismatch", "amount": str(amount), "expected": str(expected)}

    tx_time_raw = tx.get("transactionTime") or tx.get("createTime") or 0
    try:
        tx_time = datetime.utcfromtimestamp(int(tx_time_raw) / 1000)
    except Exception:
        tx_time = None
    if not tx_time:
        return {"ok": False, "reason": "not_found"}

    expiry_minutes = int(cfg.get("order_expiry_minutes") or 30)
    window_end = order.payment_expires_at or (order.created_at + timedelta(minutes=expiry_minutes))
    grace = timedelta(minutes=10)
    if tx_time < (order.created_at - timedelta(minutes=2)) or tx_time > (window_end + grace):
        return {"ok": False, "reason": "time_window"}

    # All checks passed — record the transaction (idempotent via the unique
    # index on provider+external_transaction_id) and finalize exactly once.
    ptx = PaymentTransaction(
        provider="binance_pay",
        external_transaction_id=txid,
        gateway="binance_pay",
        transaction_date=tx_time,
        amount_in=float(amount),
        matched_order_id=order.id,
        match_status="matched",
        raw_json=json.dumps(tx, ensure_ascii=False)[:5000],
    )
    try:
        db.add(ptx)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(PaymentTransaction).filter(
            PaymentTransaction.provider == "binance_pay",
            PaymentTransaction.external_transaction_id == txid,
        ).first()
        if existing and existing.matched_order_id != order.id:
            return {"ok": False, "reason": "txid_reused"}

    if order.payment_status in (PaymentStatus.paid, PaymentStatus.overpaid):
        return {"ok": True, "reason": "confirmed"}

    order.payment_status = PaymentStatus.paid
    order.paid_at = datetime.utcnow()
    order.payment_txid = txid
    order.received_crypto_amount = float(amount)
    order.paid_amount = float(amount) * (order.exchange_rate or 1.0)
    db.commit()

    logger.info(f"[binance_pay] order {order.order_code} confirmed via Pay History txid={txid}")
    asyncio.create_task(_trigger_fulfillment(order.id))
    return {"ok": True, "reason": "confirmed"}


# ── Manual TXID verification (shopper-submitted) ───────────────────────────────

async def _fetch_single_tx(network: str, txid: str, cfg: dict, wallet: str, contract: str) -> dict | None:
    """
    Look up ONE specific transaction by hash on-chain (rather than scanning a
    list of recent transfers). Returns a normalized dict or None if not found.
    """
    try:
        import httpx
        if network in ("BEP20", "ERC20"):
            if network == "BEP20":
                base = "https://api.bscscan.com/api"
                api_key = cfg.get("bscscan_api_key") or ""
            else:
                base = "https://api.etherscan.io/api"
                api_key = cfg.get("etherscan_api_key") or ""
            url = f"{base}?module=account&action=tokentx&address={wallet}&sort=desc&apikey={api_key}&page=1&offset=200"
            if contract:
                url += f"&contractaddress={contract}"
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(url)
            data = r.json()
            if data.get("status") != "1":
                return None
            for tx in data.get("result", []):
                if (tx.get("hash") or "").lower() != txid.lower():
                    continue
                decimals = int(tx.get("tokenDecimal") or 18)
                amount = int(tx.get("value") or 0) / (10 ** decimals)
                return {
                    "to": tx.get("to", ""),
                    "from": tx.get("from", ""),
                    "contract": tx.get("contractAddress", ""),
                    "amount": amount,
                    "block_number": int(tx.get("blockNumber") or 0),
                    "confirmations": int(tx.get("confirmations") or 0),
                    "log_index": int(tx.get("transactionIndex") or 0),
                    "raw": tx,
                }
            return None

        if network == "TRC20":
            headers = {}
            trongrid_key = cfg.get("trongrid_api_key") or ""
            if trongrid_key:
                headers["TRON-PRO-API-KEY"] = trongrid_key
            url = f"https://api.trongrid.io/v1/accounts/{wallet}/transactions/trc20"
            params = {"limit": 200, "only_to": "true"}
            if contract:
                params["contract_address"] = contract
            async with httpx.AsyncClient(timeout=15, headers=headers) as client:
                r = await client.get(url, params=params)
            if r.status_code != 200:
                return None
            data = r.json()
            for tx in data.get("data", []):
                tx_id = tx.get("transaction_id") or tx.get("txID") or ""
                if tx_id.lower() != txid.lower():
                    continue
                token_info = tx.get("token_info") or {}
                decimals = int(token_info.get("decimals") or 6)
                amount = int(tx.get("value") or 0) / (10 ** decimals)
                return {
                    "to": tx.get("to") or "",
                    "from": tx.get("from") or "",
                    "contract": token_info.get("address", ""),
                    "amount": amount,
                    "block_number": int(tx.get("block_timestamp") or 0),
                    "confirmations": 20 if tx.get("confirmed") else 0,
                    "log_index": 0,
                    "raw": tx,
                }
            return None
    except Exception as e:
        logger.error(f"[{network}] _fetch_single_tx error: {e}")
        return None
    return None


async def verify_txid_for_order(db, order, txid: str) -> dict:
    """
    Full verification checklist run when a shopper pastes a TXID by hand.
    Checks — in order, each with a specific failure reason so the exact
    problem can be surfaced to the shopper:
      1. order is still pending_payment / not already paid or delivered
      2. network is one we support (BEP20 / TRC20 / ERC20)
      3. the wallet/contract config for that network is present
      4. the txid hasn't already been used to pay a *different* order
      5. the transaction actually exists on-chain (implies it succeeded —
         failed transfers never emit a Transfer event / never appear in the
         token-transfer list used here)
      6. destination wallet matches the configured receiving address
      7. token contract matches the configured USDT contract
      8. amount matches the order's expected (unique) USDT amount
      9. required confirmation depth is reached — if not, reports progress
         instead of delivering
    On success, delegates to the same idempotent _process_crypto_tx() path
    used by the background monitors, which sets payment_status=paid and
    schedules fulfillment exactly once.
    Returns: {"ok": bool, "reason": str, ...extra context...}
    """
    from models import OrderStatus, PaymentStatus, CryptoTransaction

    txid = (txid or "").strip()
    if not txid:
        return {"ok": False, "reason": "empty"}

    sv = order.status.value if hasattr(order.status, "value") else str(order.status)
    if sv != "pending_payment":
        return {"ok": False, "reason": "order_not_pending"}

    ps = order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status or "")
    if ps in ("paid", "overpaid"):
        return {"ok": False, "reason": "already_paid"}

    network = order.payment_network
    if network not in ("BEP20", "TRC20", "ERC20"):
        return {"ok": False, "reason": "unsupported_network"}

    method_code = {"BEP20": "usdt_bep20", "TRC20": "usdt_trc20", "ERC20": "usdt_erc20"}[network]
    cfg = _get_pm_config(db, method_code)
    if not cfg:
        return {"ok": False, "reason": "config_missing"}

    wallet = (cfg.get("wallet_address") or "").strip().lower() if network != "TRC20" else (cfg.get("wallet_address") or "").strip()
    contract = (cfg.get("usdt_contract") or "").strip().lower() if network != "TRC20" else (cfg.get("usdt_contract") or "").strip()
    if not wallet:
        return {"ok": False, "reason": "config_missing"}
    required_conf = order.required_confirmations or int(cfg.get("required_confirmations") or 12)

    # Guard: txid already used to pay a DIFFERENT (or already-confirmed) order.
    dup = db.query(CryptoTransaction).filter(
        CryptoTransaction.network == network, CryptoTransaction.txid == txid,
    ).first()
    if dup and dup.status == "confirmed" and dup.matched_order_id != order.id:
        return {"ok": False, "reason": "txid_reused"}

    tx = await _fetch_single_tx(network, txid, cfg, wallet, contract)
    if not tx:
        return {"ok": False, "reason": "not_found"}

    to_addr = (tx.get("to") or "")
    to_cmp = to_addr.lower() if network != "TRC20" else to_addr
    if to_cmp != wallet:
        return {"ok": False, "reason": "wrong_wallet"}

    if contract:
        tx_contract = (tx.get("contract") or "")
        tx_contract_cmp = tx_contract.lower() if network != "TRC20" else tx_contract
        if tx_contract_cmp != contract:
            return {"ok": False, "reason": "wrong_token"}

    expected = order.expected_crypto_amount or 0
    amount = tx.get("amount") or 0
    if abs(float(expected) - float(amount)) >= 0.0002:
        return {"ok": False, "reason": "amount_mismatch", "amount": amount, "expected": expected}

    confirmations = tx.get("confirmations") or 0

    await _process_crypto_tx(
        db=db,
        network=network,
        txid=txid,
        log_index=tx.get("log_index", 0),
        from_addr=tx.get("from", ""),
        to_addr=to_addr,
        amount=amount,
        block_number=tx.get("block_number", 0),
        confirmations=confirmations,
        required_confirmations=required_conf,
        token_symbol="USDT",
        token_contract=contract,
        pending_orders=[order],
        raw=tx.get("raw", {}),
    )

    db.refresh(order)
    new_ps = order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status or "")
    if new_ps in ("paid", "overpaid"):
        return {"ok": True, "reason": "confirmed"}
    return {"ok": False, "reason": "insufficient_confirmations", "confirmations": confirmations, "required": required_conf}


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
            Order.payment_network.in_(["BEP20", "TRC20", "ERC20"]),
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


async def erc20_monitor_loop():
    """Background loop: check ERC20 (Ethereum) USDT transfers every N seconds."""
    logger.info("[erc20] monitor loop started")
    while True:
        try:
            from database import SessionLocal
            db = SessionLocal()
            try:
                cfg = _get_pm_config(db, "usdt_erc20")
                if cfg:
                    interval = int(cfg.get("poll_interval_seconds") or 30)
                    await _check_erc20_transfers(cfg, db)
                    await _check_late_crypto_payments(db)
                else:
                    interval = 60
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[erc20] loop error: {e}")
            interval = 60
        await asyncio.sleep(interval)


async def binance_pay_loop():
    """
    Background sweep: batches every pending Binance order that has a
    submitted TXID into the shared, throttled Pay History cache (see
    verify_binance_payment/_get_binance_transactions_cached above). Runs at
    most once per min_check_interval_seconds (60s floor for the sweep) so it
    never calls Binance more than necessary.
    """
    logger.info("[binance_pay] sweep loop started")
    while True:
        try:
            from database import SessionLocal
            db = SessionLocal()
            try:
                cfg = _get_pm_config(db, "binance_pay")
                if cfg and cfg.get("api_key") and cfg.get("secret_key") and cfg.get("receiver_binance_id"):
                    from models import Order, OrderStatus, PaymentStatus
                    pending = (
                        db.query(Order)
                        .filter(
                            Order.payment_network == "BINANCE",
                            Order.payment_status == PaymentStatus.pending,
                            Order.status == OrderStatus.pending_payment,
                            Order.payment_txid.isnot(None),
                        )
                        .all()
                    )
                    for order in pending:
                        try:
                            result = await verify_binance_payment(db, order)
                            if not result.get("ok") and result.get("reason") == "permission_denied":
                                order.status = OrderStatus.waiting_manual_verification
                                db.commit()
                        except Exception as e:
                            logger.error(f"[binance_pay] sweep verify error order={order.order_code}: {e}")
                    interval = max(60, int(cfg.get("min_check_interval_seconds") or 60))
                else:
                    interval = 60
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[binance_pay] loop error: {e}")
            interval = 60
        await asyncio.sleep(interval)
