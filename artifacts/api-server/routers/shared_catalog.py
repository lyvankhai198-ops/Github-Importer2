"""
"Kho hàng chung" — lets a tenant browse products from API connections the
OWNER has already set up and shared (ApiConnection.is_shared_with_tenants),
then "treo lên Chợ" (attach) one with their own sale price — without ever
creating their own ApiConnection/API key. See services/shared_catalog.py for
the underlying cross-tenant resolution mechanics.
"""
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import AdminUser, ProductSource
from services import shared_catalog

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def check_auth(request: Request, db: Session):
    admin_id = request.session.get("admin_id")
    if not admin_id:
        return None
    return db.query(AdminUser).filter(AdminUser.id == admin_id, AdminUser.is_active == True).first()


def flash(request: Request, msg: str, type: str = "success"):
    request.session["flash"] = {"type": type, "msg": msg}


@router.get("/shared-catalog", response_class=HTMLResponse)
async def browse(request: Request, db: Session = Depends(get_db), connection_id: int | None = None):
    admin = check_auth(request, db)
    if not admin:
        return RedirectResponse(url="/login", status_code=302)

    connections = shared_catalog.get_shared_connections(db)
    selected_conn = None
    products = []
    if connection_id:
        selected_conn = shared_catalog.get_shared_connection(db, connection_id)
        if selected_conn:
            products = shared_catalog.get_shared_products(db, connection_id)
    elif len(connections) == 1:
        selected_conn = connections[0]
        products = shared_catalog.get_shared_products(db, selected_conn.id)

    # Which ApiProduct ids this tenant has already attached, so the template
    # can show "Đã treo" instead of the attach button.
    attached_ids = {
        s.api_product_id
        for s in db.query(ProductSource)
        .filter(ProductSource.tenant_id == admin.id, ProductSource.shared_from_admin == True)
        .all()
    }

    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "shared_catalog.html", {
        "connections": connections,
        "selected_conn": selected_conn,
        "products": products,
        "attached_ids": attached_ids,
        "flash": flash_msg,
    })


@router.post("/shared-catalog/attach")
async def attach(
    request: Request,
    db: Session = Depends(get_db),
    api_product_id: int = Form(...),
    sale_price: float = Form(...),
    connection_id: int | None = Form(None),
):
    admin = check_auth(request, db)
    if not admin:
        return RedirectResponse(url="/login", status_code=302)
    try:
        shared_catalog.attach_shared_product(db, admin.id, api_product_id, sale_price)
        flash(request, "Đã treo sản phẩm lên Chợ của bạn!")
    except ValueError as e:
        flash(request, str(e), "error")
    redirect_url = "/shared-catalog"
    if connection_id:
        redirect_url += f"?connection_id={connection_id}"
    return RedirectResponse(url=redirect_url, status_code=302)


@router.get("/shared-catalog/mine", response_class=HTMLResponse)
async def mine(request: Request, db: Session = Depends(get_db)):
    admin = check_auth(request, db)
    if not admin:
        return RedirectResponse(url="/login", status_code=302)
    sources = shared_catalog.get_attached_shared_sources(db, admin.id)
    from services.shared_catalog import resolve_api_product, resolve_product
    rows = []
    for s in sources:
        rows.append({
            "source": s,
            "product": resolve_product(db, s),
            "api_product": resolve_api_product(db, s),
        })
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "shared_catalog_mine.html", {
        "rows": rows,
        "flash": flash_msg,
    })


@router.post("/shared-catalog/detach/{product_id}")
async def detach(product_id: int, request: Request, db: Session = Depends(get_db)):
    admin = check_auth(request, db)
    if not admin:
        return RedirectResponse(url="/login", status_code=302)
    if shared_catalog.detach_shared_product(db, admin.id, product_id):
        flash(request, "Đã ẩn sản phẩm khỏi Chợ của bạn.")
    else:
        flash(request, "Không tìm thấy sản phẩm.", "error")
    return RedirectResponse(url="/shared-catalog/mine", status_code=302)
