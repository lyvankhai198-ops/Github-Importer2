from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from models import User, Order
from services.bot_service import bot_manager

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def check_auth(request: Request):
    return request.session.get("admin_id")


def flash(request: Request, msg: str, type: str = "success"):
    request.session["flash"] = {"type": type, "msg": msg}


@router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, db: Session = Depends(get_db), search: str = "", page: int = 1):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    q = db.query(User)
    if search:
        q = q.filter(
            User.username.ilike(f"%{search}%") |
            User.first_name.ilike(f"%{search}%") |
            User.telegram_id.ilike(f"%{search}%")
        )
    total = q.count()
    per_page = 20
    users = q.order_by(User.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "users.html", {
        
        "users": users,
        "search": search,
        "page": page,
        "total": total,
        "per_page": per_page,
        "flash": flash_msg,
    })


@router.get("/users/{telegram_id}", response_class=HTMLResponse)
async def user_detail(telegram_id: str, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        flash(request, "Người dùng không tồn tại!", "error")
        return RedirectResponse(url="/users", status_code=302)
    orders = db.query(Order).filter(Order.telegram_user_id == telegram_id).order_by(Order.created_at.desc()).all()
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "users.html", {
        
        "detail_user": user,
        "user_orders": orders,
        "users": [],
        "search": "",
        "page": 1,
        "total": 0,
        "per_page": 20,
        "flash": flash_msg,
    })


@router.post("/users/{telegram_id}/ban")
async def ban_user(telegram_id: str, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user:
        user.is_banned = True
        db.commit()
        flash(request, f"Người dùng {telegram_id} đã bị cấm!")
    return RedirectResponse(url=f"/users/{telegram_id}", status_code=302)


@router.post("/users/{telegram_id}/unban")
async def unban_user(telegram_id: str, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user:
        user.is_banned = False
        db.commit()
        flash(request, f"Người dùng {telegram_id} đã được bỏ cấm!")
    return RedirectResponse(url=f"/users/{telegram_id}", status_code=302)


@router.post("/users/{telegram_id}/message")
async def send_message(telegram_id: str, request: Request, db: Session = Depends(get_db), message: str = Form(...)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    if bot_manager.is_running():
        success = await bot_manager.send_message(telegram_id, message)
        if success:
            flash(request, "Tin nhắn đã được gửi!")
        else:
            flash(request, "Không thể gửi tin nhắn!", "error")
    else:
        flash(request, "Bot chưa khởi động!", "error")
    return RedirectResponse(url=f"/users/{telegram_id}", status_code=302)
