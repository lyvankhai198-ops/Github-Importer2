"""
Public webhook endpoints — no session authentication required.
POST /webhooks/sepay   — receives SePay payment notifications.
POST /webhooks/binance — receives Binance Pay Merchant webhook.
"""
import json
import logging
from fastapi import APIRouter, Request, BackgroundTasks, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


def _verify_sepay_auth(request: Request, db: Session) -> bool:
    from models import SepayConfig
    from crypto import decrypt
    cfg = db.query(SepayConfig).first()
    if not cfg or not cfg.is_enabled:
        return False
    stored = decrypt(cfg.webhook_secret_encrypted) if cfg.webhook_secret_encrypted else ""
    if not stored:
        logger.warning("[webhook/sepay] no Webhook Secret configured — rejecting")
        return False
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Apikey "):
        provided = auth[7:].strip()
    elif auth.startswith("Bearer "):
        provided = auth[7:].strip()
    else:
        provided = auth.strip()
    return bool(provided) and provided == stored


@router.post("/webhooks/sepay")
async def sepay_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    if not _verify_sepay_auth(request, db):
        logger.warning("[webhook/sepay] auth failed")
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)

    try:
        raw = await request.json()
    except Exception:
        return JSONResponse({"success": False, "message": "Invalid JSON"}, status_code=400)

    tx_id = raw.get("id", "unknown")
    logger.info(f"[webhook/sepay] received tx_id={tx_id}")

    from services.payment_service import process_webhook_transaction
    try:
        result = process_webhook_transaction(db, raw)
    except Exception as e:
        logger.error(f"[webhook/sepay] process error: {e}")
        return JSONResponse({"success": True})

    action = result.get("action", "")
    order_id = result.get("order_id")

    if action in ("paid", "overpaid") and order_id:
        from services.payment_service import process_paid_order
        background_tasks.add_task(process_paid_order, order_id)

    if action == "partial" and order_id:
        background_tasks.add_task(
            _bg_notify_partial, order_id,
            result.get("new_paid", 0), result.get("expected", 0),
        )

    if action in ("paid", "overpaid") and order_id:
        background_tasks.add_task(_bg_notify_payment_received, order_id, action)

    if action == "late_payment" and order_id:
        background_tasks.add_task(_bg_notify_late_payment, order_id)

    return JSONResponse({"success": True})


@router.post("/webhooks/binance")
async def binance_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Binance Pay Merchant webhook.
    Verifies HMAC-SHA512 signature before processing.
    Idempotent: calling process_paid_order twice is safe.
    """
    from models import PaymentMethod, Order, OrderStatus, PaymentStatus
    from crypto import decrypt
    from services.binance_service import verify_binance_webhook_signature

    pm = db.query(PaymentMethod).filter(
        PaymentMethod.method_code == "binance_pay",
        PaymentMethod.is_active == True,
    ).first()
    if not pm or not pm.config_encrypted:
        return JSONResponse({"returnCode": "FAIL", "returnMessage": "Not configured"}, status_code=400)

    try:
        cfg = json.loads(decrypt(pm.config_encrypted) or "{}")
    except Exception:
        return JSONResponse({"returnCode": "FAIL"}, status_code=400)

    if cfg.get("mode") != "merchant":
        return JSONResponse({"returnCode": "FAIL", "returnMessage": "Not merchant mode"}, status_code=400)

    secret_key = cfg.get("secret_key") or ""
    if not secret_key:
        return JSONResponse({"returnCode": "FAIL"}, status_code=400)

    # Extract signature headers
    timestamp = request.headers.get("BinancePay-Timestamp", "")
    nonce = request.headers.get("BinancePay-Nonce", "")
    signature = request.headers.get("BinancePay-Signature", "")

    try:
        body_bytes = await request.body()
        body_str = body_bytes.decode("utf-8")
        raw = json.loads(body_str)
    except Exception:
        return JSONResponse({"returnCode": "FAIL"}, status_code=400)

    # SECURITY: verify signature — never trust unsigned payloads
    if not verify_binance_webhook_signature(timestamp, nonce, body_str, signature, secret_key):
        logger.warning("[webhook/binance] signature verification failed")
        return JSONResponse({"returnCode": "FAIL", "returnMessage": "Signature mismatch"}, status_code=401)

    biz_type = raw.get("bizType") or ""
    biz_status = raw.get("bizStatus") or ""
    biz_id_str = raw.get("bizIdStr") or raw.get("merchantTradeNo") or ""

    logger.info(f"[webhook/binance] bizType={biz_type} status={biz_status} tradeNo={biz_id_str}")

    if biz_type == "PAY" and biz_status == "PAY_SUCCESS":
        # Find order by order_code (= merchantTradeNo)
        order = db.query(Order).filter(Order.order_code == biz_id_str).first()
        if order and order.payment_status not in (PaymentStatus.paid, PaymentStatus.overpaid):
            order.payment_status = PaymentStatus.paid
            order.paid_at = __import__("datetime").datetime.utcnow()
            # Extract USDT amount from payload
            biz_data = {}
            try:
                biz_data = json.loads(raw.get("data") or "{}")
            except Exception:
                pass
            paid_usdt = float(biz_data.get("openAmount") or order.expected_crypto_amount or 0)
            order.received_crypto_amount = paid_usdt
            order.paid_amount = paid_usdt * (order.exchange_rate or 1.0)
            db.commit()
            from services.payment_service import process_paid_order
            background_tasks.add_task(process_paid_order, order.id)

    return JSONResponse({"returnCode": "SUCCESS", "returnMessage": "SUCCESS"})


# ── Background notification helpers ───────────────────────────────────────────

async def _bg_notify_partial(order_id: int, paid: float, expected: float):
    from database import SessionLocal
    db = SessionLocal()
    try:
        from models import Order, TelegramBotConfig
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        order = db.query(Order).filter(Order.id == order_id).first()
        cfg = db.query(TelegramBotConfig).first()
        if not order or not cfg:
            return
        bot = bot_manager._application.bot
        from bot.notifier import notify_user_payment_partial, notify_admin_payment_partial
        await notify_user_payment_partial(bot, order.telegram_user_id, order, paid, expected)
        if cfg.admin_telegram_id:
            await notify_admin_payment_partial(bot, order, cfg.admin_telegram_id, paid, expected)
    except Exception as e:
        logger.error(f"[webhook/sepay] _bg_notify_partial error: {e}")
    finally:
        db.close()


async def _bg_notify_payment_received(order_id: int, action: str):
    from database import SessionLocal
    db = SessionLocal()
    try:
        from models import Order, TelegramBotConfig
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        order = db.query(Order).filter(Order.id == order_id).first()
        cfg = db.query(TelegramBotConfig).first()
        if not order or not cfg or not cfg.admin_telegram_id:
            return
        bot = bot_manager._application.bot
        from bot.notifier import notify_admin_payment_received, notify_admin_payment_overpaid
        if action == "overpaid":
            await notify_admin_payment_overpaid(bot, order, cfg.admin_telegram_id)
        else:
            await notify_admin_payment_received(bot, order, cfg.admin_telegram_id)
    except Exception as e:
        logger.error(f"[webhook/sepay] _bg_notify_payment_received error: {e}")
    finally:
        db.close()


async def _bg_notify_late_payment(order_id: int):
    from database import SessionLocal
    db = SessionLocal()
    try:
        from models import Order, TelegramBotConfig
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        order = db.query(Order).filter(Order.id == order_id).first()
        cfg = db.query(TelegramBotConfig).first()
        if not order or not cfg:
            return
        bot = bot_manager._application.bot
        from bot.notifier import notify_user_late_payment, notify_admin_late_payment
        await notify_user_late_payment(bot, order.telegram_user_id, order)
        if cfg.admin_telegram_id:
            await notify_admin_late_payment(bot, order, cfg.admin_telegram_id)
    except Exception as e:
        logger.error(f"[webhook/sepay] _bg_notify_late_payment error: {e}")
    finally:
        db.close()
