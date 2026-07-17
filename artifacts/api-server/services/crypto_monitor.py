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
    pending_deposits = _get_pending_deposits(db, "BEP20")
    if not wallet or not contract:
        return
    if not pending and not pending_deposits:
        return

    # Use BSCScan API to get recent token transfers
    if bscscan_key:
        await _scan_bep20_via_bscscan(wallet, contract, bscscan_key, required_conf, pending, db, pending_deposits)
    else:
        await _scan_bep20_via_rpc(rpc_url, wallet, contract, required_conf, pending, db, pending_deposits)


def _get_pending_deposits(db, network: str) -> list:
    """
    Deposits to try matching an incoming on-chain USDT transfer against for
    `network` — active ones (still counting confirmations) PLUS recently
    expired/failed/cancelled ones. The latter group is included so a
    transfer that arrives late (after the deposit's window already closed)
    still gets linked and escalated to manual_review instead of silently
    landing as an unmatched on-chain transaction that nobody looks at —
    see _process_crypto_tx_for_deposits for how the two groups are handled
    differently.
    """
    from models import WalletDeposit, WalletDepositStatus
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(hours=48)
    return (
        db.query(WalletDeposit)
        .filter(
            WalletDeposit.network == network,
            WalletDeposit.status.in_([
                WalletDepositStatus.pending.value,
                WalletDepositStatus.detected.value,
                WalletDepositStatus.confirming.value,
                WalletDepositStatus.expired.value,
                WalletDepositStatus.failed.value,
                WalletDepositStatus.cancelled.value,
            ]),
            WalletDeposit.created_at >= cutoff,
        )
        .all()
    )


async def _scan_bep20_via_bscscan(
    wallet: str, contract: str, api_key: str,
    required_conf: int, pending_orders: list, db, pending_deposits: list = None,
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
            await _process_crypto_tx_for_deposits(
                db=db, network="BEP20", txid=txid, log_index=log_index,
                amount=amount, confirmations=confs, required_confirmations=required_conf,
                pending_deposits=pending_deposits or [],
            )
    except Exception as e:
        logger.error(f"[bep20] bscscan scan error: {e}")


_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_rpc_decimals_cache: dict = {}


async def _rpc_call(client, rpc_url: str, method: str, params: list):
    resp = await client.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    data = resp.json()
    if "error" in data:
        raise RuntimeError(str(data["error"]))
    return data.get("result")


async def _get_token_decimals(client, rpc_url: str, contract: str) -> int:
    if contract in _rpc_decimals_cache:
        return _rpc_decimals_cache[contract]
    try:
        result = await _rpc_call(client, rpc_url, "eth_call", [
            {"to": contract, "data": "0x313ce567"}, "latest",
        ])
        decimals = int(result, 16) if result else 18
    except Exception:
        decimals = 18
    _rpc_decimals_cache[contract] = decimals
    return decimals


async def _scan_bep20_via_rpc(
    rpc_url: str, wallet: str, contract: str,
    required_conf: int, pending_orders: list, db, pending_deposits: list = None,
):
    """
    Fallback path used when no BSCScan API key is configured: reads
    ERC20/BEP20 Transfer(address,address,uint256) event logs directly from
    the BSC JSON-RPC node instead of BSCScan's indexed API. Same matching
    (_process_crypto_tx / _process_crypto_tx_for_deposits) as the BSCScan
    path — this keeps deposit auto-crediting working even without a
    BSCScan key, since operators without one would otherwise see BEP20
    deposits silently never confirm.
    """
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            latest_hex = await _rpc_call(client, rpc_url, "eth_blockNumber", [])
            latest_block = int(latest_hex, 16)
            from_block = max(0, latest_block - 3000)
            padded_wallet = "0x" + wallet.replace("0x", "").rjust(64, "0")
            logs = await _rpc_call(client, rpc_url, "eth_getLogs", [{
                "fromBlock": hex(from_block),
                "toBlock": hex(latest_block),
                "address": contract,
                "topics": [_TRANSFER_TOPIC, None, padded_wallet],
            }])
            if not logs:
                return
            decimals = await _get_token_decimals(client, rpc_url, contract)
            for log in logs:
                try:
                    txid = log.get("transactionHash", "")
                    block_number = int(log.get("blockNumber", "0x0"), 16)
                    log_index = int(log.get("logIndex", "0x0"), 16)
                    from_topic = log.get("topics", [None, None, None])[1] or "0x0"
                    from_addr = "0x" + from_topic[-40:]
                    raw_amount = int(log.get("data", "0x0"), 16)
                    amount = raw_amount / (10 ** decimals)
                    confirmations = max(0, latest_block - block_number + 1)
                except Exception as e:
                    logger.error(f"[bep20] rpc log decode error: {e}")
                    continue

                await _process_crypto_tx(
                    db=db, network="BEP20", txid=txid, log_index=log_index,
                    from_addr=from_addr, to_addr=wallet, amount=amount,
                    block_number=block_number, confirmations=confirmations,
                    required_confirmations=required_conf, token_symbol="USDT",
                    token_contract=contract, pending_orders=pending_orders, raw=log,
                )
                await _process_crypto_tx_for_deposits(
                    db=db, network="BEP20", txid=txid, log_index=log_index,
                    amount=amount, confirmations=confirmations,
                    required_confirmations=required_conf,
                    pending_deposits=pending_deposits or [],
                )
    except Exception as e:
        logger.error(f"[bep20] rpc fallback scan error: {e}")


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
    pending_deposits = _get_pending_deposits(db, "ERC20")
    if not pending and not pending_deposits:
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
            await _process_crypto_tx_for_deposits(
                db=db, network="ERC20", txid=txid, log_index=log_index,
                amount=amount, confirmations=confs, required_confirmations=required_conf,
                pending_deposits=pending_deposits,
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
    pending_deposits = _get_pending_deposits(db, "TRC20")
    if not pending and not pending_deposits:
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
            await _process_crypto_tx_for_deposits(
                db=db, network="TRC20", txid=txid, log_index=0,
                amount=amount, confirmations=confs, required_confirmations=required_conf,
                pending_deposits=pending_deposits,
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


# ── Wallet deposit crypto matching ──────────────────────────────────────────────
#
# Runs right after _process_crypto_tx (order-matching) for the same tx, in
# the same scan pass — never an extra API call. Reuses the CryptoTransaction
# row _process_crypto_tx always creates/updates for this (network, txid,
# log_index) as its anti-replay/dedup source of truth: a txid already spent
# on an order can never also credit a deposit, and a deposit-matched txid
# already recorded here can't be re-matched to a different deposit.

_DEPOSIT_AMOUNT_TOLERANCE = 0.0002  # USDT — matches the order-matching tolerance


def _rank_deposit_candidates(candidates: list) -> list:
    """
    `_get_pending_deposits()` deliberately mixes active deposits with
    recently-terminal ones (see its docstring) so late transfers still get
    escalated instead of dropped. But when an amount collides across both
    groups (e.g. an old expired deposit happens to share its amount with a
    brand-new pending one), a plain "first match wins" scan is
    order-dependent and can wrongly route a live payment to the stale
    terminal deposit. Rank active deposits (pending/detected/confirming)
    ahead of terminal ones (expired/failed/cancelled) so a genuinely live
    deposit is always preferred; only fall back to a terminal deposit when
    no active one shares the amount. Within the same tier, prefer the
    oldest (first created) so this stays deterministic.
    """
    from models import WalletDepositStatus
    active = {WalletDepositStatus.pending, WalletDepositStatus.detected, WalletDepositStatus.confirming}

    def sort_key(dep):
        tier = 0 if dep.status in active else 1
        return (tier, dep.created_at or datetime.min)

    return sorted(candidates, key=sort_key)


async def _process_crypto_tx_for_deposits(
    db, network: str, txid: str, log_index: int, amount: float,
    confirmations: int, required_confirmations: int, pending_deposits: list,
):
    if not txid or not pending_deposits:
        return

    from models import CryptoTransaction

    candidates = [
        dep for dep in pending_deposits
        if dep.amount and abs(float(dep.amount) - amount) < _DEPOSIT_AMOUNT_TOLERANCE
    ]
    if not candidates:
        return
    matched = _rank_deposit_candidates(candidates)[0]

    existing = db.query(CryptoTransaction).filter_by(
        network=network, txid=txid, log_index=log_index
    ).first()
    if not existing:
        # _process_crypto_tx always creates this row first in the same scan
        # pass; if it's somehow missing, don't risk a racing duplicate insert.
        return
    if existing.matched_order_id:
        # This txid already paid an order — never also credit a deposit with it.
        return
    if existing.matched_deposit_id and existing.matched_deposit_id != matched.id:
        return

    from models import WalletDepositStatus

    existing.matched_deposit_id = matched.id
    existing.confirmations = max(existing.confirmations or 0, confirmations)
    db.commit()

    if matched.status in (WalletDepositStatus.expired, WalletDepositStatus.failed,
                           WalletDepositStatus.cancelled):
        # The window already closed before this transfer was seen — this is
        # real money that arrived late, so it must be escalated to a human
        # rather than silently sitting as an "unmatched" on-chain tx or
        # (worse) auto-credited without anyone knowing the deposit had
        # already been closed out.
        await _escalate_late_deposit_to_review(db, matched, txid)
        return

    if confirmations >= required_confirmations:
        await _finalize_wallet_deposit_crypto(db, matched, existing)
    elif confirmations > 0:
        await _update_deposit_confirming(db, matched, confirmations, required_confirmations)


async def _escalate_late_deposit_to_review(db, deposit, txid: str) -> None:
    from models import WalletDepositStatus

    if deposit.status == WalletDepositStatus.manual_review:
        return
    prev_status = deposit.status.value if hasattr(deposit.status, "value") else str(deposit.status)
    deposit.status = WalletDepositStatus.manual_review
    deposit.external_transaction_id = txid
    deposit.failed_reason = (
        f"Giao dịch on-chain khớp số tiền đến sau khi yêu cầu đã {prev_status} — cần admin kiểm tra."
    )
    db.commit()
    logger.warning(
        f"[wallet] deposit {deposit.id} late-matched by txid={txid} after status={prev_status} — escalated to manual_review"
    )
    try:
        from services.bot_service import bot_manager
        from bot.handlers import _get_admin_id
        admin_id = _get_admin_id(db)
        if admin_id and bot_manager.is_running():
            from bot.notifier import notify_admin_wallet_deposit_request
            await notify_admin_wallet_deposit_request(bot_manager._application.bot, deposit, admin_id)
    except Exception as e:
        logger.error(f"[wallet] late-deposit admin notify error deposit={deposit.id}: {e}")


async def _update_deposit_confirming(db, deposit, confirmations: int, required: int):
    from models import WalletDepositStatus

    if deposit.status == WalletDepositStatus.credited:
        return
    was_pending = deposit.status == WalletDepositStatus.pending
    deposit.status = WalletDepositStatus.detected if was_pending else WalletDepositStatus.confirming
    if was_pending:
        deposit.detected_at = datetime.utcnow()
    deposit.confirmations = confirmations
    deposit.required_confirmations = required
    db.commit()
    await _notify_deposit_progress(deposit, db, confirmations, required)


async def _finalize_wallet_deposit_crypto(db, deposit, ctx):
    from models import WalletDepositStatus, WalletCurrency, WalletTxType
    from services import wallet_service
    from services.wallet_service import AlreadyProcessedError

    if deposit.status == WalletDepositStatus.credited:
        return

    now_iso = datetime.utcnow().isoformat(sep=" ")
    try:
        wallet_service.credit_wallet(
            db, deposit.telegram_user_id, WalletCurrency.USDT, deposit.amount,
            WalletTxType.deposit, deposit_id=deposit.id,
            note=f"Auto-credited on-chain deposit ({ctx.network} txid={ctx.txid})",
            actor="system",
            extra_updates=[(
                "UPDATE wallet_deposits SET status='credited', confirmations=?, "
                "external_transaction_id=?, verified_at=?, credited_at=?, "
                "raw_transaction_data=? "
                "WHERE id=? AND status NOT IN ('credited','failed','expired','cancelled')",
                (ctx.confirmations, ctx.txid, now_iso, now_iso, ctx.raw_json, deposit.id),
            )],
        )
    except AlreadyProcessedError:
        return

    ctx.status = "confirmed"
    ctx.confirmed_at = datetime.utcnow()
    db.commit()
    db.refresh(deposit)
    logger.info(f"[{ctx.network}] wallet deposit {deposit.reference_code} credited via txid={ctx.txid}")
    await _notify_deposit_credited(deposit, db)


async def _notify_deposit_progress(deposit, db, confirmations: int, required: int):
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        from bot.i18n import t, get_user_lang
        lang = get_user_lang(db, deposit.telegram_user_id)
        bot = bot_manager._application.bot
        chat_id = deposit.chat_id or deposit.telegram_user_id
        text = t(lang, "wallet_deposit_detecting", ref=deposit.reference_code,
                 current=confirmations, required=required)
        await bot.send_message(chat_id=int(chat_id), text=text)
    except Exception as e:
        logger.error(f"[wallet] _notify_deposit_progress error: {e}")


async def _notify_deposit_credited(deposit, db):
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        from bot.notifier import notify_user_wallet_deposit_confirmed
        from bot.i18n import get_user_lang
        lang = get_user_lang(db, deposit.telegram_user_id)
        chat_id = deposit.chat_id or deposit.telegram_user_id
        await notify_user_wallet_deposit_confirmed(bot_manager._application.bot, chat_id, deposit, lang=lang)
    except Exception as e:
        logger.error(f"[wallet] _notify_deposit_credited error: {e}")


async def expire_wallet_deposits_loop():
    """
    Background loop: mark USDT/VND wallet deposits as expired once their
    window passes with nothing received, and flip crypto deposits stuck in
    detected/confirming with no further progress. Runs every 60s.
    """
    logger.info("[wallet] deposit expiry loop started")
    while True:
        try:
            from database import SessionLocal
            from models import WalletDeposit, WalletDepositStatus
            db = SessionLocal()
            try:
                now = datetime.utcnow()
                stale = (
                    db.query(WalletDeposit)
                    .filter(
                        WalletDeposit.status.in_([
                            WalletDepositStatus.pending.value,
                            WalletDepositStatus.detected.value,
                            WalletDepositStatus.confirming.value,
                        ]),
                        WalletDeposit.expires_at.isnot(None),
                        WalletDeposit.expires_at < now,
                    )
                    .all()
                )
                to_expire = []
                to_review = []
                for dep in stale:
                    if dep.status == WalletDepositStatus.pending:
                        # Nothing ever arrived — safe to just expire, the
                        # shopper can create a fresh deposit request.
                        dep.status = WalletDepositStatus.expired
                        to_expire.append(dep)
                    else:
                        # A matching transfer WAS detected but confirmations
                        # never finished accumulating before the window
                        # closed — money likely already moved, so this needs
                        # a human to look at it rather than silently expiring.
                        dep.status = WalletDepositStatus.manual_review
                        to_review.append(dep)
                db.commit()

                from services.bot_service import bot_manager
                if bot_manager.is_running():
                    from bot.notifier import notify_user_wallet_deposit_expired
                    from bot.i18n import get_user_lang
                    for dep in to_expire:
                        try:
                            lang = get_user_lang(db, dep.telegram_user_id)
                            chat_id = dep.chat_id or dep.telegram_user_id
                            await notify_user_wallet_deposit_expired(
                                bot_manager._application.bot, chat_id, dep, lang=lang,
                            )
                        except Exception as e:
                            logger.error(f"[wallet] expiry notify error deposit={dep.id}: {e}")

                if to_review:
                    from bot.handlers import _get_admin_id
                    admin_id = _get_admin_id(db)
                    if admin_id and bot_manager.is_running():
                        from bot.notifier import notify_admin_wallet_deposit_request
                        for dep in to_review:
                            try:
                                await notify_admin_wallet_deposit_request(
                                    bot_manager._application.bot, dep, admin_id,
                                )
                            except Exception as e:
                                logger.error(f"[wallet] manual_review admin notify error deposit={dep.id}: {e}")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[wallet] expire_wallet_deposits_loop error: {e}")
        await asyncio.sleep(60)


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


# ── Binance Pay wallet-deposit sweep ────────────────────────────────────────────
#
# Deposits (unlike orders) never have a shopper-submitted TXID to look up —
# there's nothing to "verify against a specific hash". Instead this mirrors
# verify_binance_payment's checklist but scans the whole Pay History cache
# and matches each pending deposit by receiver + coin + amount + time window.

async def _sweep_binance_deposits(db, cfg: dict, pending_deposits: list):
    from models import PaymentTransaction
    from sqlalchemy.exc import IntegrityError

    if not cfg.get("api_key") or not cfg.get("secret_key") or not cfg.get("receiver_binance_id"):
        return

    result = await _get_binance_transactions_cached(cfg)
    if not result.get("success"):
        return

    expected_receiver = str(cfg.get("receiver_binance_id") or "")
    coin = cfg.get("default_coin") or "USDT"
    tolerance = _decimal(cfg.get("amount_tolerance")) or Decimal("0")
    expiry_minutes = int(cfg.get("order_expiry_minutes") or 30)

    for tx in result.get("transactions") or []:
        order_type = (tx.get("orderType") or "").upper()
        if order_type and order_type not in ("PAY", "C2C", "PAYMENT"):
            continue
        receiver_info = tx.get("receiverInfo") or {}
        receiver_id = str(receiver_info.get("binanceId") or tx.get("receiverId") or "")
        if not receiver_id or receiver_id != expected_receiver:
            continue
        amount = _extract_binance_coin_amount(tx, coin)
        if amount is None:
            continue
        txid = str(tx.get("transactionId") or tx.get("orderId") or tx.get("tranId") or "")
        if not txid:
            continue

        amount_candidates = [
            dep for dep in pending_deposits
            if _decimal(dep.amount) is not None and abs(amount - _decimal(dep.amount)) <= tolerance
        ]
        if not amount_candidates:
            continue

        tx_time_raw = tx.get("transactionTime") or tx.get("createTime") or 0
        try:
            tx_time = datetime.utcfromtimestamp(int(tx_time_raw) / 1000)
        except Exception:
            continue

        dup = db.query(PaymentTransaction).filter(
            PaymentTransaction.provider == "binance_pay",
            PaymentTransaction.external_transaction_id == txid,
        ).first()

        # Same amount can collide across multiple candidates (e.g. a stale
        # expired deposit sharing its amount with a brand-new pending one).
        # Try active deposits before terminal ones (see
        # _rank_deposit_candidates), and within a tier don't let the first
        # candidate's failed time-window check block a later candidate that
        # actually fits — otherwise a valid deposit could be missed
        # entirely just because it happened to sort after a bad candidate.
        matched = None
        grace = timedelta(minutes=10)
        for dep in _rank_deposit_candidates(amount_candidates):
            if dup and (dup.matched_order_id or (dup.matched_deposit_id and dup.matched_deposit_id != dep.id)):
                continue  # this txid already paid an order or a different deposit
            window_end = dep.expires_at or (dep.created_at + timedelta(minutes=expiry_minutes))
            if tx_time < (dep.created_at - timedelta(minutes=2)) or tx_time > (window_end + grace):
                continue
            matched = dep
            break
        if not matched:
            continue

        if not dup:
            ptx = PaymentTransaction(
                provider="binance_pay",
                external_transaction_id=txid,
                gateway="binance_pay",
                transaction_date=tx_time,
                amount_in=float(amount),
                matched_deposit_id=matched.id,
                match_status="deposit_matched",
                raw_json=json.dumps(tx, ensure_ascii=False)[:5000],
            )
            try:
                db.add(ptx)
                db.commit()
            except IntegrityError:
                db.rollback()
                continue

        from models import WalletDepositStatus, WalletCurrency, WalletTxType
        from services import wallet_service
        from services.wallet_service import AlreadyProcessedError

        if matched.status in (WalletDepositStatus.expired, WalletDepositStatus.failed,
                               WalletDepositStatus.cancelled):
            # The deposit's window already closed before this Binance Pay
            # transfer was seen — real customer money that must be escalated
            # to a human, never silently skipped as "already done".
            await _escalate_late_deposit_to_review(db, matched, txid)
            continue

        now_iso = datetime.utcnow().isoformat(sep=" ")
        try:
            wallet_service.credit_wallet(
                db, matched.telegram_user_id, WalletCurrency.USDT, matched.amount,
                WalletTxType.deposit, deposit_id=matched.id,
                note=f"Auto-credited Binance Pay deposit (txid={txid})",
                actor="system",
                extra_updates=[(
                    "UPDATE wallet_deposits SET status='credited', external_transaction_id=?, "
                    "verified_at=?, credited_at=? "
                    "WHERE id=? AND status NOT IN ('credited','failed','expired','cancelled')",
                    (txid, now_iso, now_iso, matched.id),
                )],
            )
        except AlreadyProcessedError:
            continue
        db.refresh(matched)
        logger.info(f"[binance_pay] wallet deposit {matched.reference_code} credited via txid={txid}")
        await _notify_deposit_credited(matched, db)


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

                    pending_deposits = _get_pending_deposits(db, "BINANCE")
                    if pending_deposits:
                        try:
                            await _sweep_binance_deposits(db, cfg, pending_deposits)
                        except Exception as e:
                            logger.error(f"[binance_pay] deposit sweep error: {e}")

                    interval = max(60, int(cfg.get("min_check_interval_seconds") or 60))
                else:
                    interval = 60
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[binance_pay] loop error: {e}")
            interval = 60
        await asyncio.sleep(interval)
