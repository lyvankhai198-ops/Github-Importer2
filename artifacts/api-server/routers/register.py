"""
Tenant self-registration — anyone can sign up and get a free trial immediately.
Creates a new AdminUser (tenant) with expires_at = now + plan.trial_days.
"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from database import get_db
from models import AdminUser, Plan
from auth import hash_password

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

_DEFAULT_TRIAL_DAYS = 7


def _get_trial_plan(db: Session):
    """Return the cheapest active plan (Free / trial), or None."""
    return (
        db.query(Plan)
        .filter(Plan.is_active == True)
        .order_by(Plan.price_per_month.asc(), Plan.id.asc())
        .first()
    )


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, db: Session = Depends(get_db)):
    if request.session.get("admin_id"):
        return RedirectResponse(url="/", status_code=302)
    plans = db.query(Plan).filter(Plan.is_active == True).order_by(Plan.price_per_month.asc()).all()
    return templates.TemplateResponse(request, "register.html", {
        "plans": plans,
        "error": request.session.pop("reg_error", None),
        "success": request.session.pop("reg_success", None),
    })


@router.post("/register")
async def register_post(
    request: Request,
    db: Session = Depends(get_db),
    display_name: str = Form(...),
    username: str = Form(...),
    email: str = Form(""),
    password: str = Form(...),
    password2: str = Form(...),
):
    # Validation
    username = username.strip().lower()
    display_name = display_name.strip()
    email = email.strip()

    if not username or not password or not display_name:
        request.session["reg_error"] = "Vui lòng điền đầy đủ thông tin."
        return RedirectResponse(url="/register", status_code=302)

    if len(username) < 3:
        request.session["reg_error"] = "Tên đăng nhập phải có ít nhất 3 ký tự."
        return RedirectResponse(url="/register", status_code=302)

    if len(password) < 6:
        request.session["reg_error"] = "Mật khẩu phải có ít nhất 6 ký tự."
        return RedirectResponse(url="/register", status_code=302)

    if password != password2:
        request.session["reg_error"] = "Mật khẩu xác nhận không khớp."
        return RedirectResponse(url="/register", status_code=302)

    existing = db.query(AdminUser).filter(AdminUser.username == username).first()
    if existing:
        request.session["reg_error"] = "Tên đăng nhập đã tồn tại. Vui lòng chọn tên khác."
        return RedirectResponse(url="/register", status_code=302)

    # Find trial plan
    plan = _get_trial_plan(db)
    trial_days = plan.trial_days if plan else _DEFAULT_TRIAL_DAYS
    expires_at = datetime.utcnow() + timedelta(days=trial_days)

    tenant = AdminUser(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
        email=email or None,
        is_active=True,
        is_owner=False,
        plan_id=plan.id if plan else None,
        expires_at=expires_at,
    )
    db.add(tenant)
    db.commit()

    request.session["reg_success"] = (
        f"Đăng ký thành công! Tài khoản của bạn đã được kích hoạt với {trial_days} ngày dùng thử. "
        f"Đăng nhập ngay để bắt đầu."
    )
    return RedirectResponse(url="/register", status_code=302)
