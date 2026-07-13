import json
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from pathlib import Path
from database import get_db
from models import Product, ApiProduct, ApiConnection, Order, User, TelegramBotConfig, OrderStatus
from services.bot_service import bot_manager
from config import UPLOADS_DIR

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def check_auth(request: Request):
    return request.session.get("admin_id")


def flash(request: Request, msg: str, type: str = "success"):
    request.session["flash"] = {"type": type, "msg": msg}


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)

    total_products = db.query(Product).count()
    total_api_products = db.query(ApiProduct).count()
    active_apis = db.query(ApiConnection).filter(ApiConnection.is_active == True).count()

    today = datetime.utcnow().date()
    today_start = datetime.combine(today, datetime.min.time())
    today_end = today_start + timedelta(days=1)

    orders_today = db.query(Order).filter(Order.created_at >= today_start, Order.created_at < today_end).count()
    orders_total = db.query(Order).count()

    rev_today_row = db.query(func.sum(Order.total_price)).filter(
        Order.created_at >= today_start,
        Order.created_at < today_end,
        Order.status == OrderStatus.completed
    ).scalar()
    revenue_today = rev_today_row or 0.0

    rev_total_row = db.query(func.sum(Order.total_price)).filter(Order.status == OrderStatus.completed).scalar()
    revenue_total = rev_total_row or 0.0

    total_users = db.query(User).count()

    bot_status_info = bot_manager.get_status()
    cfg = db.query(TelegramBotConfig).first()
    if cfg:
        bot_status_info["status"] = cfg.bot_status.value if hasattr(cfg.bot_status, "value") else str(cfg.bot_status)

    # Revenue chart last 7 days
    chart_labels = []
    chart_data = []
    for i in range(6, -1, -1):
        day = datetime.utcnow().date() - timedelta(days=i)
        day_start = datetime.combine(day, datetime.min.time())
        day_end = day_start + timedelta(days=1)
        rev = db.query(func.sum(Order.total_price)).filter(
            Order.created_at >= day_start,
            Order.created_at < day_end,
            Order.status == OrderStatus.completed
        ).scalar() or 0.0
        chart_labels.append(day.strftime("%d/%m"))
        chart_data.append(float(rev))

    top_products = db.query(Product).order_by(Product.sold_count.desc()).limit(5).all()

    recent_orders = db.query(Order).order_by(Order.created_at.desc()).limit(10).all()

    api_statuses = db.query(ApiConnection).all()

    return templates.TemplateResponse(request, "dashboard.html", {
        
        "bot_status": bot_status_info,
        "total_products": total_products,
        "total_api_products": total_api_products,
        "active_apis": active_apis,
        "orders_today": orders_today,
        "orders_total": orders_total,
        "revenue_today": revenue_today,
        "revenue_total": revenue_total,
        "total_users": total_users,
        "revenue_chart_labels": json.dumps(chart_labels),
        "revenue_chart_data": json.dumps(chart_data),
        "top_products": top_products,
        "recent_orders": recent_orders,
        "api_statuses": api_statuses,
    })


@router.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    total_users = db.query(User).filter(User.is_banned == False).count()
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "broadcast.html", {
        "total_users": total_users,
        "flash": flash_msg,
        "result": request.session.pop("broadcast_result", None),
    })


@router.post("/broadcast/send")
async def broadcast_send(
    request: Request,
    db: Session = Depends(get_db),
    title: str = Form(...),
    content: str = Form(...),
    image: UploadFile = File(None),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)

    if not title.strip() or not content.strip():
        flash(request, "Vui lòng nhập tiêu đề và nội dung!", "error")
        return RedirectResponse(url="/broadcast", status_code=302)

    image_path = None
    if image and image.filename:
        ext = Path(image.filename).suffix
        fname = f"{uuid.uuid4().hex}{ext}"
        fpath = UPLOADS_DIR / fname
        fpath.write_bytes(await image.read())
        image_path = f"/uploads/{fname}"

    from services.broadcast_service import send_broadcast
    result = await send_broadcast(db, title.strip(), content.strip(), image_path=image_path)

    if result.get("error"):
        flash(request, result["error"], "error")
    else:
        flash(request, f"Đã gửi thông báo: {result['sent']}/{result['total']} thành công, {result['failed']} thất bại.")
        request.session["broadcast_result"] = result

    return RedirectResponse(url="/broadcast", status_code=302)
