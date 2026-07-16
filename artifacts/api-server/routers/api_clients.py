from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import ApiClient, ApiRequestLog, User, WalletCurrency, WalletTxType
from services import wallet_service
from services import api_client_service

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def check_auth(request: Request):
    return request.session.get("admin_id")


def flash(request: Request, msg: str, type: str = "success"):
    request.session["flash"] = {"type": type, "msg": msg}


@router.get("/api-clients", response_class=HTMLResponse)
async def list_api_clients(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    clients = db.query(ApiClient).order_by(ApiClient.created_at.desc()).all()
    user_map = {}
    for c in clients:
        if c.telegram_user_id not in user_map:
            user_map[c.telegram_user_id] = db.query(User).filter(User.telegram_id == c.telegram_user_id).first()
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "api_clients.html", {
        "clients": clients,
        "user_map": user_map,
        "flash": flash_msg,
    })


@router.get("/api-clients/{client_id}", response_class=HTMLResponse)
async def view_api_client(client_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    client = db.query(ApiClient).filter(ApiClient.id == client_id).first()
    if not client:
        flash(request, "Không tìm thấy client!", "error")
        return RedirectResponse(url="/api-clients", status_code=302)
    user = db.query(User).filter(User.telegram_id == client.telegram_user_id).first()
    logs = (
        db.query(ApiRequestLog)
        .filter(ApiRequestLog.api_client_id == client.id)
        .order_by(ApiRequestLog.created_at.desc())
        .limit(100)
        .all()
    )
    from models import Order
    orders = (
        db.query(Order)
        .filter(Order.api_client_id == client.id)
        .order_by(Order.created_at.desc())
        .limit(50)
        .all()
    )
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "api_client_detail.html", {
        "client": client,
        "user": user,
        "logs": logs,
        "orders": orders,
        "flash": flash_msg,
    })


@router.post("/api-clients/{client_id}/lock")
async def lock_client(client_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    client = db.query(ApiClient).filter(ApiClient.id == client_id).first()
    if not client:
        flash(request, "Không tìm thấy client!", "error")
        return RedirectResponse(url="/api-clients", status_code=302)
    api_client_service.set_lock(db, client, locked=True)
    await _notify_lockout(db, client)
    flash(request, "Đã khóa API client.")
    return RedirectResponse(url=f"/api-clients/{client_id}", status_code=302)


@router.post("/api-clients/{client_id}/unlock")
async def unlock_client(client_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    client = db.query(ApiClient).filter(ApiClient.id == client_id).first()
    if not client:
        flash(request, "Không tìm thấy client!", "error")
        return RedirectResponse(url="/api-clients", status_code=302)
    api_client_service.set_lock(db, client, locked=False)
    flash(request, "Đã mở khóa API client.")
    return RedirectResponse(url=f"/api-clients/{client_id}", status_code=302)


@router.post("/api-clients/{client_id}/reset-key")
async def reset_key(client_id: int, request: Request, db: Session = Depends(get_db)):
    """Admin-triggered key reset. The new raw key is shown once, then the
    customer must be told to fetch it from the bot's "🔗 API" menu on their
    side — we don't have a channel to hand it to them directly from here."""
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    client = db.query(ApiClient).filter(ApiClient.id == client_id).first()
    if not client:
        flash(request, "Không tìm thấy client!", "error")
        return RedirectResponse(url="/api-clients", status_code=302)
    api_client_service.regenerate_key(db, client)
    flash(request, "Đã cấp lại key mới. Khách hàng cần vào menu 🔗 API trong Bot để lấy key mới.")
    return RedirectResponse(url=f"/api-clients/{client_id}", status_code=302)


@router.post("/api-clients/{client_id}/adjust-wallet")
async def adjust_wallet(client_id: int, request: Request, db: Session = Depends(get_db),
                         currency: str = Form(...), amount: float = Form(...), note: str = Form("")):
    """Reuses the same atomic credit/debit primitives as the admin
    users-wallet-adjust flow — no separate money-moving code path."""
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    client = db.query(ApiClient).filter(ApiClient.id == client_id).first()
    if not client:
        flash(request, "Không tìm thấy client!", "error")
        return RedirectResponse(url="/api-clients", status_code=302)

    admin_id = request.session.get("admin_id", "admin")
    cur = WalletCurrency(currency.upper())
    try:
        if amount > 0:
            wallet_service.credit_wallet(
                db, client.telegram_user_id, cur, amount, WalletTxType.admin_credit,
                note=note or f"Admin adjustment via API client #{client.id}", actor=str(admin_id),
            )
        elif amount < 0:
            wallet_service.debit_wallet(
                db, client.telegram_user_id, cur, abs(amount), WalletTxType.admin_debit,
                note=note or f"Admin adjustment via API client #{client.id}", actor=str(admin_id),
            )
        flash(request, "Đã điều chỉnh số dư ví.")
    except wallet_service.InsufficientBalanceError:
        flash(request, "Số dư không đủ để trừ số tiền này!", "error")
    except Exception as e:
        flash(request, f"Lỗi: {e}", "error")
    return RedirectResponse(url=f"/api-clients/{client_id}", status_code=302)


async def _notify_lockout(db: Session, client: ApiClient):
    try:
        from services.bot_service import bot_manager
        if not bot_manager.is_running():
            return
        from models import TelegramBotConfig
        cfg = db.query(TelegramBotConfig).first()
        admin_id = cfg.admin_telegram_id if cfg else ""
        if not admin_id:
            return
        from bot.notifier import notify_admin_api_client_lockout
        await notify_admin_api_client_lockout(bot_manager._application.bot, client, admin_id)
    except Exception:
        pass
