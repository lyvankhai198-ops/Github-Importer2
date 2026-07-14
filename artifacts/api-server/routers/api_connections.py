from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from models import ApiConnection, ApiProduct, AuthType, ApiType
from crypto import encrypt, decrypt, mask_key
from services.api_service import (
    sync_api_products, test_api_connection, get_api_balance,
    start_sync_scheduler, stop_sync_scheduler,
)
from integrations.manager import api_manager
from integrations.canboso import CanBosoAdapter
from integrations.aicenter_buyer import AICenterBuyerAdapter

router = APIRouter()


def _resolve_base_url(base_url: str, api_type: str) -> str:
    """Fall back to the supplier's default base URL server-side if the admin
    picked that API type but left the field blank (safety net behind the
    client-side auto-fill in the add/edit connection form)."""
    base_url = (base_url or "").strip()
    if not base_url and api_type == ApiType.canboso_market.value:
        return CanBosoAdapter.DEFAULT_BASE_URL
    if not base_url and api_type == ApiType.aicenter_buyer.value:
        return AICenterBuyerAdapter.DEFAULT_BASE_URL
    return base_url
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def check_auth(request: Request):
    return request.session.get("admin_id")


def flash(request: Request, msg: str, type: str = "success"):
    request.session["flash"] = {"type": type, "msg": msg}


@router.get("/api-connections", response_class=HTMLResponse)
async def list_connections(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    connections = db.query(ApiConnection).order_by(ApiConnection.created_at.desc()).all()
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "api_connections.html", {
        
        "connections": connections,
        "mask_key": mask_key,
        "flash": flash_msg,
    })


@router.post("/api-connections/add")
async def add_connection(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    base_url: str = Form(""),
    api_key: str = Form(""),
    auth_type: str = Form("x_api_key"),
    api_type: str = Form("zampto_standard"),
    sync_interval_minutes: int = Form(60),
    is_active: str = Form("true"),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    base_url = _resolve_base_url(base_url, api_type)
    conn = ApiConnection(
        name=name,
        base_url=base_url.rstrip("/"),
        api_key_encrypted=encrypt(api_key) if api_key else "",
        auth_type=AuthType(auth_type),
        api_type=ApiType(api_type),
        sync_interval_minutes=sync_interval_minutes,
        is_active=(is_active == "true"),
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    if conn.is_active:
        # Start syncing immediately — don't make the admin wait for a full
        # app restart before the newly added connection begins auto-syncing.
        start_sync_scheduler(conn.id, conn.sync_interval_minutes)
    flash(request, "Kết nối API đã được thêm!")
    return RedirectResponse(url="/api-connections", status_code=302)


@router.post("/api-connections/{conn_id}/edit")
async def edit_connection(
    conn_id: int,
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    base_url: str = Form(""),
    api_key: str = Form(""),
    auth_type: str = Form("x_api_key"),
    api_type: str = Form("zampto_standard"),
    sync_interval_minutes: int = Form(60),
    is_active: str = Form("true"),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    conn = db.query(ApiConnection).filter(ApiConnection.id == conn_id).first()
    if not conn:
        flash(request, "Không tìm thấy kết nối!", "error")
        return RedirectResponse(url="/api-connections", status_code=302)
    base_url = _resolve_base_url(base_url, api_type)
    conn.name = name
    conn.base_url = base_url.rstrip("/")
    if api_key:
        conn.api_key_encrypted = encrypt(api_key)
    conn.auth_type = AuthType(auth_type)
    conn.api_type = ApiType(api_type)
    conn.sync_interval_minutes = sync_interval_minutes
    conn.is_active = (is_active == "true")
    db.commit()
    api_manager.invalidate(conn_id)
    # Re-apply the live scheduler so an interval change or reactivation takes
    # effect immediately instead of waiting for the next app restart.
    stop_sync_scheduler(conn_id)
    if conn.is_active:
        start_sync_scheduler(conn_id, conn.sync_interval_minutes)
    flash(request, "Kết nối API đã được cập nhật!")
    return RedirectResponse(url="/api-connections", status_code=302)


@router.post("/api-connections/{conn_id}/test")
async def test_connection(conn_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    result = await test_api_connection(db, conn_id)
    return JSONResponse(result)


@router.post("/api-connections/{conn_id}/balance")
async def get_balance(conn_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    result = await get_api_balance(db, conn_id)
    return JSONResponse(result)


@router.post("/api-connections/{conn_id}/sync")
async def sync_connection(conn_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    result = await sync_api_products(db, conn_id)
    return JSONResponse(result)


@router.post("/api-connections/{conn_id}/toggle")
async def toggle_connection(conn_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = db.query(ApiConnection).filter(ApiConnection.id == conn_id).first()
    if not conn:
        return JSONResponse({"error": "Not found"}, status_code=404)
    conn.is_active = not conn.is_active
    db.commit()
    stop_sync_scheduler(conn_id)
    if conn.is_active:
        start_sync_scheduler(conn_id, conn.sync_interval_minutes)
    return JSONResponse({"is_active": conn.is_active})


@router.post("/api-connections/{conn_id}/delete")
async def delete_connection(conn_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    conn = db.query(ApiConnection).filter(ApiConnection.id == conn_id).first()
    if conn:
        stop_sync_scheduler(conn_id)
        db.delete(conn)
        db.commit()
        api_manager.invalidate(conn_id)
        flash(request, "Kết nối đã được xóa!")
    return RedirectResponse(url="/api-connections", status_code=302)


@router.get("/api-connections/{conn_id}/products", response_class=HTMLResponse)
async def view_products(conn_id: int, request: Request, db: Session = Depends(get_db), page: int = 1):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    conn = db.query(ApiConnection).filter(ApiConnection.id == conn_id).first()
    if not conn:
        return RedirectResponse(url="/api-connections", status_code=302)
    per_page = 20
    q = db.query(ApiProduct).filter(ApiProduct.api_connection_id == conn_id)
    total = q.count()
    products = q.order_by(ApiProduct.last_sync_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    return templates.TemplateResponse(request, "product_sources.html", {
        
        "api_products": products,
        "connections": [conn],
        "all_products": [],
        "selected_conn": conn_id,
        "page": page,
        "total": total,
        "per_page": per_page,
        "flash": None,
    })


@router.get("/api-connections/{conn_id}/orders", response_class=HTMLResponse)
async def view_orders(conn_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    from models import Order
    orders = db.query(Order).filter(Order.api_connection_id == conn_id).order_by(Order.created_at.desc()).limit(50).all()
    conn = db.query(ApiConnection).filter(ApiConnection.id == conn_id).first()
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "orders.html", {
        
        "orders": orders,
        "status_filter": "",
        "search": "",
        "date_from": "",
        "date_to": "",
        "page": 1,
        "total": len(orders),
        "per_page": 50,
        "flash": flash_msg,
        "order_statuses": [],
        "title": f"Đơn hàng từ {conn.name if conn else conn_id}",
    })
