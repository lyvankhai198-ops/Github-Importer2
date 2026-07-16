import json
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from models import Order, OrderStatus
from services.bot_service import bot_manager
from services.order_service import update_order_delivery

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def check_auth(request: Request):
    return request.session.get("admin_id")


def flash(request: Request, msg: str, type: str = "success"):
    request.session["flash"] = {"type": type, "msg": msg}


@router.get("/orders", response_class=HTMLResponse)
async def orders_list(
    request: Request,
    db: Session = Depends(get_db),
    status: str = "",
    payment_status: str = "",
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    from models import PaymentStatus
    q = db.query(Order)
    if status:
        q = q.filter(Order.status == status)
    if payment_status:
        try:
            q = q.filter(Order.payment_status == PaymentStatus(payment_status))
        except Exception:
            pass
    if search:
        q = q.filter(Order.order_code.ilike(f"%{search}%") | Order.telegram_user_id.ilike(f"%{search}%"))
    if date_from:
        try:
            q = q.filter(Order.created_at >= datetime.strptime(date_from, "%Y-%m-%d"))
        except Exception:
            pass
    if date_to:
        try:
            from datetime import timedelta
            q = q.filter(Order.created_at < datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
        except Exception:
            pass
    total = q.count()
    per_page = 20
    orders = q.order_by(Order.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "orders.html", {
        "orders": orders,
        "status_filter": status,
        "payment_status_filter": payment_status,
        "search": search,
        "date_from": date_from,
        "date_to": date_to,
        "page": page,
        "total": total,
        "per_page": per_page,
        "flash": flash_msg,
        "order_statuses": [e.value for e in OrderStatus],
    })


@router.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_detail(order_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        flash(request, "Đơn hàng không tồn tại!", "error")
        return RedirectResponse(url="/orders", status_code=302)
    flash_msg = request.session.pop("flash", None)
    # Parse normalized delivery items for template
    delivery_items = []
    if order.delivery_items:
        try:
            delivery_items = json.loads(order.delivery_items)
        except Exception:
            pass
    # Format raw JSON nicely for display (without re-encoding)
    raw_json_pretty = ""
    if order.delivery_data:
        try:
            raw_json_pretty = json.dumps(json.loads(order.delivery_data), ensure_ascii=False, indent=2)
        except Exception:
            raw_json_pretty = order.delivery_data
    return templates.TemplateResponse(request, "order_detail.html", {
        "order": order,
        "flash": flash_msg,
        "order_statuses": [e.value for e in OrderStatus],
        "delivery_items": delivery_items,
        "raw_json_pretty": raw_json_pretty,
    })


@router.post("/orders/{order_id}/complete")
async def complete_order(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    delivery_data: str = Form(""),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    order = update_order_delivery(db, order_id, delivery_data, OrderStatus.completed)
    if order and bot_manager.is_running():
        try:
            from bot.notifier import notify_user_delivery
            from models import TelegramBotConfig
            from database import SessionLocal as _SL
            _db = _SL()
            try:
                cfg = _db.query(TelegramBotConfig).first()
                support = cfg.support_username if cfg else ""
            finally:
                _db.close()
            await notify_user_delivery(
                bot_manager._application.bot,
                order.telegram_user_id,
                order,
                support_username=support,
            )
        except Exception:
            pass
    flash(request, "Đơn hàng đã hoàn thành và thông báo đã được gửi!")
    return RedirectResponse(url=f"/orders/{order_id}", status_code=302)


@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    order = db.query(Order).filter(Order.id == order_id).first()
    if order:
        order.status = OrderStatus.cancelled
        order.updated_at = datetime.utcnow()
        db.commit()
        flash(request, "Đơn hàng đã bị hủy!")
    return RedirectResponse(url=f"/orders/{order_id}", status_code=302)


@router.post("/orders/{order_id}/status")
async def change_order_status(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    new_status: str = Form(...),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    order = db.query(Order).filter(Order.id == order_id).first()
    if order:
        try:
            order.status = OrderStatus(new_status)
            order.updated_at = datetime.utcnow()
            db.commit()
            flash(request, f"Trạng thái đã được cập nhật thành: {new_status}")
        except Exception:
            flash(request, "Trạng thái không hợp lệ!", "error")
    return RedirectResponse(url=f"/orders/{order_id}", status_code=302)


@router.post("/orders/{order_id}/notify")
async def notify_user(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    message: str = Form(...),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    order = db.query(Order).filter(Order.id == order_id).first()
    if order and bot_manager.is_running():
        success = await bot_manager.send_message(order.telegram_user_id, message)
        if success:
            flash(request, "Tin nhắn đã được gửi!")
        else:
            flash(request, "Không thể gửi tin nhắn!", "error")
    else:
        flash(request, "Bot chưa khởi động hoặc đơn hàng không tồn tại!", "error")
    return RedirectResponse(url=f"/orders/{order_id}", status_code=302)
