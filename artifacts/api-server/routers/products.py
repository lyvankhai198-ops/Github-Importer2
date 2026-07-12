import os
import uuid
import logging
import traceback
from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from models import (
    Product, ApiProduct, ApiConnection, ProductSource, SourceType, DeliveryMode,
    InventoryItem, InventoryStatus, Order,
)
from services.api_service import sync_api_products
from config import UPLOADS_DIR

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

# delivery_mode values selectable in the admin UI (legacy "manual" is hidden — new
# products always get manual_admin/manual_stock/api_auto).
SELECTABLE_DELIVERY_MODES = {"manual_admin", "manual_stock", "api_auto"}


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

    from services.inventory_service import get_available_count
    for p in products:
        p.stock_available = get_available_count(db, p.id) if p.delivery_mode == DeliveryMode.manual_stock else None

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


def _validate_product_fields(request: Request, name: str, product_code: str, sale_price: float,
                              min_quantity: int, delivery_mode: str) -> str | None:
    """Returns an error message (Vietnamese) if invalid, else None."""
    if not name or not name.strip():
        return "Tên sản phẩm không được để trống!"
    if not product_code or not product_code.strip():
        return "Mã sản phẩm không được để trống!"
    if sale_price is None or sale_price < 0:
        return "Giá bán phải lớn hơn hoặc bằng 0!"
    if min_quantity is None or min_quantity < 1:
        return "Số lượng tối thiểu phải lớn hơn hoặc bằng 1!"
    if delivery_mode not in SELECTABLE_DELIVERY_MODES:
        return "Chế độ giao hàng không hợp lệ!"
    return None


async def _save_image(image: UploadFile) -> str | None:
    if image and image.filename:
        ext = Path(image.filename).suffix
        fname = f"{uuid.uuid4().hex}{ext}"
        fpath = UPLOADS_DIR / fname
        content = await image.read()
        fpath.write_bytes(content)
        return f"/uploads/{fname}"
    return None


@router.post("/products/add")
async def add_product(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    product_code: str = Form(...),
    description: str = Form(""),
    description_en: str = Form(""),
    sale_price: float = Form(0.0),
    min_quantity: int = Form(1),
    telegram_icon: str = Form(""),
    delivery_mode: str = Form("manual_admin"),
    allow_manual_order: str = Form(None),
    is_active: str = Form("true"),
    image: UploadFile = File(None),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)

    error = _validate_product_fields(request, name, product_code, sale_price, min_quantity, delivery_mode)
    if error:
        flash(request, error, "error")
        return RedirectResponse(url="/products", status_code=302)

    product_code = product_code.strip()
    existing = db.query(Product).filter(Product.product_code == product_code).first()
    if existing:
        flash(request, "Mã sản phẩm đã tồn tại!", "error")
        return RedirectResponse(url="/products", status_code=302)

    try:
        image_path = await _save_image(image)
        product = Product(
            name=name.strip(),
            product_code=product_code,
            description=description,
            description_en=description_en or None,
            sale_price=sale_price,
            min_quantity=min_quantity,
            telegram_icon=telegram_icon or None,
            delivery_mode=DeliveryMode(delivery_mode),
            allow_manual_order=bool(allow_manual_order),
            is_active=(is_active == "true"),
            image_path=image_path,
            source_type=SourceType.manual,
        )
        db.add(product)
        db.commit()
        flash(request, "Sản phẩm đã được thêm thành công!")
    except Exception:
        db.rollback()
        logger.error("add_product failed:\n" + traceback.format_exc())
        flash(request, "Có lỗi xảy ra khi thêm sản phẩm. Vui lòng thử lại!", "error")
    return RedirectResponse(url="/products", status_code=302)


@router.post("/products/{product_id}/edit")
async def edit_product(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    product_code: str = Form(...),
    description: str = Form(""),
    description_en: str = Form(""),
    sale_price: float = Form(0.0),
    min_quantity: int = Form(1),
    telegram_icon: str = Form(""),
    delivery_mode: str = Form("manual_admin"),
    allow_manual_order: str = Form(None),
    is_active: str = Form("true"),
    image: UploadFile = File(None),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)

    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        flash(request, "Sản phẩm không tồn tại!", "error")
        return RedirectResponse(url="/products", status_code=302)

    error = _validate_product_fields(request, name, product_code, sale_price, min_quantity, delivery_mode)
    if error:
        flash(request, error, "error")
        return RedirectResponse(url="/products", status_code=302)

    product_code = product_code.strip()
    dup = db.query(Product).filter(Product.product_code == product_code, Product.id != product_id).first()
    if dup:
        flash(request, "Mã sản phẩm đã tồn tại ở sản phẩm khác!", "error")
        return RedirectResponse(url="/products", status_code=302)

    try:
        product.name = name.strip()
        product.product_code = product_code
        product.description = description
        product.description_en = description_en or None
        product.sale_price = sale_price
        product.min_quantity = min_quantity
        product.telegram_icon = telegram_icon or None
        product.delivery_mode = DeliveryMode(delivery_mode)
        product.allow_manual_order = bool(allow_manual_order)
        product.is_active = (is_active == "true")

        image_path = await _save_image(image)
        if image_path:
            product.image_path = image_path

        db.commit()
        db.refresh(product)
        flash(request, "Sản phẩm đã được cập nhật!")
    except Exception:
        db.rollback()
        logger.error(f"edit_product({product_id}) failed:\n" + traceback.format_exc())
        flash(request, "Có lỗi xảy ra khi cập nhật sản phẩm. Vui lòng thử lại!", "error")
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
    if not product:
        return RedirectResponse(url="/products", status_code=302)

    has_orders = db.query(Order).filter(Order.product_id == product_id).first() is not None
    if has_orders:
        # Never hard-delete a product that has order history — deactivate instead.
        product.is_active = False
        db.commit()
        flash(request, "Sản phẩm đã có đơn hàng nên không thể xóa — đã chuyển sang trạng thái ẩn/ngừng bán!", "error")
        return RedirectResponse(url="/products", status_code=302)

    try:
        db.delete(product)
        db.commit()
        flash(request, "Sản phẩm đã được xóa!")
    except Exception:
        db.rollback()
        logger.error(f"delete_product({product_id}) failed:\n" + traceback.format_exc())
        flash(request, "Có lỗi xảy ra khi xóa sản phẩm!", "error")
    return RedirectResponse(url="/products", status_code=302)


# ── Product detail page ("kho tài khoản" management) ────────────────────────────

@router.get("/products/{product_id}", response_class=HTMLResponse)
async def product_detail(product_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        flash(request, "Sản phẩm không tồn tại!", "error")
        return RedirectResponse(url="/products", status_code=302)

    from services.inventory_service import get_inventory_counts
    counts = get_inventory_counts(db, product_id) if product.delivery_mode == DeliveryMode.manual_stock else None
    has_orders = db.query(Order).filter(Order.product_id == product_id).first() is not None
    orders_count = db.query(Order).filter(Order.product_id == product_id).count()

    inventory_page = 1
    inventory_items = []
    if product.delivery_mode == DeliveryMode.manual_stock:
        inventory_items = (
            db.query(InventoryItem)
            .filter(InventoryItem.product_id == product_id, InventoryItem.status != InventoryStatus.deleted)
            .order_by(InventoryItem.created_at.desc())
            .limit(100)
            .all()
        )

    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "product_detail.html", {
        "product": product,
        "counts": counts,
        "has_orders": has_orders,
        "orders_count": orders_count,
        "inventory_items": inventory_items,
        "flash": flash_msg,
    })


@router.post("/products/{product_id}/inventory/preview")
async def inventory_preview(product_id: int, request: Request, raw_text: str = Form(...)):
    """AJAX: parse pasted text and return valid/duplicate/error counts without saving."""
    if not check_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    from services.inventory_service import parse_bulk_accounts
    result = parse_bulk_accounts(raw_text)
    return JSONResponse({
        "valid_count": len(result["valid"]),
        "duplicates": result["duplicates"],
        "errors": result["errors"],
        "total_lines": result["total_lines"],
        "preview": result["valid"][:10],
    })


@router.post("/products/{product_id}/inventory/import")
async def inventory_import(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    raw_text: str = Form(...),
    cost_price: float = Form(0.0),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        flash(request, "Sản phẩm không tồn tại!", "error")
        return RedirectResponse(url="/products", status_code=302)
    if product.delivery_mode != DeliveryMode.manual_stock:
        flash(request, "Sản phẩm này không dùng chế độ kho tài khoản!", "error")
        return RedirectResponse(url=f"/products/{product_id}", status_code=302)

    from services.inventory_service import parse_bulk_accounts, add_inventory_items, notify_restock_if_enabled, process_waiting_orders_for_product

    try:
        parsed = parse_bulk_accounts(raw_text)
        result = add_inventory_items(db, product_id, parsed["valid"], cost_price=cost_price)

        summary = (
            f"Đã nhập {result['inserted']} tài khoản mới "
            f"({parsed['duplicates']} trùng trong nội dung dán, "
            f"{result['skipped_existing']} đã có sẵn trong kho, "
            f"{parsed['errors']} dòng lỗi). "
            f"Tồn kho hiện tại: {result['after_count']}."
        )
        flash(request, summary)

        if result["inserted"] > 0:
            if result["back_in_stock"]:
                await notify_restock_if_enabled(product_id, back_in_stock=True)
            await process_waiting_orders_for_product(product_id)

    except Exception:
        db.rollback()
        logger.error(f"inventory_import({product_id}) failed:\n" + traceback.format_exc())
        flash(request, "Có lỗi xảy ra khi nhập kho. Vui lòng thử lại!", "error")

    return RedirectResponse(url=f"/products/{product_id}", status_code=302)


@router.get("/products/{product_id}/inventory/export")
async def inventory_export(product_id: int, request: Request, db: Session = Depends(get_db), status: str = "available"):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    try:
        status_enum = InventoryStatus(status)
    except ValueError:
        status_enum = InventoryStatus.available

    items = db.query(InventoryItem).filter(
        InventoryItem.product_id == product_id,
        InventoryItem.status == status_enum,
    ).order_by(InventoryItem.created_at.asc()).all()

    lines = [it.raw_value or f"{it.username or ''}|{it.password or ''}" for it in items]
    content = "\n".join(lines)
    product = db.query(Product).filter(Product.id == product_id).first()
    fname = f"{(product.product_code if product else product_id)}_{status_enum.value}.txt"
    return PlainTextResponse(content, headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/products/{product_id}/inventory/{item_id}/delete")
async def inventory_delete_item(product_id: int, item_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    item = db.query(InventoryItem).filter(InventoryItem.id == item_id, InventoryItem.product_id == product_id).first()
    if item and item.status == InventoryStatus.available:
        item.status = InventoryStatus.deleted
        db.commit()
        flash(request, "Đã xóa tài khoản khỏi kho!")
    elif item:
        flash(request, "Chỉ có thể xóa tài khoản đang ở trạng thái 'available'!", "error")
    return RedirectResponse(url=f"/products/{product_id}", status_code=302)


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

    # Use sale_price if admin set it; otherwise default to source price
    final_price = sale_price if sale_price > 0 else (ap.external_price or 0.0)

    product = Product(
        name=ap.external_name or code,
        product_code=code,
        # Description from source (admin can override later)
        description=ap.external_description or "",
        sale_price=final_price,
        min_quantity=ap.external_min_quantity or 1,
        warranty=ap.external_warranty or "",
        duration=ap.external_duration or "",
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
