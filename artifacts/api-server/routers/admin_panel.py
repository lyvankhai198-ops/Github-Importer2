"""
Super-Admin Panel — /admin prefix, owner-only.
All queries bypass tenant filter (skip_tenant_filter=True) so the owner
can see and manage every tenant's data from a single view.
"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from pathlib import Path

from database import get_db
from models import AdminUser, Plan, TelegramBotConfig, Order, Product, User, BotStatus, OrderStatus
from auth import hash_password
from services.bot_service import get_bot_manager

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


# ── helpers ─────────────────────────────────────────────────────────────────

def _require_owner(request: Request, db: Session):
    admin_id = request.session.get("admin_id")
    if not admin_id:
        return None
    admin = db.query(AdminUser).filter(AdminUser.id == admin_id, AdminUser.is_active == True).first()
    if not admin or not admin.is_owner:
        return None
    return admin


def flash(request: Request, msg: str, ftype: str = "success"):
    request.session["admin_flash"] = {"type": ftype, "msg": msg}


def _tenant_stats(db: Session, tenant_id: int) -> dict:
    """Quick per-tenant stats for the list and detail pages."""
    opts = {"skip_tenant_filter": True}
    products = (
        db.query(func.count(Product.id))
        .filter(Product.tenant_id == tenant_id)
        .execution_options(**opts).scalar() or 0
    )
    orders = (
        db.query(func.count(Order.id))
        .filter(Order.tenant_id == tenant_id)
        .execution_options(**opts).scalar() or 0
    )
    users = (
        db.query(func.count(User.id))
        .filter(User.tenant_id == tenant_id)
        .execution_options(**opts).scalar() or 0
    )
    cfg = (
        db.query(TelegramBotConfig)
        .filter(TelegramBotConfig.tenant_id == tenant_id)
        .execution_options(**opts).first()
    )
    bot_status = cfg.bot_status.value if cfg and cfg.bot_status else "stopped"
    shop_name = cfg.shop_name or "" if cfg else ""
    bot_username = cfg.bot_username or "" if cfg else ""
    return {
        "products": products,
        "orders": orders,
        "users": users,
        "bot_status": bot_status,
        "shop_name": shop_name,
        "bot_username": bot_username,
    }


# ── dashboard ────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    if not _require_owner(request, db):
        return RedirectResponse(url="/", status_code=302)

    opts = {"skip_tenant_filter": True}
    total_tenants = db.query(func.count(AdminUser.id)).filter(AdminUser.is_owner == False).scalar() or 0
    active_tenants = db.query(func.count(AdminUser.id)).filter(
        AdminUser.is_owner == False, AdminUser.is_active == True
    ).scalar() or 0
    expired_tenants = db.query(func.count(AdminUser.id)).filter(
        AdminUser.is_owner == False,
        AdminUser.expires_at != None,
        AdminUser.expires_at < datetime.utcnow(),
    ).scalar() or 0

    active_bots = db.query(func.count(TelegramBotConfig.id)).filter(
        TelegramBotConfig.bot_status == BotStatus.running
    ).execution_options(**opts).scalar() or 0

    total_orders = db.query(func.count(Order.id)).execution_options(**opts).scalar() or 0
    total_products = db.query(func.count(Product.id)).execution_options(**opts).scalar() or 0
    total_users = db.query(func.count(User.id)).execution_options(**opts).scalar() or 0

    # Recent tenants
    recent_tenants = (
        db.query(AdminUser)
        .filter(AdminUser.is_owner == False)
        .order_by(AdminUser.created_at.desc())
        .limit(8).all()
    )

    flash_msg = request.session.pop("admin_flash", None)
    return templates.TemplateResponse(request, "admin_dashboard.html", {
        "total_tenants": total_tenants,
        "active_tenants": active_tenants,
        "expired_tenants": expired_tenants,
        "inactive_tenants": total_tenants - active_tenants,
        "active_bots": active_bots,
        "total_orders": total_orders,
        "total_products": total_products,
        "total_users": total_users,
        "recent_tenants": recent_tenants,
        "flash": flash_msg,
        "now": datetime.utcnow(),
    })


# ── tenants list ─────────────────────────────────────────────────────────────

@router.get("/tenants", response_class=HTMLResponse)
async def admin_tenants(request: Request, db: Session = Depends(get_db)):
    if not _require_owner(request, db):
        return RedirectResponse(url="/", status_code=302)

    tenants = (
        db.query(AdminUser)
        .filter(AdminUser.is_owner == False)
        .order_by(AdminUser.created_at.desc())
        .all()
    )
    plans = db.query(Plan).order_by(Plan.price_per_month.asc()).all()

    # Attach quick stats per tenant
    for t in tenants:
        t._stats = _tenant_stats(db, t.id)

    flash_msg = request.session.pop("admin_flash", None)
    return templates.TemplateResponse(request, "admin_tenants.html", {
        "tenants": tenants,
        "plans": plans,
        "flash": flash_msg,
        "now": datetime.utcnow(),
    })


@router.post("/tenants/create")
async def admin_tenant_create(
    request: Request,
    db: Session = Depends(get_db),
    display_name: str = Form(...),
    username: str = Form(...),
    email: str = Form(""),
    password: str = Form(...),
    plan_id: int = Form(None),
    trial_days: int = Form(7),
):
    if not _require_owner(request, db):
        return RedirectResponse(url="/", status_code=302)

    username = username.strip().lower()
    if db.query(AdminUser).filter(AdminUser.username == username).first():
        flash(request, f"Username '{username}' already exists.", "error")
        return RedirectResponse(url="/admin/tenants", status_code=302)

    plan = db.query(Plan).filter(Plan.id == plan_id).first() if plan_id else None
    days = plan.trial_days if plan else trial_days

    tenant = AdminUser(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name.strip(),
        email=email.strip() or None,
        is_active=True,
        is_owner=False,
        plan_id=plan.id if plan else None,
        expires_at=datetime.utcnow() + timedelta(days=days),
    )
    db.add(tenant)
    db.commit()
    flash(request, f"Tenant '{username}' created with {days} days access.")
    return RedirectResponse(url="/admin/tenants", status_code=302)


# ── tenant detail ─────────────────────────────────────────────────────────────

@router.get("/tenants/{tenant_id}", response_class=HTMLResponse)
async def admin_tenant_detail(request: Request, tenant_id: int, db: Session = Depends(get_db)):
    if not _require_owner(request, db):
        return RedirectResponse(url="/", status_code=302)

    tenant = db.query(AdminUser).filter(AdminUser.id == tenant_id, AdminUser.is_owner == False).first()
    if not tenant:
        flash(request, "Tenant not found.", "error")
        return RedirectResponse(url="/admin/tenants", status_code=302)

    plans = db.query(Plan).order_by(Plan.price_per_month.asc()).all()
    stats = _tenant_stats(db, tenant_id)

    # Recent orders for this tenant
    opts = {"skip_tenant_filter": True}
    recent_orders = (
        db.query(Order)
        .filter(Order.tenant_id == tenant_id)
        .execution_options(**opts)
        .order_by(Order.created_at.desc())
        .limit(5).all()
    )

    flash_msg = request.session.pop("admin_flash", None)
    return templates.TemplateResponse(request, "admin_tenant_detail.html", {
        "tenant": tenant,
        "plans": plans,
        "stats": stats,
        "recent_orders": recent_orders,
        "flash": flash_msg,
        "now": datetime.utcnow(),
    })


@router.post("/tenants/{tenant_id}/lock")
async def admin_tenant_lock(request: Request, tenant_id: int, db: Session = Depends(get_db)):
    if not _require_owner(request, db):
        return RedirectResponse(url="/", status_code=302)
    tenant = db.query(AdminUser).filter(AdminUser.id == tenant_id, AdminUser.is_owner == False).first()
    if tenant:
        tenant.is_active = not tenant.is_active
        db.commit()
        action = "unlocked" if tenant.is_active else "locked"
        flash(request, f"Account '{tenant.username}' has been {action}.")
    return RedirectResponse(url=f"/admin/tenants/{tenant_id}", status_code=302)


@router.post("/tenants/{tenant_id}/extend")
async def admin_tenant_extend(
    request: Request, tenant_id: int,
    db: Session = Depends(get_db),
    days: int = Form(...),
):
    if not _require_owner(request, db):
        return RedirectResponse(url="/", status_code=302)
    tenant = db.query(AdminUser).filter(AdminUser.id == tenant_id, AdminUser.is_owner == False).first()
    if tenant:
        base = max(tenant.expires_at or datetime.utcnow(), datetime.utcnow())
        tenant.expires_at = base + timedelta(days=days)
        if not tenant.is_active:
            tenant.is_active = True
        db.commit()
        flash(request, f"Extended {tenant.username} by {days} days. New expiry: {tenant.expires_at.strftime('%d/%m/%Y')}.")
    return RedirectResponse(url=f"/admin/tenants/{tenant_id}", status_code=302)


@router.post("/tenants/{tenant_id}/set-plan")
async def admin_tenant_set_plan(
    request: Request, tenant_id: int,
    db: Session = Depends(get_db),
    plan_id: int = Form(None),
):
    if not _require_owner(request, db):
        return RedirectResponse(url="/", status_code=302)
    tenant = db.query(AdminUser).filter(AdminUser.id == tenant_id, AdminUser.is_owner == False).first()
    if tenant:
        tenant.plan_id = plan_id or None
        db.commit()
        plan = db.query(Plan).filter(Plan.id == plan_id).first() if plan_id else None
        flash(request, f"Plan updated to: {plan.name if plan else 'None'}.")
    return RedirectResponse(url=f"/admin/tenants/{tenant_id}", status_code=302)


@router.post("/tenants/{tenant_id}/set-expiry")
async def admin_tenant_set_expiry(
    request: Request, tenant_id: int,
    db: Session = Depends(get_db),
    expires_at: str = Form(...),
):
    if not _require_owner(request, db):
        return RedirectResponse(url="/", status_code=302)
    tenant = db.query(AdminUser).filter(AdminUser.id == tenant_id, AdminUser.is_owner == False).first()
    if tenant:
        try:
            tenant.expires_at = datetime.strptime(expires_at, "%Y-%m-%d")
            if not tenant.is_active:
                tenant.is_active = True
            db.commit()
            flash(request, f"Expiry set to {expires_at}.")
        except ValueError:
            flash(request, "Invalid date format.", "error")
    return RedirectResponse(url=f"/admin/tenants/{tenant_id}", status_code=302)


@router.post("/tenants/{tenant_id}/bot-action")
async def admin_tenant_bot_action(
    request: Request, tenant_id: int,
    db: Session = Depends(get_db),
    action: str = Form(...),
):
    if not _require_owner(request, db):
        return RedirectResponse(url="/", status_code=302)

    opts = {"skip_tenant_filter": True}
    cfg = (
        db.query(TelegramBotConfig)
        .filter(TelegramBotConfig.tenant_id == tenant_id)
        .execution_options(**opts).first()
    )
    if not cfg:
        flash(request, "No bot configured for this tenant.", "error")
        return RedirectResponse(url=f"/admin/tenants/{tenant_id}", status_code=302)

    from crypto import decrypt
    token = decrypt(cfg.bot_token_encrypted) if cfg.bot_token_encrypted else ""
    if not token:
        flash(request, "Bot token not set.", "error")
        return RedirectResponse(url=f"/admin/tenants/{tenant_id}", status_code=302)

    import tenancy as _tenancy
    with _tenancy.tenant_scope(tenant_id):
        bm = get_bot_manager(tenant_id=tenant_id)
        if action == "start":
            await bm.start(token)
            flash(request, "Bot start requested.")
        elif action == "stop":
            await bm.stop()
            flash(request, "Bot stopped.")
        elif action == "restart":
            await bm.stop()
            await bm.start(token)
            flash(request, "Bot restarted.")

    return RedirectResponse(url=f"/admin/tenants/{tenant_id}", status_code=302)


@router.post("/tenants/{tenant_id}/delete")
async def admin_tenant_delete(request: Request, tenant_id: int, db: Session = Depends(get_db)):
    if not _require_owner(request, db):
        return RedirectResponse(url="/", status_code=302)
    tenant = db.query(AdminUser).filter(AdminUser.id == tenant_id, AdminUser.is_owner == False).first()
    if tenant:
        name = tenant.username
        tenant.is_active = False
        db.commit()
        flash(request, f"Account '{name}' has been deactivated.")
    return RedirectResponse(url="/admin/tenants", status_code=302)


# ── plans ─────────────────────────────────────────────────────────────────────

@router.get("/plans", response_class=HTMLResponse)
async def admin_plans(request: Request, db: Session = Depends(get_db)):
    if not _require_owner(request, db):
        return RedirectResponse(url="/", status_code=302)
    plans = db.query(Plan).order_by(Plan.price_per_month.asc(), Plan.id.asc()).all()
    # Count tenants per plan
    for p in plans:
        p._tenant_count = db.query(func.count(AdminUser.id)).filter(
            AdminUser.plan_id == p.id, AdminUser.is_owner == False
        ).scalar() or 0
    flash_msg = request.session.pop("admin_flash", None)
    return templates.TemplateResponse(request, "admin_plans.html", {
        "plans": plans,
        "flash": flash_msg,
    })


@router.post("/plans/create")
async def admin_plan_create(
    request: Request, db: Session = Depends(get_db),
    name: str = Form(...),
    description: str = Form(""),
    price_per_month: int = Form(0),
    trial_days: int = Form(7),
    max_products: str = Form(""),
    max_orders: str = Form(""),
    max_bots: int = Form(1),
):
    if not _require_owner(request, db):
        return RedirectResponse(url="/", status_code=302)
    if db.query(Plan).filter(Plan.name == name.strip()).first():
        flash(request, f"Plan '{name}' already exists.", "error")
        return RedirectResponse(url="/admin/plans", status_code=302)
    plan = Plan(
        name=name.strip(),
        description=description.strip() or None,
        price_per_month=price_per_month,
        trial_days=trial_days,
        max_products=int(max_products) if max_products.strip() else None,
        max_orders=int(max_orders) if max_orders.strip() else None,
        max_bots=max_bots,
    )
    db.add(plan)
    db.commit()
    flash(request, f"Plan '{plan.name}' created.")
    return RedirectResponse(url="/admin/plans", status_code=302)


@router.post("/plans/{plan_id}/edit")
async def admin_plan_edit(
    request: Request, plan_id: int, db: Session = Depends(get_db),
    name: str = Form(...),
    description: str = Form(""),
    price_per_month: int = Form(0),
    trial_days: int = Form(7),
    max_products: str = Form(""),
    max_orders: str = Form(""),
    max_bots: int = Form(1),
    is_active: str = Form(""),
):
    if not _require_owner(request, db):
        return RedirectResponse(url="/", status_code=302)
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if plan:
        plan.name = name.strip()
        plan.description = description.strip() or None
        plan.price_per_month = price_per_month
        plan.trial_days = trial_days
        plan.max_products = int(max_products) if max_products.strip() else None
        plan.max_orders = int(max_orders) if max_orders.strip() else None
        plan.max_bots = max_bots
        plan.is_active = is_active == "1"
        db.commit()
        flash(request, f"Plan '{plan.name}' updated.")
    return RedirectResponse(url="/admin/plans", status_code=302)


@router.post("/plans/{plan_id}/delete")
async def admin_plan_delete(request: Request, plan_id: int, db: Session = Depends(get_db)):
    if not _require_owner(request, db):
        return RedirectResponse(url="/", status_code=302)
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if plan:
        in_use = db.query(func.count(AdminUser.id)).filter(AdminUser.plan_id == plan_id).scalar() or 0
        if in_use:
            flash(request, f"Cannot delete: {in_use} tenant(s) on this plan.", "error")
        else:
            db.delete(plan)
            db.commit()
            flash(request, "Plan deleted.")
    return RedirectResponse(url="/admin/plans", status_code=302)
