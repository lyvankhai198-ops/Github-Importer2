"""
Public webhook endpoints — no session authentication required.
POST /webhooks/sepay — receives SePay payment notifications.

Binance Pay is no longer verified via a Merchant API webhook — see
services.crypto_monitor.verify_binance_payment, which checks the shop's own
Binance API Management Pay History instead.
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
    print("=== SEPAY WEBHOOK RECEIVED ===")
    print(request.headers)
    body_bytes = await request.body()
    print(body_bytes)

    if not _verify_sepay_auth(request, db):
        logger.warning("[webhook/sepay] auth failed")
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)

    try:
        raw = json.loads(body_bytes)
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


# ── Background notification helpers ───────────────────────────────────────────

def _get_order_and_tenant_cfg(db, order_id: int):
    """
    Helper shared by the background notification tasks below.

    Looks up the order cross-tenant (skip_tenant_filter) because SePay
    webhooks arrive with no session cookie → ambient tenant defaults to the
    owner → a non-owner tenant's order would be invisible to a plain query.
    After the order is found, sets the ambient tenant to order.tenant_id so
    that the TelegramBotConfig query and bot_manager proxy both resolve to
    the correct tenant's config and bot.

    Returns (order, cfg, tenant_token) — caller must call
    reset_current_tenant(tenant_token) in its finally block.
    """
    from models import Order, TelegramBotConfig
    from tenancy import set_current_tenant
    order = (
        db.query(Order)
        .execution_options(skip_tenant_filter=True)
        .filter(Order.id == order_id)
        .first()
    )
    if not order:
        return None, None, None
    token = set_current_tenant(order.tenant_id) if order.tenant_id else None
    cfg = db.query(TelegramBotConfig).first()
    return order, cfg, token


async def _bg_notify_partial(order_id: int, paid: float, expected: float):
    from database import SessionLocal
    from tenancy import reset_current_tenant
    db = SessionLocal()
    token = None
    try:
        from services.bot_service import bot_manager
        order, cfg, token = _get_order_and_tenant_cfg(db, order_id)
        if not order or not cfg or not bot_manager.is_running():
            return
        bot = bot_manager._application.bot
        from bot.notifier import notify_user_payment_partial, notify_admin_payment_partial
        await notify_user_payment_partial(bot, order.telegram_user_id, order, paid, expected)
        if cfg.admin_telegram_id:
            await notify_admin_payment_partial(bot, order, cfg.admin_telegram_id, paid, expected)
    except Exception as e:
        logger.error(f"[webhook/sepay] _bg_notify_partial error: {e}")
    finally:
        if token is not None:
            reset_current_tenant(token)
        db.close()


async def _bg_notify_payment_received(order_id: int, action: str):
    from database import SessionLocal
    from tenancy import reset_current_tenant
    db = SessionLocal()
    token = None
    try:
        from services.bot_service import bot_manager
        order, cfg, token = _get_order_and_tenant_cfg(db, order_id)
        if not order or not cfg or not cfg.admin_telegram_id or not bot_manager.is_running():
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
        if token is not None:
            reset_current_tenant(token)
        db.close()


async def _bg_notify_late_payment(order_id: int):
    from database import SessionLocal
    from tenancy import reset_current_tenant
    db = SessionLocal()
    token = None
    try:
        from services.bot_service import bot_manager
        order, cfg, token = _get_order_and_tenant_cfg(db, order_id)
        if not order or not cfg or not bot_manager.is_running():
            return
        bot = bot_manager._application.bot
        from bot.notifier import notify_user_late_payment, notify_admin_late_payment
        await notify_user_late_payment(bot, order.telegram_user_id, order)
        if cfg.admin_telegram_id:
            await notify_admin_late_payment(bot, order, cfg.admin_telegram_id)
    except Exception as e:
        logger.error(f"[webhook/sepay] _bg_notify_late_payment error: {e}")
    finally:
        if token is not None:
            reset_current_tenant(token)
        db.close()
