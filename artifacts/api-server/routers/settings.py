import json
import httpx
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from models import TelegramBotConfig, BotStatus, SepayConfig, PaymentTransaction
from crypto import encrypt, decrypt, mask_key
from services.bot_service import bot_manager

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def check_auth(request: Request):
    return request.session.get("admin_id")


def flash(request: Request, msg: str, type: str = "success"):
    request.session["flash"] = {"type": type, "msg": msg}


def get_or_create_bot_config(db: Session) -> TelegramBotConfig:
    cfg = db.query(TelegramBotConfig).first()
    if not cfg:
        cfg = TelegramBotConfig()
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def get_or_create_sepay_config(db: Session) -> SepayConfig:
    cfg = db.query(SepayConfig).first()
    if not cfg:
        cfg = SepayConfig()
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


# ── Main settings page ────────────────────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    cfg = get_or_create_bot_config(db)
    sepay = get_or_create_sepay_config(db)
    bot_status = bot_manager.get_status()
    flash_msg = request.session.pop("flash", None)
    masked_token = mask_key(decrypt(cfg.bot_token_encrypted)) if cfg.bot_token_encrypted else ""
    masked_sepay_token = mask_key(decrypt(sepay.api_token_encrypted)) if sepay.api_token_encrypted else ""
    masked_webhook_secret = mask_key(decrypt(sepay.webhook_secret_encrypted)) if sepay.webhook_secret_encrypted else ""
    base_url = str(request.base_url).rstrip("/")
    webhook_url = f"{base_url}/webhooks/sepay"
    active_tab = request.query_params.get("tab", "shop")
    return templates.TemplateResponse(request, "settings.html", {
        "cfg": cfg,
        "sepay": sepay,
        "bot_status": bot_status,
        "masked_token": masked_token,
        "masked_sepay_token": masked_sepay_token,
        "masked_webhook_secret": masked_webhook_secret,
        "webhook_url": webhook_url,
        "flash": flash_msg,
        "mask_key": mask_key,
        "active_tab": active_tab,
    })


# ── Bot settings ──────────────────────────────────────────────────────────────

@router.post("/settings/bot")
async def save_bot_settings(
    request: Request,
    db: Session = Depends(get_db),
    bot_token: str = Form(""),
    admin_telegram_id: str = Form(""),
    welcome_message: str = Form(""),
    support_username: str = Form(""),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    cfg = get_or_create_bot_config(db)
    if bot_token and bot_token.strip():
        cfg.bot_token_encrypted = encrypt(bot_token.strip())
    if admin_telegram_id:
        cfg.admin_telegram_id = admin_telegram_id.strip()
    if welcome_message:
        cfg.welcome_message = welcome_message
    if support_username:
        cfg.support_username = support_username.strip().lstrip("@")
    cfg.updated_at = datetime.utcnow()
    db.commit()
    flash(request, "Cài đặt bot đã được lưu!")
    return RedirectResponse(url="/settings?tab=bot", status_code=302)


@router.post("/settings/bot/verify")
async def verify_bot_token(request: Request, db: Session = Depends(get_db), token: str = Form("")):
    if not check_auth(request):
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    if not token:
        cfg = get_or_create_bot_config(db)
        token = decrypt(cfg.bot_token_encrypted) if cfg.bot_token_encrypted else ""
    if not token:
        return JSONResponse({"success": False, "message": "Không có token nào được cấu hình"})
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    bot_info = data["result"]
                    cfg = get_or_create_bot_config(db)
                    cfg.bot_name = bot_info.get("first_name", "")
                    cfg.bot_username = bot_info.get("username", "")
                    db.commit()
                    return JSONResponse({
                        "success": True,
                        "bot_name": bot_info.get("first_name"),
                        "bot_username": bot_info.get("username"),
                        "bot_id": bot_info.get("id"),
                    })
            return JSONResponse({"success": False, "message": f"Telegram API lỗi: {r.text}"})
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)})


@router.post("/settings/bot/start")
async def start_bot(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    if bot_manager.is_running():
        return JSONResponse({"success": False, "message": "Bot đang chạy rồi"})
    cfg = get_or_create_bot_config(db)
    token = decrypt(cfg.bot_token_encrypted) if cfg.bot_token_encrypted else ""
    if not token:
        return JSONResponse({"success": False, "message": "Chưa cấu hình token bot"})
    import asyncio
    asyncio.create_task(bot_manager.start_bot(token))
    return JSONResponse({"success": True, "message": "Bot đang khởi động..."})


@router.post("/settings/bot/stop")
async def stop_bot(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    await bot_manager.stop_bot()
    return JSONResponse({"success": True, "message": "Bot đã dừng"})


@router.post("/settings/bot/restart")
async def restart_bot(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    cfg = get_or_create_bot_config(db)
    token = decrypt(cfg.bot_token_encrypted) if cfg.bot_token_encrypted else ""
    if not token:
        return JSONResponse({"success": False, "message": "Chưa cấu hình token bot"})
    import asyncio
    asyncio.create_task(bot_manager.restart_bot(token))
    return JSONResponse({"success": True, "message": "Bot đang khởi động lại..."})


@router.post("/settings/bot/test-message")
async def test_message(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    cfg = get_or_create_bot_config(db)
    if not cfg.admin_telegram_id:
        return JSONResponse({"success": False, "message": "Chưa cấu hình Admin Telegram ID"})
    if not bot_manager.is_running():
        return JSONResponse({"success": False, "message": "Bot chưa khởi động"})
    success = await bot_manager.send_message(cfg.admin_telegram_id, "✅ Test message từ AI Center Web Bot Manager!")
    return JSONResponse({"success": success, "message": "Đã gửi!" if success else "Không thể gửi tin nhắn"})


@router.get("/api/bot-status")
async def bot_status_api(request: Request, db: Session = Depends(get_db)):
    status = bot_manager.get_status()
    cfg = db.query(TelegramBotConfig).first()
    if cfg:
        status["status"] = cfg.bot_status.value if hasattr(cfg.bot_status, "value") else str(cfg.bot_status)
        status["bot_name"] = cfg.bot_name or status.get("bot_name", "")
        status["bot_username"] = cfg.bot_username or status.get("bot_username", "")
    return JSONResponse(status)


@router.post("/settings/store")
async def save_store_settings(
    request: Request,
    db: Session = Depends(get_db),
    shop_name: str = Form(""),
    support_username: str = Form(""),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    cfg = get_or_create_bot_config(db)
    if support_username:
        cfg.support_username = support_username.strip().lstrip("@")
        db.commit()
    flash(request, "Cài đặt cửa hàng đã được lưu!")
    return RedirectResponse(url="/settings", status_code=302)


@router.post("/settings/order")
async def save_order_settings(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    flash(request, "Cài đặt đơn hàng đã được lưu!")
    return RedirectResponse(url="/settings", status_code=302)


@router.post("/settings/api")
async def save_api_settings(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    flash(request, "Cài đặt API đã được lưu!")
    return RedirectResponse(url="/settings", status_code=302)


# ── SePay settings ────────────────────────────────────────────────────────────

@router.post("/settings/sepay")
async def save_sepay_settings(
    request: Request,
    db: Session = Depends(get_db),
    is_enabled: str = Form("off"),
    bank_name: str = Form(""),
    account_number: str = Form(""),
    account_name: str = Form(""),
    bank_bin: str = Form(""),
    api_token: str = Form(""),
    webhook_secret: str = Form(""),
    payment_prefix: str = Form("AIC"),
    payment_timeout_minutes: int = Form(15),
    allow_overpay: str = Form("off"),
    auto_refund_partial: str = Form("off"),
    test_mode: str = Form("off"),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)

    sepay = get_or_create_sepay_config(db)
    want_enabled = (is_enabled == "on")

    # Determine effective webhook_secret after this save (new input or existing stored)
    new_token = api_token.strip() if api_token.strip() and not api_token.strip().startswith("*") else ""
    new_secret = webhook_secret.strip() if webhook_secret.strip() and not webhook_secret.strip().startswith("*") else ""
    existing_secret = decrypt(sepay.webhook_secret_encrypted) if sepay.webhook_secret_encrypted else ""
    effective_secret = new_secret or existing_secret

    # SECURITY: refuse to enable SePay without a Webhook Secret.
    # SePay auth header is "Apikey {WEBHOOK_SECRET}" — no secret = fail-closed rejection of all webhooks.
    if want_enabled and not effective_secret:
        flash(
            request,
            "⚠️ Không thể bật SePay: chưa nhập Webhook Secret. "
            "Webhook endpoint dùng Webhook Secret để xác thực — "
            "nhập Webhook Secret trước khi bật.",
            "error",
        )
        return RedirectResponse(url="/settings?tab=sepay", status_code=302)

    sepay.is_enabled = want_enabled
    if bank_name.strip():
        sepay.bank_name = bank_name.strip()
    if account_number.strip():
        sepay.account_number = account_number.strip()
    if account_name.strip():
        sepay.account_name = account_name.strip()
    if bank_bin.strip():
        sepay.bank_bin = bank_bin.strip()
    # Only update token/secret if user entered a new value (not the masked placeholder)
    if new_token:
        sepay.api_token_encrypted = encrypt(new_token)
    if webhook_secret.strip() and not webhook_secret.strip().startswith("*"):
        sepay.webhook_secret_encrypted = encrypt(webhook_secret.strip())
    sepay.payment_prefix = (payment_prefix or "AIC").strip().upper()[:10]
    sepay.payment_timeout_minutes = max(1, payment_timeout_minutes)
    sepay.allow_overpay = (allow_overpay == "on")
    sepay.auto_refund_partial = (auto_refund_partial == "on")
    sepay.test_mode = (test_mode == "on")
    sepay.updated_at = datetime.utcnow()
    db.commit()
    flash(request, "Cài đặt SePay đã được lưu!")
    return RedirectResponse(url="/settings?tab=sepay", status_code=302)


@router.get("/settings/sepay/logs")
async def sepay_webhook_logs(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return JSONResponse({"success": False}, status_code=401)
    txs = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.provider == "sepay")
        .order_by(PaymentTransaction.created_at.desc())
        .limit(50)
        .all()
    )
    return JSONResponse([{
        "id": t.id,
        "tx_id": t.external_transaction_id,
        "amount_in": t.amount_in,
        "content": (t.transfer_content or "")[:80],
        "match_status": t.match_status,
        "order_id": t.matched_order_id,
        "created_at": t.created_at.strftime("%d/%m/%Y %H:%M:%S") if t.created_at else "",
    } for t in txs])


@router.post("/settings/sepay/test-webhook")
async def test_sepay_webhook(
    request: Request,
    db: Session = Depends(get_db),
    amount: float = Form(0),
    payment_code: str = Form(""),
):
    """Test mode only: simulate a SePay webhook transaction."""
    if not check_auth(request):
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    sepay = get_or_create_sepay_config(db)
    if not sepay.test_mode:
        return JSONResponse({"success": False, "message": "Bật Test Mode trước"})
    if amount <= 0:
        return JSONResponse({"success": False, "message": "Nhập số tiền > 0"})
    if not payment_code.strip():
        return JSONResponse({"success": False, "message": "Nhập mã thanh toán"})

    import uuid
    fake_tx = {
        "id": f"TEST_{uuid.uuid4().hex[:8].upper()}",
        "gateway": "TEST",
        "transactionDate": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "accountNumber": sepay.account_number or "000000000",
        "transferContent": payment_code.strip().upper(),
        "transferAmount": amount,
        "referenceCode": "",
    }
    from services.payment_service import process_webhook_transaction
    result = process_webhook_transaction(db, fake_tx)
    action = result.get("action", "")
    order_id = result.get("order_id")
    if action in ("paid", "overpaid") and order_id:
        import asyncio
        from services.payment_service import process_paid_order
        asyncio.create_task(process_paid_order(order_id))
    return JSONResponse({"success": True, "result": result})


@router.get("/settings/sepay/check-endpoint")
async def check_sepay_endpoint(request: Request):
    if not check_auth(request):
        return JSONResponse({"success": False}, status_code=401)
    return JSONResponse({"success": True, "message": "Webhook endpoint đang hoạt động ✅"})
