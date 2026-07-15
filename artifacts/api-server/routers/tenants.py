"""
Owner-only tenant account management: create/list/edit/lock/unlock/extend
rented-out admin accounts. Every AdminUser is a tenant (see models.py /
tenancy.py) — creating one here immediately gives that tenant an isolated
shop (own products/orders/users/settings/payment methods), auto-scoped by
the tenant_id contextvar machinery, with zero data visible from any other
tenant. Only the platform owner (AdminUser.is_owner) can see this page —
enforced both by hiding the nav link (templates/base.html) and by
check_owner() below on every route.
"""
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import AdminUser
from auth import hash_password

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def check_owner(request: Request, db: Session):
    admin_id = request.session.get("admin_id")
    if not admin_id:
        return None
    admin = db.query(AdminUser).filter(AdminUser.id == admin_id, AdminUser.is_active == True).first()
    if not admin or not admin.is_owner:
        return None
    return admin


def flash(request: Request, msg: str, type: str = "success"):
    request.session["flash"] = {"type": type, "msg": msg}


@router.get("/tenants", response_class=HTMLResponse)
async def tenants_list(request: Request, db: Session = Depends(get_db)):
    owner = check_owner(request, db)
    if not owner:
        return RedirectResponse(url="/", status_code=302)
    tenants = db.query(AdminUser).filter(AdminUser.is_owner == False).order_by(AdminUser.created_at.desc()).all()
    flash_msg = request.session.pop("flash", None)
    now = datetime.utcnow()
    return templates.TemplateResponse(request, "tenants.html", {
        "tenants": tenants,
        "now": now,
        "flash": flash_msg,
    })


@router.post("/tenants/add")
async def add_tenant(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(""),
    expires_in_days: int = Form(30),
    notes: str = Form(""),
):
    owner = check_owner(request, db)
    if not owner:
        return RedirectResponse(url="/", status_code=302)

    username = username.strip()
    if not username or not password:
        flash(request, "Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu.", "danger")
        return RedirectResponse(url="/tenants", status_code=302)

    existing = db.query(AdminUser).filter(AdminUser.username == username).first()
    if existing:
        flash(request, f"Tên đăng nhập '{username}' đã tồn tại.", "danger")
        return RedirectResponse(url="/tenants", status_code=302)

    expires_at = None
    if expires_in_days and expires_in_days > 0:
        expires_at = datetime.utcnow() + timedelta(days=expires_in_days)

    tenant = AdminUser(
        username=username,
        password_hash=hash_password(password),
        is_active=True,
        is_owner=False,
        display_name=display_name.strip() or None,
        notes=notes.strip() or None,
        expires_at=expires_at,
    )
    db.add(tenant)
    db.commit()

    # Give the brand-new tenant working defaults (payment methods, ranks)
    # instead of an empty shop, scoped strictly to their own tenant_id.
    from tenancy import tenant_scope
    from main import _seed_payment_methods, _seed_ranks
    with tenant_scope(tenant.id):
        _seed_payment_methods()
        _seed_ranks()

    flash(request, f"Đã tạo tài khoản khách thuê '{username}'.")
    return RedirectResponse(url="/tenants", status_code=302)


@router.post("/tenants/{tenant_id}/extend")
async def extend_tenant(
    request: Request,
    tenant_id: int,
    db: Session = Depends(get_db),
    extra_days: int = Form(30),
):
    owner = check_owner(request, db)
    if not owner:
        return RedirectResponse(url="/", status_code=302)
    tenant = db.query(AdminUser).filter(AdminUser.id == tenant_id, AdminUser.is_owner == False).first()
    if tenant:
        base = tenant.expires_at if (tenant.expires_at and tenant.expires_at > datetime.utcnow()) else datetime.utcnow()
        tenant.expires_at = base + timedelta(days=extra_days)
        tenant.is_active = True
        db.commit()
        flash(request, f"Đã gia hạn {extra_days} ngày cho '{tenant.username}'.")
    return RedirectResponse(url="/tenants", status_code=302)


@router.post("/tenants/{tenant_id}/toggle")
async def toggle_tenant(request: Request, tenant_id: int, db: Session = Depends(get_db)):
    owner = check_owner(request, db)
    if not owner:
        return RedirectResponse(url="/", status_code=302)
    tenant = db.query(AdminUser).filter(AdminUser.id == tenant_id, AdminUser.is_owner == False).first()
    if tenant:
        tenant.is_active = not tenant.is_active
        db.commit()
        flash(request, f"'{tenant.username}' đã {'được mở khoá' if tenant.is_active else 'bị khoá'}.")
    return RedirectResponse(url="/tenants", status_code=302)


@router.post("/tenants/{tenant_id}/reset-password")
async def reset_tenant_password(
    request: Request,
    tenant_id: int,
    db: Session = Depends(get_db),
    new_password: str = Form(...),
):
    owner = check_owner(request, db)
    if not owner:
        return RedirectResponse(url="/", status_code=302)
    tenant = db.query(AdminUser).filter(AdminUser.id == tenant_id, AdminUser.is_owner == False).first()
    if tenant and new_password:
        tenant.password_hash = hash_password(new_password)
        db.commit()
        flash(request, f"Đã đặt lại mật khẩu cho '{tenant.username}'.")
    return RedirectResponse(url="/tenants", status_code=302)


@router.post("/tenants/{tenant_id}/delete")
async def delete_tenant(request: Request, tenant_id: int, db: Session = Depends(get_db)):
    owner = check_owner(request, db)
    if not owner:
        return RedirectResponse(url="/", status_code=302)
    tenant = db.query(AdminUser).filter(AdminUser.id == tenant_id, AdminUser.is_owner == False).first()
    if tenant:
        # The tenant's own shop data (products/orders/users/...) is left in
        # place, orphaned under their tenant_id — deleting an account must
        # never silently delete a shop's order/financial history. It simply
        # becomes inaccessible (no login can reach that tenant_id anymore).
        db.delete(tenant)
        db.commit()
        flash(request, f"Đã xoá tài khoản '{tenant.username}'. Dữ liệu đơn hàng/sản phẩm của họ được giữ lại.")
    return RedirectResponse(url="/tenants", status_code=302)
