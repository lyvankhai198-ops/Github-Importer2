import os
import uuid
from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from models import Product, ApiProduct, ApiConnection, ProductSource, SourceType, DeliveryMode
from services.api_service import sync_api_products
from config import UPLOADS_DIR

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def check_auth(request: Request):
    return request.session.get("admin_id")


def flash(request: Request, msg: str, type: str = "success"):
    request.session["flash"] = {"type": type, "msg": msg}


@router.get("/products", response_class=HTMLResponse)
async def products_list(request: Request, db: Session = Depends(get_db), search: str = "", source_type: str = "", is_active: str = "", page: int = 1):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    q = db.query(Product)
    if search:
        q = q.filter(Product.name.ilike(f"%{search}%") | Product.product_code.ilike(f"%{search}%"))
    if source_type:
        q = q.filter(Product.source_type == source_type)
    if is_active:
        q = q.filter(Product.is_active == (is_active == "true"))
    total = q.count()
    per_page = 20
    products = q.order_by(Product.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    api_connections = db.query(ApiConnection).filter(ApiConnection.is_active == True).all()
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "products.html", {
        
        "products": products,
        "api_connections": api_connections,
        "search": search,
        "source_type_filter": source_type,
        "is_active_filter": is_active,
        "page": page,
        "total": total,
        "per_page": per_page,
        "flash": flash_msg,
    })


@router.post("/products/add")
async def add_product(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    product_code: str = Form(...),
    description: str = Form(""),
    sale_price: float = Form(0.0),
    delivery_mode: str = Form("manual"),
    is_active: str = Form("true"),
    image: UploadFile = File(None),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    existing = db.query(Product).filter(Product.product_code == product_code).first()
    if existing:
        flash(request, "Mã sản phẩm đã tồn tại!", "error")
        return RedirectResponse(url="/products", status_code=302)
    image_path = None
    if image and image.filename:
        ext = Path(image.filename).suffix
        fname = f"{uuid.uuid4().hex}{ext}"
        fpath = UPLOADS_DIR / fname
        content = await image.read()
        fpath.write_bytes(content)
        image_path = f"/uploads/{fname}"
    product = Product(
        name=name,
        product_code=product_code,
        description=description,
        sale_price=sale_price,
        delivery_mode=DeliveryMode(delivery_mode),
        is_active=(is_active == "true"),
        image_path=image_path,
        source_type=SourceType.manual,
    )
    db.add(product)
    db.commit()
    flash(request, "Sản phẩm đã được thêm thành công!")
    return RedirectResponse(url="/products", status_code=302)


@router.post("/products/{product_id}/edit")
async def edit_product(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    product_code: str = Form(...),
    description: str = Form(""),
    sale_price: float = Form(0.0),
    delivery_mode: str = Form("manual"),
    is_active: str = Form("true"),
    image: UploadFile = File(None),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        flash(request, "Sản phẩm không tồn tại!", "error")
        return RedirectResponse(url="/products", status_code=302)
    product.name = name
    product.product_code = product_code
    product.description = description
    product.sale_price = sale_price
    product.delivery_mode = DeliveryMode(delivery_mode)
    product.is_active = (is_active == "true")
    if image and image.filename:
        ext = Path(image.filename).suffix
        fname = f"{uuid.uuid4().hex}{ext}"
        fpath = UPLOADS_DIR / fname
        content = await image.read()
        fpath.write_bytes(content)
        product.image_path = f"/uploads/{fname}"
    db.commit()
    flash(request, "Sản phẩm đã được cập nhật!")
    return RedirectResponse(url="/products", status_code=302)


@router.post("/products/{product_id}/toggle")
async def toggle_product(product_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return JSONResponse({"error": "Not found"}, status_code=404)
    product.is_active = not product.is_active
    db.commit()
    return JSONResponse({"is_active": product.is_active})


@router.post("/products/{product_id}/delete")
async def delete_product(product_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    product = db.query(Product).filter(Product.id == product_id).first()
    if product:
        db.delete(product)
        db.commit()
        flash(request, "Sản phẩm đã được xóa!")
    return RedirectResponse(url="/products", status_code=302)


@router.get("/products/api-sources", response_class=HTMLResponse)
async def api_sources(request: Request, db: Session = Depends(get_db), conn_id: int = 0, page: int = 1):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    q = db.query(ApiProduct)
    if conn_id:
        q = q.filter(ApiProduct.api_connection_id == conn_id)
    total = q.count()
    per_page = 20
    api_products = q.order_by(ApiProduct.last_sync_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    connections = db.query(ApiConnection).all()
    all_products = db.query(Product).filter(Product.is_active == True).all()
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "product_sources.html", {
        
        "api_products": api_products,
        "connections": connections,
        "all_products": all_products,
        "selected_conn": conn_id,
        "page": page,
        "total": total,
        "per_page": per_page,
        "flash": flash_msg,
    })


@router.post("/products/api-sources/{api_product_id}/create-product")
async def create_product_from_source(
    api_product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    sale_price: float = Form(0.0),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    ap = db.query(ApiProduct).filter(ApiProduct.id == api_product_id).first()
    if not ap:
        flash(request, "Không tìm thấy sản phẩm nguồn!", "error")
        return RedirectResponse(url="/products/api-sources", status_code=302)
    code = f"API-{ap.api_connection_id}-{ap.external_product_id}"
    existing = db.query(Product).filter(Product.product_code == code).first()
    if existing:
        flash(request, "Sản phẩm đã tồn tại!", "error")
        return RedirectResponse(url="/products/api-sources", status_code=302)
    product = Product(
        name=ap.external_name or code,
        product_code=code,
        sale_price=sale_price or (ap.external_price or 0.0),
        source_type=SourceType.api,
        delivery_mode=DeliveryMode.api_auto,
        is_active=True,
    )
    db.add(product)
    db.flush()
    source = ProductSource(
        product_id=product.id,
        api_product_id=ap.id,
        priority=1,
        is_active=True,
        last_cost=ap.external_price,
        last_stock=ap.external_stock,
    )
    db.add(source)
    db.commit()
    flash(request, "Sản phẩm đã được tạo từ nguồn API!")
    return RedirectResponse(url="/products/api-sources", status_code=302)


@router.post("/products/api-sources/{api_product_id}/link-product")
async def link_api_product(
    api_product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    product_id: int = Form(...),
    priority: int = Form(1),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    ap = db.query(ApiProduct).filter(ApiProduct.id == api_product_id).first()
    if not ap:
        flash(request, "Không tìm thấy sản phẩm nguồn!", "error")
        return RedirectResponse(url="/products/api-sources", status_code=302)
    existing = db.query(ProductSource).filter(
        ProductSource.product_id == product_id,
        ProductSource.api_product_id == api_product_id
    ).first()
    if existing:
        flash(request, "Nguồn đã được liên kết!", "error")
        return RedirectResponse(url="/products/api-sources", status_code=302)
    source = ProductSource(
        product_id=product_id,
        api_product_id=ap.id,
        priority=priority,
        is_active=True,
        last_cost=ap.external_price,
        last_stock=ap.external_stock,
    )
    db.add(source)
    db.commit()
    flash(request, "Liên kết nguồn thành công!")
    return RedirectResponse(url="/products/api-sources", status_code=302)


@router.post("/products/{product_id}/sources")
async def add_product_source(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    api_product_id: int = Form(...),
    priority: int = Form(1),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    source = ProductSource(
        product_id=product_id,
        api_product_id=api_product_id,
        priority=priority,
        is_active=True,
    )
    db.add(source)
    db.commit()
    flash(request, "Nguồn đã được thêm!")
    return RedirectResponse(url="/products", status_code=302)


@router.post("/products/sources/{source_id}/delete")
async def delete_product_source(source_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    source = db.query(ProductSource).filter(ProductSource.id == source_id).first()
    if source:
        db.delete(source)
        db.commit()
        flash(request, "Nguồn đã được xóa!")
    return RedirectResponse(url="/products", status_code=302)


@router.post("/products/sync-all")
async def sync_all_products(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    connections = db.query(ApiConnection).filter(ApiConnection.is_active == True).all()
    for conn in connections:
        await sync_api_products(db, conn.id)
    flash(request, f"Đã đồng bộ {len(connections)} kết nối API!")
    return RedirectResponse(url="/products/api-sources", status_code=302)
