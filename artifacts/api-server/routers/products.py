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
from services.normalize import compute_price_usdt
from config import UPLOADS_DIR


def _current_retail_rate(db: Session) -> float:
    """VND-per-USDT rate used for auto-computing product.price_usdt. Reuses the
    same admin-editable exchange rate config used for crypto payments (settings
    key "exchange_rate_config", fixed_rate field) so there is a single source
    of truth for "1 USDT = N VND"."""
    from services.exchange_rate_service import get_exchange_config
    cfg = get_exchange_config(db)
    return float(cfg.get("fixed_rate") or 26500.0)

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
async def products_list(
    request: Request, db: Session = Depends(get_db),
    search: str = "", source_type: str = "", is_active: str = "",
    brand: str = "", stock: str = "", page: int = 1,
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    from services.inventory_service import get_available_count
    from services.normalize import compute_brand_key

    q = db.query(Product)
    if search:
        q = q.filter(Product.name.ilike(f"%{search}%") | Product.product_code.ilike(f"%{search}%"))
    if source_type:
        q = q.filter(Product.source_type == source_type)
    if is_active:
        q = q.filter(Product.is_active == (is_active == "true"))

    # brand/stock filters depend on values computed in Python (brand grouping
    # key, live inventory count) rather than plain columns, so when either is
    # active we page in Python after applying them instead of at the SQL level.
    per_page = 20
    if brand or stock:
        all_matching = q.order_by(Product.created_at.desc()).all()
        for p in all_matching:
            p.stock_available = get_available_count(db, p.id) if p.delivery_mode == DeliveryMode.manual_stock else None
        if brand:
            all_matching = [p for p in all_matching if compute_brand_key(p.name) == brand]
        if stock == "in":
            all_matching = [p for p in all_matching if p.delivery_mode != DeliveryMode.manual_stock or (p.stock_available or 0) > 0]
        elif stock == "out":
            all_matching = [p for p in all_matching if p.delivery_mode == DeliveryMode.manual_stock and (p.stock_available or 0) <= 0]
        total = len(all_matching)
        products = all_matching[(page - 1) * per_page: page * per_page]
    else:
        total = q.count()
        products = q.order_by(Product.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
        for p in products:
            p.stock_available = get_available_count(db, p.id) if p.delivery_mode == DeliveryMode.manual_stock else None

    # Distinct brand keys across all products (unfiltered) so the brand
    # dropdown always lists every brand, not just the ones on the current page.
    brand_keys = sorted({compute_brand_key(p.name) for p in db.query(Product.name).all() if compute_brand_key(p.name)})

    api_connections = db.query(ApiConnection).filter(ApiConnection.is_active == True).all()
    from models import EmojiIcon
    emoji_icons = db.query(EmojiIcon).filter(EmojiIcon.is_active == True).order_by(EmojiIcon.sort_order.asc(), EmojiIcon.id.asc()).all()
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "products.html", {
        
        "products": products,
        "api_connections": api_connections,
        "emoji_icons": emoji_icons,
        "search": search,
        "source_type_filter": source_type,
        "is_active_filter": is_active,
        "brand_filter": brand,
        "stock_filter": stock,
        "brand_keys": brand_keys,
        "page": page,
        "total": total,
        "per_page": per_page,
        "flash": flash_msg,
    })


def _parse_optional_float(raw: str | None) -> float | None:
    """Blank/whitespace-only form field -> None (no limit); otherwise parsed float."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


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
    name_en: str = Form(""),
    product_code: str = Form(...),
    description: str = Form(""),
    description_en: str = Form(""),
    sale_price: float = Form(0.0),
    source_price: float = Form(0.0),
    auto_adjust_price: str = Form(None),
    min_sale_price: str = Form(""),
    max_sale_price: str = Form(""),
    require_admin_approval_above_percent: str = Form(""),
    min_quantity: int = Form(1),
    telegram_icon: str = Form(""),
    telegram_custom_emoji_id: str = Form(""),
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
        from services.product_sync import resolve_bilingual_fields, sync_translations, apply_admin_icon_edit, auto_assign_icon_if_unlocked
        from services.translation_alerts import notify_admin_translation_failed
        from services.price_sync_service import compute_margin

        image_path = await _save_image(image)
        # Detect which language box the admin actually filled in — if only
        # the Vietnamese box has English-looking text and both English
        # boxes are blank, treat it as an English-sourced product (see
        # resolve_bilingual_fields) so translation fills the Vietnamese
        # side instead of the (default) English side.
        r_name, r_desc, r_name_en, r_desc_en, src_lang = resolve_bilingual_fields(
            None, None, name, description, name_en, description_en
        )
        product = Product(
            name=(r_name or r_name_en or "").strip(),
            name_en=r_name_en,
            name_en_locked=bool(r_name_en),
            product_code=product_code,
            description=r_desc or "",
            description_en=r_desc_en,
            description_en_locked=bool(r_desc_en),
            source_language=src_lang,
            sale_price=sale_price,
            source_price=source_price or 0.0,
            price_margin=compute_margin(sale_price, source_price or 0.0),
            auto_adjust_price=bool(auto_adjust_price),
            min_sale_price=_parse_optional_float(min_sale_price),
            max_sale_price=_parse_optional_float(max_sale_price),
            require_admin_approval_above_percent=_parse_optional_float(require_admin_approval_above_percent),
            price_usdt=compute_price_usdt(sale_price, _current_retail_rate(db)),
            min_quantity=min_quantity,
            delivery_mode=DeliveryMode(delivery_mode),
            allow_manual_order=bool(allow_manual_order),
            is_active=(is_active == "true"),
            image_path=image_path,
            source_type=SourceType.manual,
        )
        # Admin-entered icon locks it against auto-assignment; otherwise
        # auto-assign one from the name-keyword mapping (e.g. "Grok" → 🤖).
        apply_admin_icon_edit(product, telegram_icon, telegram_custom_emoji_id)
        auto_assign_icon_if_unlocked(product)
        # Auto-translate whichever side the admin left blank (direction
        # depends on source_language, resolved above), so bilingual display
        # never falls back to raw untranslated text in either language.
        sync_translations(product)
        db.add(product)
        db.commit()
        if product.translation_status == "failed":
            await notify_admin_translation_failed(db, product)
        flash(request, "Sản phẩm đã được thêm thành công!")
        if product.is_active:
            from services.broadcast_service import notify_new_product_broadcast
            await notify_new_product_broadcast(product)
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
    name_en: str = Form(""),
    product_code: str = Form(...),
    description: str = Form(""),
    description_en: str = Form(""),
    sale_price: float = Form(0.0),
    source_price: float = Form(0.0),
    auto_adjust_price: str = Form(None),
    min_sale_price: str = Form(""),
    max_sale_price: str = Form(""),
    require_admin_approval_above_percent: str = Form(""),
    min_quantity: int = Form(1),
    telegram_icon: str = Form(""),
    telegram_custom_emoji_id: str = Form(""),
    delivery_mode: str = Form("manual_admin"),
    allow_manual_order: str = Form(None),
    is_active: str = Form("true"),
    warranty: str = Form(""),
    duration: str = Form(""),
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
        from services.product_sync import (
            apply_admin_edit, apply_admin_en_edit, sync_translations, resolve_bilingual_fields,
            apply_admin_icon_edit, auto_assign_icon_if_unlocked,
        )
        from services.translation_alerts import notify_admin_translation_failed
        from services.price_sync_service import apply_admin_price_edit

        # Detect which language box the admin actually filled in this time
        # (see resolve_bilingual_fields) — usually a no-op that just passes
        # the submitted values through unchanged, but lets an admin flip a
        # product between Vietnamese-sourced and English-sourced by simply
        # typing in the "other" box, or flip back with an explicit VI edit.
        r_name, r_desc, r_name_en, r_desc_en, src_lang = resolve_bilingual_fields(
            product.source_language, product.description, name, description, name_en, description_en
        )
        product.source_language = src_lang
        product.name = (r_name or r_name_en or product.name or "").strip()
        product.product_code = product_code
        # name_en/description_en: only flag as manually-locked when the
        # admin's submitted value actually differs from what's stored —
        # resubmitting the same (possibly auto-translated) text must not
        # freeze it against future auto-translation.
        apply_admin_en_edit(product, r_name_en, r_desc_en)
        # Admin-entered sale_price/source_price always recompute price_margin
        # (spec §2: editing sale price by hand re-derives the margin to keep).
        apply_admin_price_edit(db, product, source_price or 0.0, sale_price, changed_by=request.session.get("admin_id"))
        product.price_usdt = compute_price_usdt(product.sale_price, _current_retail_rate(db))
        product.auto_adjust_price = bool(auto_adjust_price)
        product.min_sale_price = _parse_optional_float(min_sale_price)
        product.max_sale_price = _parse_optional_float(max_sale_price)
        product.require_admin_approval_above_percent = _parse_optional_float(require_admin_approval_above_percent)
        product.min_quantity = min_quantity
        # Admin-entered icon locks it against auto-assignment; clearing it
        # back to blank unlocks auto-assignment from the name again.
        apply_admin_icon_edit(product, telegram_icon, telegram_custom_emoji_id)
        auto_assign_icon_if_unlocked(product)
        product.delivery_mode = DeliveryMode(delivery_mode)
        product.allow_manual_order = bool(allow_manual_order)
        product.is_active = (is_active == "true")

        # description/warranty/duration: only flag as "manually edited" (and
        # thus frozen against future API sync) when the admin actually
        # changed the value — resubmitting the same text leaves it untouched.
        # When source_language == "en", `description` (vi) is a translation
        # target, not an admin-authored field — never freeze it against API
        # sync via manually_edited_fields; sync_translations() below fills it.
        if src_lang == "vi":
            apply_admin_edit(product, {
                "description": r_desc or "",
                "warranty": warranty,
                "duration": duration,
            })
        else:
            apply_admin_edit(product, {
                "warranty": warranty,
                "duration": duration,
            })

        image_path = await _save_image(image)
        if image_path:
            product.image_path = image_path
            from services.product_sync import mark_fields_edited
            mark_fields_edited(product, {"image_path"})

        # Fill in whichever side the admin left blank (direction depends on
        # source_language, resolved above) so display never falls back to
        # raw untranslated text in either language.
        sync_translations(product)

        db.commit()
        db.refresh(product)
        if product.translation_status == "failed":
            await notify_admin_translation_failed(db, product)
        flash(request, "Sản phẩm đã được cập nhật!")
    except Exception:
        db.rollback()
        logger.error(f"edit_product({product_id}) failed:\n" + traceback.format_exc())
        flash(request, "Có lỗi xảy ra khi cập nhật sản phẩm. Vui lòng thử lại!", "error")
    return RedirectResponse(url="/products", status_code=302)


@router.post("/products/generate_en")
async def generate_en_preview(
    request: Request,
    name: str = Form(""),
    description: str = Form(""),
):
    """
    "🌐 Tạo bản tiếng Anh" — returns an auto-translated EN name/description
    preview (name via the fixed shorthand table, description via the LLM
    translator — see services.translation_service) for the admin to review
    and edit before saving. Does not touch the database. Also doubles as
    "Dịch lại mô tả tiếng Anh" for an existing product: the edit modal opens
    pre-filled with the current Vietnamese/English text, and clicking this
    button regenerates the English preview from the current Vietnamese
    description for the admin to review before saving.
    """
    if not check_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    from services.normalize import translate_product_name_to_en
    from services.translation_service import translate_description_with_fallback
    from services.text_protect import format_description
    desc_en = translate_description_with_fallback(description) if description else ""
    return JSONResponse({
        "name_en": translate_product_name_to_en(name) if name else "",
        "description_en": format_description(desc_en) if desc_en else "",
    })


@router.post("/products/generate_vi")
async def generate_vi_preview(
    request: Request,
    name_en: str = Form(""),
    description_en: str = Form(""),
):
    """
    "🌐 Dịch lại sang tiếng Việt" — the reverse-direction counterpart of
    generate_en_preview: returns an auto-translated Vietnamese name/
    description preview from the current English text for the admin to
    review/edit before saving. Does not touch the database.
    """
    if not check_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    from services.normalize import translate_product_name_to_vi
    from services.translation_service import translate_description_to_vi_with_fallback
    from services.text_protect import format_description
    desc_vi = translate_description_to_vi_with_fallback(description_en) if description_en else ""
    return JSONResponse({
        "name": translate_product_name_to_vi(name_en) if name_en else "",
        "description": format_description(desc_vi) if desc_vi else "",
    })


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


# ── Static /products/* routes MUST be declared before the dynamic
# /products/id/{product_id} route below, otherwise FastAPI/Starlette will
# match the static path as a {product_id} path param first and fail with an
# int_parsing error. Keep any new static /products/<literal> route above
# this comment block, and any new dynamic /products/id/{...} route below it.

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


@router.get("/products/api-sources/{api_product_id}/create-product")
async def create_product_from_source_get_guard(api_product_id: int, request: Request):
    """
    This action only supports POST (it's a form submit). Mobile browsers
    sometimes replay the last request as GET when the user taps "back"
    after submitting, which used to surface a raw {"detail":"Method Not
    Allowed"} JSON page. Redirect back to the source list instead of
    showing that error — the original POST already completed or failed
    on its own, so there's nothing to redo here.
    """
    return RedirectResponse(url="/products/api-sources", status_code=302)


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

    from services.normalize import auto_assign_emoji

    product = Product(
        name=ap.external_name or code,
        product_code=code,
        # Description from source (admin can override later)
        description=ap.external_description or "",
        sale_price=final_price,
        price_usdt=compute_price_usdt(final_price, _current_retail_rate(db)),
        min_quantity=ap.external_min_quantity or 1,
        warranty=ap.external_warranty or "",
        duration=ap.external_duration or "",
        telegram_icon=auto_assign_emoji(ap.external_name or code),
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
    if product.is_active:
        from services.broadcast_service import notify_new_product_broadcast
        await notify_new_product_broadcast(product)
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


# ── Product detail page ("kho tài khoản" management) — dynamic route, must
# stay below every static /products/<literal> route declared above. ────────

@router.get("/products/id/{product_id}", response_class=HTMLResponse)
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

    from models import ProductPriceHistory
    price_history = (
        db.query(ProductPriceHistory)
        .filter(ProductPriceHistory.product_id == product_id)
        .order_by(ProductPriceHistory.created_at.desc())
        .limit(30)
        .all()
    )

    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "product_detail.html", {
        "product": product,
        "counts": counts,
        "has_orders": has_orders,
        "orders_count": orders_count,
        "inventory_items": inventory_items,
        "price_history": price_history,
        "flash": flash_msg,
    })


@router.post("/products/{product_id}/price/approve")
async def approve_price(product_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        flash(request, "Sản phẩm không tồn tại!", "error")
        return RedirectResponse(url="/products", status_code=302)
    from services.price_sync_service import approve_pending_price
    result = await approve_pending_price(db, product, exchange_rate=_current_retail_rate(db), changed_by=request.session.get("admin_id"))
    if result.get("action") == "applied":
        flash(request, "Đã áp dụng giá nguồn mới!")
    else:
        flash(request, "Không có thay đổi giá đang chờ duyệt.", "error")
    return RedirectResponse(url=f"/products/id/{product_id}", status_code=302)


@router.post("/products/{product_id}/price/reject")
async def reject_price(product_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        flash(request, "Sản phẩm không tồn tại!", "error")
        return RedirectResponse(url="/products", status_code=302)
    from services.price_sync_service import reject_pending_price
    reject_pending_price(db, product)
    flash(request, "Đã từ chối thay đổi giá nguồn.")
    return RedirectResponse(url=f"/products/id/{product_id}", status_code=302)


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
        "error_lines": result.get("error_lines", []),
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
    notify_users: str = Form(None),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        flash(request, "Sản phẩm không tồn tại!", "error")
        return RedirectResponse(url="/products", status_code=302)
    if product.delivery_mode != DeliveryMode.manual_stock:
        flash(request, "Sản phẩm này không dùng chế độ kho tài khoản!", "error")
        return RedirectResponse(url=f"/products/id/{product_id}", status_code=302)

    from services.inventory_service import parse_bulk_accounts, add_inventory_items, notify_restock_if_enabled, process_waiting_orders_for_product
    from services.restock_notify_service import notify_restock_waiting_list

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

        if result["inserted"] > 0:
            if result["back_in_stock"]:
                await notify_restock_if_enabled(product_id, back_in_stock=True)
                # Only an actual 0 → positive stock transition counts as a
                # "restock" for the explicit notify-checkbox path — a routine
                # top-up of an already-in-stock product must never trigger a
                # mass notification (falls back to "notify all users" when
                # there's no per-product waiting list, so this gate matters).
                if notify_users:
                    notify_result = await notify_restock_waiting_list(product_id)
                    summary += (
                        f" Đã thông báo cho {notify_result['notified']} khách hàng"
                        f" ({notify_result['audience']})."
                    )
            # Auto "🔄 ĐÃ BỔ SUNG HÀNG" broadcast to all active users whenever
            # stock genuinely increased (any top-up, not just 0 → positive).
            if result["after_count"] > result["before_count"]:
                from services.broadcast_service import notify_restock_broadcast
                await notify_restock_broadcast(
                    product_id,
                    result["after_count"] - result["before_count"],
                    result["after_count"],
                )
            await process_waiting_orders_for_product(product_id)

        flash(request, summary)

    except Exception:
        db.rollback()
        logger.error(f"inventory_import({product_id}) failed:\n" + traceback.format_exc())
        flash(request, "Có lỗi xảy ra khi nhập kho. Vui lòng thử lại!", "error")

    return RedirectResponse(url=f"/products/id/{product_id}", status_code=302)


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
    return RedirectResponse(url=f"/products/id/{product_id}", status_code=302)


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
