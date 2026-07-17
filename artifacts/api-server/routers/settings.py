import json
import httpx
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
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
    active_tab = request.query_params.get("tab", "config")
    from services.market_pricing import get_market_pricing_config
    market_pricing = get_market_pricing_config(db)
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
        "market_pricing": market_pricing,
    })


# ── Market ("chợ") default markup & platform fee ──────────────────────────────

@router.post("/settings/market-pricing")
async def save_market_pricing(
    request: Request,
    db: Session = Depends(get_db),
    default_markup_percent: float = Form(10.0),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    if not request.state.is_owner:
        flash(request, "Chỉ owner mới có thể chỉnh cấu hình Chợ", "error")
        return RedirectResponse(url="/settings?tab=config", status_code=302)
    from services.market_pricing import save_market_pricing_config
    save_market_pricing_config(db, default_markup_percent)
    flash(request, "Cài đặt markup Chợ đã được lưu!")
    return RedirectResponse(url="/settings?tab=config", status_code=302)


# ── Bot settings ──────────────────────────────────────────────────────────────

@router.post("/settings/bot")
async def save_bot_settings(
    request: Request,
    db: Session = Depends(get_db),
    bot_token: str = Form(""),
    admin_telegram_id: str = Form(""),
    shop_name: str = Form(""),
    welcome_message: str = Form(""),
    support_username: str = Form(""),
    show_out_of_stock: str = Form(None),
    allow_manual_order_when_out_of_stock: str = Form(None),
    notify_users_when_restocked: str = Form(None),
    allow_partial_delivery: str = Form(None),
    notify_new_products: str = Form(None),
    notify_restock: str = Form(None),
    notify_admin_on_price_change: str = Form(None),
    broadcast_batch_size: int = Form(25),
    broadcast_delay_ms: int = Form(300),
    products_per_page: int = Form(15),
    default_product_icon: str = Form("📦"),
    default_language: str = Form("vi"),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    cfg = get_or_create_bot_config(db)
    if bot_token and bot_token.strip():
        cfg.bot_token_encrypted = encrypt(bot_token.strip())
    if admin_telegram_id:
        cfg.admin_telegram_id = admin_telegram_id.strip()
    if shop_name is not None:
        cfg.shop_name = shop_name.strip()
    if welcome_message:
        cfg.welcome_message = welcome_message
    if support_username is not None:
        cfg.support_username = support_username.strip().lstrip("@")
    # Checkboxes: present = True, absent = False
    cfg.show_out_of_stock = show_out_of_stock is not None
    cfg.allow_manual_order_when_out_of_stock = allow_manual_order_when_out_of_stock is not None
    cfg.notify_users_when_restocked = notify_users_when_restocked is not None
    cfg.allow_partial_delivery = allow_partial_delivery is not None
    cfg.notify_new_products = notify_new_products is not None
    cfg.notify_restock = notify_restock is not None
    # Customers are NEVER notified about price changes — that toggle/logic
    # has been permanently removed. Admin-only price alert toggle below.
    cfg.notify_admin_on_price_change = notify_admin_on_price_change is not None
    cfg.broadcast_batch_size = max(1, min(100, broadcast_batch_size or 25))
    cfg.broadcast_delay_ms = max(0, min(10000, broadcast_delay_ms if broadcast_delay_ms is not None else 300))
    cfg.products_per_page = max(5, min(50, products_per_page or 15))
    cfg.default_product_icon = (default_product_icon or "📦").strip() or "📦"
    if default_language in ("vi", "en"):
        cfg.default_language = default_language
    cfg.updated_at = datetime.utcnow()
    db.commit()
    flash(request, "Bot settings saved!")
    return RedirectResponse(url="/settings?tab=config", status_code=302)


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
    # Persist intent: an admin-requested start means the bot should also come
    # back up automatically the next time the web app boots.
    cfg.is_enabled = True
    db.commit()
    import asyncio
    asyncio.create_task(bot_manager.start_bot(token))
    return JSONResponse({"success": True, "message": "Bot đang khởi động..."})


@router.post("/settings/bot/stop")
async def stop_bot(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    cfg = get_or_create_bot_config(db)
    cfg.is_enabled = False
    db.commit()
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
    cfg.is_enabled = True
    db.commit()
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
    flash(request, "Store settings saved!")
    return RedirectResponse(url="/settings", status_code=302)


@router.post("/settings/order")
async def save_order_settings(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    flash(request, "Order settings saved!")
    return RedirectResponse(url="/settings", status_code=302)


@router.post("/settings/api")
async def save_api_settings(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    flash(request, "API settings saved!")
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
        return RedirectResponse(url="/settings?tab=config", status_code=302)

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
    flash(request, "SePay settings saved!")
    return RedirectResponse(url="/settings?tab=config", status_code=302)


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
        return JSONResponse({"success": False, "message": "Enter payment code"})

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


# ── Payment methods (Binance Pay / BEP20 / TRC20) ────────────────────────────

def _get_pm(db: Session, code: str):
    from models import PaymentMethod
    pm = db.query(PaymentMethod).filter(PaymentMethod.method_code == code).first()
    if not pm:
        from models import PaymentMethod as PM
        pm = PM(method_code=code, display_name_vi=code, display_name_en=code, is_active=False)
        db.add(pm)
        db.commit()
        db.refresh(pm)
    return pm


@router.get("/settings/payment-method/{code}/config")
async def get_pm_config(request: Request, code: str, db: Session = Depends(get_db)):
    if not check_auth(request):
        return JSONResponse({"success": False}, status_code=401)
    if code not in ("binance_pay", "usdt_bep20", "usdt_trc20", "usdt_erc20"):
        return JSONResponse({"success": False, "message": "Unknown method"}, status_code=400)
    from models import PaymentMethod
    pm = db.query(PaymentMethod).filter(PaymentMethod.method_code == code).first()
    if not pm:
        return JSONResponse({"success": True, "is_active": False, "config": {}})
    cfg_raw = {}
    if pm.config_encrypted:
        try:
            cfg_raw = json.loads(decrypt(pm.config_encrypted) or "{}")
        except Exception:
            cfg_raw = {}
    # Mask secrets before returning
    masked_cfg = {}
    for k, v in cfg_raw.items():
        if any(x in k.lower() for x in ("key", "secret", "token", "password")):
            masked_cfg[k] = mask_key(v) if v else ""
        else:
            masked_cfg[k] = v
    return JSONResponse({"success": True, "is_active": pm.is_active, "config": masked_cfg})


@router.post("/settings/payment-method/binance")
async def save_binance_settings(
    request: Request,
    db: Session = Depends(get_db),
    is_enabled: str = Form("off"),
    receiver_binance_id: str = Form(""),
    default_coin: str = Form("USDT"),
    order_expiry_minutes: int = Form(30),
    min_check_interval_seconds: int = Form(15),
    amount_tolerance: float = Form(0.0),
    api_key: str = Form(""),
    secret_key: str = Form(""),
    qr_image: UploadFile = File(None),
    remove_qr_image: str = Form("off"),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)

    pm = _get_pm(db, "binance_pay")
    pm.is_active = (is_enabled == "on")

    try:
        existing_cfg = json.loads(decrypt(pm.config_encrypted) or "{}") if pm.config_encrypted else {}
    except Exception:
        existing_cfg = {}

    new_cfg = {
        "receiver_binance_id": receiver_binance_id.strip(),
        "default_coin": (default_coin.strip() or "USDT").upper(),
        "order_expiry_minutes": max(5, order_expiry_minutes),
        "min_check_interval_seconds": max(5, min_check_interval_seconds),
        "amount_tolerance": max(0.0, amount_tolerance),
        "qr_image_path": existing_cfg.get("qr_image_path", ""),
    }
    # Only update key/secret if the admin submitted non-masked values —
    # never overwrite a saved secret with the masked placeholder we returned.
    if api_key.strip() and not api_key.strip().startswith("*"):
        new_cfg["api_key"] = api_key.strip()
    else:
        new_cfg["api_key"] = existing_cfg.get("api_key", "")
    if secret_key.strip() and not secret_key.strip().startswith("*"):
        new_cfg["secret_key"] = secret_key.strip()
    else:
        new_cfg["secret_key"] = existing_cfg.get("secret_key", "")

    if remove_qr_image == "on":
        new_cfg["qr_image_path"] = ""
    elif qr_image and qr_image.filename:
        from routers.products import _save_image
        saved = await _save_image(qr_image)
        if saved:
            new_cfg["qr_image_path"] = saved

    pm.config_encrypted = encrypt(json.dumps(new_cfg, ensure_ascii=False))
    pm.updated_at = datetime.utcnow()
    db.commit()
    flash(request, "Binance Pay settings saved!")
    return RedirectResponse(url="/settings?tab=config", status_code=302)


@router.post("/settings/payment-method/binance/test-connection")
async def test_binance_connection_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    api_key: str = Form(""),
    secret_key: str = Form(""),
):
    """
    Test the given (or currently saved, if the field is left as the masked
    placeholder) API Key/Secret against Binance Pay History, without
    exposing any transaction contents in the response.
    """
    if not check_auth(request):
        return JSONResponse({"success": False}, status_code=401)

    pm = _get_pm(db, "binance_pay")
    try:
        existing_cfg = json.loads(decrypt(pm.config_encrypted) or "{}") if pm.config_encrypted else {}
    except Exception:
        existing_cfg = {}

    real_api_key = api_key.strip() if api_key.strip() and not api_key.strip().startswith("*") else existing_cfg.get("api_key", "")
    real_secret_key = secret_key.strip() if secret_key.strip() and not secret_key.strip().startswith("*") else existing_cfg.get("secret_key", "")

    from services.binance_service import test_binance_connection
    result = await test_binance_connection(real_api_key, real_secret_key)
    return JSONResponse(result)


@router.post("/settings/payment-method/bep20")
async def save_bep20_settings(
    request: Request,
    db: Session = Depends(get_db),
    is_enabled: str = Form("off"),
    wallet_address: str = Form(""),
    usdt_contract: str = Form(""),
    bscscan_api_key: str = Form(""),
    bsc_rpc_url: str = Form(""),
    required_confirmations: int = Form(12),
    poll_interval_seconds: int = Form(30),
    timeout_minutes: int = Form(60),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    pm = _get_pm(db, "usdt_bep20")
    pm.is_active = (is_enabled == "on")

    try:
        existing = json.loads(decrypt(pm.config_encrypted) or "{}") if pm.config_encrypted else {}
    except Exception:
        existing = {}

    if not usdt_contract.strip():
        usdt_contract = "0x55d398326f99059fF775485246999027B3197955"  # BSC USDT

    new_cfg = {
        "wallet_address": wallet_address.strip(),
        "usdt_contract": usdt_contract.strip().lower(),
        "bsc_rpc_url": bsc_rpc_url.strip() or "https://bsc-dataseed.binance.org/",
        "required_confirmations": max(1, required_confirmations),
        "poll_interval_seconds": max(15, poll_interval_seconds),
        "timeout_minutes": max(15, timeout_minutes),
    }
    if bscscan_api_key.strip() and not bscscan_api_key.strip().startswith("*"):
        new_cfg["bscscan_api_key"] = bscscan_api_key.strip()
    else:
        new_cfg["bscscan_api_key"] = existing.get("bscscan_api_key", "")

    pm.config_encrypted = encrypt(json.dumps(new_cfg, ensure_ascii=False))
    pm.updated_at = datetime.utcnow()
    db.commit()
    flash(request, "USDT BEP20 settings saved!")
    return RedirectResponse(url="/settings?tab=config", status_code=302)


@router.post("/settings/payment-method/trc20")
async def save_trc20_settings(
    request: Request,
    db: Session = Depends(get_db),
    is_enabled: str = Form("off"),
    wallet_address: str = Form(""),
    usdt_contract: str = Form(""),
    trongrid_api_key: str = Form(""),
    required_confirmations: int = Form(20),
    poll_interval_seconds: int = Form(30),
    timeout_minutes: int = Form(60),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    pm = _get_pm(db, "usdt_trc20")
    pm.is_active = (is_enabled == "on")

    try:
        existing = json.loads(decrypt(pm.config_encrypted) or "{}") if pm.config_encrypted else {}
    except Exception:
        existing = {}

    if not usdt_contract.strip():
        usdt_contract = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # TRON USDT

    new_cfg = {
        "wallet_address": wallet_address.strip(),
        "usdt_contract": usdt_contract.strip(),
        "required_confirmations": max(1, required_confirmations),
        "poll_interval_seconds": max(15, poll_interval_seconds),
        "timeout_minutes": max(15, timeout_minutes),
    }
    if trongrid_api_key.strip() and not trongrid_api_key.strip().startswith("*"):
        new_cfg["trongrid_api_key"] = trongrid_api_key.strip()
    else:
        new_cfg["trongrid_api_key"] = existing.get("trongrid_api_key", "")

    pm.config_encrypted = encrypt(json.dumps(new_cfg, ensure_ascii=False))
    pm.updated_at = datetime.utcnow()
    db.commit()
    flash(request, "USDT TRC20 settings saved!")
    return RedirectResponse(url="/settings?tab=config", status_code=302)


@router.post("/settings/payment-method/erc20")
async def save_erc20_settings(
    request: Request,
    db: Session = Depends(get_db),
    is_enabled: str = Form("off"),
    wallet_address: str = Form(""),
    usdt_contract: str = Form(""),
    etherscan_api_key: str = Form(""),
    required_confirmations: int = Form(12),
    poll_interval_seconds: int = Form(30),
    timeout_minutes: int = Form(60),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    pm = _get_pm(db, "usdt_erc20")
    pm.is_active = (is_enabled == "on")

    try:
        existing = json.loads(decrypt(pm.config_encrypted) or "{}") if pm.config_encrypted else {}
    except Exception:
        existing = {}

    if not usdt_contract.strip():
        usdt_contract = "0xdAC17F958D2ee523a2206206994597C13D831ec7"  # Ethereum USDT

    new_cfg = {
        "wallet_address": wallet_address.strip(),
        "usdt_contract": usdt_contract.strip().lower(),
        "required_confirmations": max(1, required_confirmations),
        "poll_interval_seconds": max(15, poll_interval_seconds),
        "timeout_minutes": max(15, timeout_minutes),
    }
    if etherscan_api_key.strip() and not etherscan_api_key.strip().startswith("*"):
        new_cfg["etherscan_api_key"] = etherscan_api_key.strip()
    else:
        new_cfg["etherscan_api_key"] = existing.get("etherscan_api_key", "")

    pm.config_encrypted = encrypt(json.dumps(new_cfg, ensure_ascii=False))
    pm.updated_at = datetime.utcnow()
    db.commit()
    flash(request, "USDT ERC20 settings saved!")
    return RedirectResponse(url="/settings?tab=config", status_code=302)


# ── Exchange rate config ──────────────────────────────────────────────────────

@router.post("/settings/exchange-rate")
async def save_exchange_rate(
    request: Request,
    db: Session = Depends(get_db),
    rate_mode: str = Form("fixed"),
    fixed_rate: float = Form(25500.0),
    markup_percent: float = Form(2.0),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    from services.exchange_rate_service import get_exchange_config
    from services.normalize import compute_price_usdt
    from models import Setting, Product
    cfg = {
        "mode": rate_mode,
        "fixed_rate": fixed_rate,
        "markup_percent": max(0.0, markup_percent),
    }
    s = db.query(Setting).filter(Setting.key == "exchange_rate_config").first()
    if not s:
        s = Setting(key="exchange_rate_config")
        db.add(s)
    s.value = json.dumps(cfg)

    # The retail "1 USDT = N VND" rate feeds every product's auto-computed
    # price_usdt (shown to English-language shoppers). Whenever the fixed rate
    # changes, recompute price_usdt for every existing product so displayed
    # USDT prices never drift from the current rate.
    updated = 0
    for product in db.query(Product).all():
        product.price_usdt = compute_price_usdt(product.sale_price, fixed_rate)
        updated += 1

    db.commit()
    flash(request, f"Exchange rate settings saved! Updated USDT price for {updated} product(s).")
    return RedirectResponse(url="/settings?tab=config", status_code=302)


@router.get("/api/exchange-rate")
async def get_exchange_rate_api(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return JSONResponse({"success": False}, status_code=401)
    from services.exchange_rate_service import get_vnd_usdt_rate, get_exchange_config
    cfg = get_exchange_config(db)
    try:
        rate = await get_vnd_usdt_rate(db)
        return JSONResponse({"success": True, "rate": rate, "config": cfg})
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)})


# ── Page update: pass payment method context ─────────────────────────────────
# Override GET /settings to inject payment method configs

_original_settings_page = settings_page.__wrapped__ if hasattr(settings_page, "__wrapped__") else None


@router.get("/settings/payment-methods/status")
async def get_payment_methods_status(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return JSONResponse({"success": False}, status_code=401)
    from models import PaymentMethod
    pms = db.query(PaymentMethod).all()
    result = []
    for pm in pms:
        cfg_raw = {}
        if pm.config_encrypted:
            try:
                cfg_raw = json.loads(decrypt(pm.config_encrypted) or "{}")
            except Exception:
                pass
        result.append({
            "code": pm.method_code,
            "name_vi": pm.display_name_vi,
            "is_active": pm.is_active,
            "mode": cfg_raw.get("mode"),
            "wallet": cfg_raw.get("wallet_address", "")[:8] + "..." if cfg_raw.get("wallet_address") else "",
        })
    from services.exchange_rate_service import get_exchange_config
    ex_cfg = get_exchange_config(db)
    return JSONResponse({"methods": result, "exchange_rate_config": ex_cfg})
