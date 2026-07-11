"""
Public webhook endpoints — no session authentication required.
POST /webhooks/sepay  — receives SePay payment notifications.
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
    """
    Validate SePay Authorization header: `Apikey <token>`.
    Compares in-memory only — token value is NEVER logged.

    FAIL-CLOSED security contract:
      - SePay disabled          → False (do not process)
      - SePay enabled, no token → False (misconfigured; reject to prevent fraud)
      - SePay enabled, token present, header matches → True
      - SePay enabled, token present, header wrong/missing → False
    """
    from models import SepayConfig
    from crypto import decrypt
    cfg = db.query(SepayConfig).first()
    if not cfg or not cfg.is_enabled:
        return False
    stored = decrypt(cfg.api_token_encrypted) if cfg.api_token_encrypted else ""
    # FAIL-CLOSED: if no token configured, reject all requests
    if not stored:
        logger.warning(
            "[webhook/sepay] SePay is enabled but no API token is configured — "
            "rejecting all requests. Configure an API token in Settings → SePay."
        )
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
    """
    Receives SePay payment notification webhook.

    Flow:
      1. Authenticate via Authorization header (Apikey).
      2. Parse JSON body.
      3. Save + deduplicate transaction.
      4. Return HTTP 200 immediately.
      5. Background: match order, update payment_status, trigger fulfillment.
    """
    if not _verify_sepay_auth(request, db):
        logger.warning("[webhook/sepay] auth failed — check API token in settings")
        # Return 200 to prevent SePay from retrying with a bad secret
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
        return JSONResponse({"success": True})  # always 200 to SePay

    action = result.get("action", "")
    order_id = result.get("order_id")

    # Trigger fulfillment in background when payment complete
    if action in ("paid", "overpaid") and order_id:
        from services.payment_service import process_paid_order
        background_tasks.add_task(process_paid_order, order_id)

    # Notify on partial payment
    if action == "partial" and order_id:
        background_tasks.add_task(
            _bg_notify_partial, order_id,
            result.get("new_paid", 0), result.get("expected", 0),
        )

    # Notify admin of confirmed payment
    if action in ("paid", "overpaid") and order_id:
        background_tasks.add_task(_bg_notify_payment_received, order_id, action)

    # Notify on late payment
    if action == "late_payment" and order_id:
        background_tasks.add_task(_bg_notify_late_payment, order_id)

    return JSONResponse({"success": True})


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
