import httpx
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from models import TelegramBotConfig, BotStatus
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


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    cfg = get_or_create_bot_config(db)
    bot_status = bot_manager.get_status()
    flash_msg = request.session.pop("flash", None)
    masked_token = mask_key(decrypt(cfg.bot_token_encrypted)) if cfg.bot_token_encrypted else ""
    return templates.TemplateResponse(request, "settings.html", {
        
        "cfg": cfg,
        "bot_status": bot_status,
        "masked_token": masked_token,
        "flash": flash_msg,
        "mask_key": mask_key,
    })


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
    return RedirectResponse(url="/settings", status_code=302)


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
