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
    InventoryItem, InventoryStatus, Order, RestockSubscription, NotificationEvent,
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

    # Chợ-sourced products: hide the real supplier cost from anyone but the
    # owner, and surface the enforced minimum listing price instead — see
    # services/shared_catalog.is_shared_from_admin_product.
    from services import shared_catalog as _shared_catalog
    from services.market_pricing import default_sale_price as _default_sale_price
    _is_owner = request.state.is_owner
    for p in products:
        p.is_shared_from_admin = _shared_catalog.is_shared_from_admin_product(db, p.id)
        p.hide_source_price = p.is_shared_from_admin and not _is_owner
        p.min_allowed_sale_price = _default_sale_price(db, p.source_price or 0.0) if p.is_shared_from_admin else None

    # Distinct brand keys across all products (unfiltered) so the brand
    # dropdown always lists every brand, not just the ones on the current page.
    brand_keys = sorted({compute_brand_key(p.name) for p in db.query(Product.name).all() if compute_brand_key(p.name)})

    api_connections = db.query(ApiConnection).filter(ApiConnection.is_active == True).all()
    from models import EmojiIcon
    emoji_icons = db.query(EmojiIcon).filter(EmojiIcon.is_active == True).order_by(EmojiIcon.sort_order.asc(), EmojiIcon.id.asc()).all()
    flash_msg = request.session.pop("flash", None)
    response = templates.TemplateResponse(request, "products.html", {
        
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
    # Force a fresh server fetch on browser back/forward navigation (bfcache)
    # instead of showing a stale snapshot from before a create/edit/delete —
    # e.g. right after creating a product from a source, hitting "back" must
    # show the product that was just created, not the old cached list.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


@router.get("/products/market", response_class=HTMLResponse)
async def products_market(
    request: Request, db: Session = Depends(get_db),
    brand: str = "", state: str = "listed",
):
    """
    "Chợ" view — browses products pulled straight from the connected supplier
    API(s) (ApiProduct rows, kept fresh by the background sync scheduler and
    refreshed on-demand here too), not the local bot catalog. Each source
    item shows a one-click "Treo" (list) action that pulls it into the bot's
    product list, or, once listed, a "Lấy xuống treo" (Gỡ) action that
    unlists it again — the source item itself is never deleted, so it can be
    re-treo'd any time without re-fetching.
    """
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    from services.product_service import get_product_stock_status
    from services.api_service import sync_active_supplier_products
    from services import shared_catalog
    from tenancy import get_current_tenant, get_owner_tenant_id

    # Pull the freshest supplier data before rendering — the periodic
    # scheduler already keeps ApiProduct up to date in the background, but a
    # human opening "Chợ" should never see stale numbers (this is cheap: it's
    # a no-op if the last full sync ran within the last 30s).
    try:
        await sync_active_supplier_products(db)
    except Exception:
        logger.exception("[products_market] on-demand sync failed")

    from services.market_pricing import default_sale_price

    # Own connections (this tenant's, or — if this IS the owner — all of the
    # owner's connections). Tenant-scoped automatically via the do_orm_execute
    # filter, so no explicit tenant_id filter needed here.
    api_products = (
        db.query(ApiProduct)
        .join(ApiConnection, ApiProduct.api_connection_id == ApiConnection.id)
        .filter(ApiConnection.is_active == True)
        .all()
    )
    conn_name_by_ap_id: dict[int, str] = {ap.id: (ap.connection.name if ap.connection else "") for ap in api_products}

    # Non-owner tenants additionally see products from any connection the
    # owner has explicitly shared ("Chia sẻ catalog này cho khách thuê" in
    # Kết nối API) — straight in the Chợ, no separate page. "Treo" on one of
    # these creates a Product owned by THIS tenant, fulfilled later through
    # the owner's connection (see services/shared_catalog.py).
    current_tenant_id = get_current_tenant()
    is_owner = current_tenant_id == get_owner_tenant_id()
    shared_ap_ids: set[int] = set()
    if not is_owner:
        for conn in shared_catalog.get_shared_connections(db):
            shared_aps = shared_catalog.get_shared_products(db, conn.id)
            for ap in shared_aps:
                shared_ap_ids.add(ap.id)
                conn_name_by_ap_id[ap.id] = conn.name
            api_products.extend(shared_aps)

    # One query for every ProductSource linking these source items to a bot
    # product, so "is this currently treo'd?" never costs an extra query per row.
    # Auto tenant-filtered: for a shared ApiProduct this only ever returns
    # THIS tenant's own attachment, never another tenant's, which is exactly
    # "did *I* treo this" — see tenancy.py.
    ap_ids = [ap.id for ap in api_products]
    linked_by_ap: dict[int, Product] = {}
    if ap_ids:
        sources = (
            db.query(ProductSource)
            .filter(ProductSource.api_product_id.in_(ap_ids))
            .all()
        )
        for src in sources:
            if not src.product:
                continue
            current = linked_by_ap.get(src.api_product_id)
            # Prefer an active product over an inactive one if a source ever
            # ends up linked to more than one (rare — manual "Liên kết").
            if current is None or (src.product.is_active and not current.is_active):
                linked_by_ap[src.api_product_id] = src.product

    from services import shared_catalog as _shared_catalog

    for ap in api_products:
        ap.is_shared_from_admin = ap.id in shared_ap_ids
        # A non-owner tenant must never learn which real supplier connection
        # (e.g. "canboso") an admin-shared item comes from — that would leak
        # exactly the kind of sourcing info the price-secrecy rule protects.
        # Only the owner sees the real connection name; tenants just see "Chợ".
        if ap.is_shared_from_admin and not is_owner:
            ap.display_connection_name = "Chợ"
        else:
            ap.display_connection_name = conn_name_by_ap_id.get(ap.id, "")
        product = linked_by_ap.get(ap.id)
        ap.linked_product = product
        ap.is_listed = bool(product and product.is_active)
        if ap.is_listed:
            info = get_product_stock_status(product.id, db)
            ap.display_name = product.name
            ap.display_icon = product.telegram_icon or "📦"
            ap.display_image = product.image_path
            ap.display_price = product.sale_price or 0
            ap.display_stock = info["stock"]
            ap.display_unlimited = product.delivery_mode.value in ("manual_admin", "manual")
            ap.last_update = product.updated_at
            # Same hide-source-price / enforce-floor rule as the edit modal
            # elsewhere — the modal here is keyed off `product`, not `ap`.
            product.is_shared_from_admin = _shared_catalog.is_shared_from_admin_product(db, product.id)
            product.hide_source_price = product.is_shared_from_admin and not is_owner
            product.min_allowed_sale_price = default_sale_price(db, product.source_price or 0.0) if product.is_shared_from_admin else None
        else:
            ap.display_name = ap.external_name or ap.external_product_id
            ap.display_icon = "📦"
            ap.display_image = ap.external_image_url
            # Show the price already marked up by the configured default —
            # the "Nguồn hàng" list and the "Treo" form must always agree on
            # what the tenant will actually charge, not the raw supplier
            # cost, or tenants routinely forget to add margin themselves.
            ap.display_source_price = ap.external_price or 0
            ap.display_price = default_sale_price(db, ap.external_price or 0)
            ap.display_stock = ap.external_stock or 0
            ap.display_unlimited = False
            ap.last_update = ap.last_sync_at

    # Category chips are a fixed, curated list (MARKET_CATEGORIES) rather than
    # one chip per distinct first word — that fragmented into dozens of noisy
    # single-item chips (Admin, Api, Cdk, Fam...) which was hard to scan.
    # Chips always reflect the tab currently being viewed (listed vs
    # unlisted) and are always shown in the fixed order, even at zero count,
    # so the menu never reflows as items move between categories.
    from services.normalize import MARKET_CATEGORIES, classify_market_category
    is_listed_state = state != "unlisted"
    state_products = [ap for ap in api_products if ap.is_listed == is_listed_state]

    for ap in state_products:
        ap.category_key = classify_market_category(ap.display_name)

    category_counts: dict[str, int] = {}
    for ap in state_products:
        category_counts[ap.category_key] = category_counts.get(ap.category_key, 0) + 1
    brands = [
        {"key": cat["key"], "label": cat["label"], "icon": cat["icon"], "count": category_counts.get(cat["key"], 0)}
        for cat in MARKET_CATEGORIES
    ]
    total_listed = len(state_products)
    # Whether the *other* tab has anything — the empty-state message must not
    # say "no products at all" when e.g. "Đang treo" is empty simply because
    # nothing has been treo'd yet while "Chưa treo" has plenty to pick from.
    other_tab_has_products = any(ap.is_listed != is_listed_state for ap in api_products)

    products = state_products
    if brand:
        products = [ap for ap in products if ap.category_key == brand]

    # Price ascending — "Giá tối thiểu treo" first, matching the canboso table.
    products.sort(key=lambda ap: (ap.display_price or 0, ap.display_name or ""))

    api_connections = db.query(ApiConnection).filter(ApiConnection.is_active == True).all()
    from models import EmojiIcon
    emoji_icons = db.query(EmojiIcon).filter(EmojiIcon.is_active == True).order_by(EmojiIcon.sort_order.asc(), EmojiIcon.id.asc()).all()
    flash_msg = request.session.pop("flash", None)
    response = templates.TemplateResponse(request, "market.html", {
        "products": products,
        "api_connections": api_connections,
        "emoji_icons": emoji_icons,
        "brands": brands,
        "total_listed": total_listed,
        "other_tab_has_products": other_tab_has_products,
        "brand_filter": brand,
        "state_filter": "unlisted" if not is_listed_state else "listed",
        "flash": flash_msg,
    })
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


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

    # Chợ-sourced products: the tenant must never move the real supplier
    # cost, and must never list below cost+markup — both the source_price
    # they submitted and any sale_price under the floor are rejected here,
    # independent of whatever the (possibly tampered) form actually sent.
    from services import shared_catalog
    from services.market_pricing import default_sale_price
    is_owner = request.state.is_owner
    is_shared = shared_catalog.is_shared_from_admin_product(db, product.id)
    if is_shared and not is_owner:
        source_price = product.source_price or 0.0  # ignore submitted value entirely
        floor = default_sale_price(db, source_price)
        if sale_price < floor:
            flash(request, f"Giá bán tối thiểu cho sản phẩm này là {floor:,.0f}đ".replace(",", "."), "error")
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

    try:
        # Xoá restock subscriptions trước (NOT NULL FK, không có cascade từ Product)
        db.query(RestockSubscription).filter(
            RestockSubscription.product_id == product_id
        ).delete(synchronize_session=False)
        # Xoá notification events liên quan (nullable FK, xoá để không rác)
        db.query(NotificationEvent).filter(
            NotificationEvent.product_id == product_id
        ).delete(synchronize_session=False)
        # Product.orders có passive_deletes=True nên SQLAlchemy không cố NULL
        # hoá orders.product_id — SQLite không enforce FK nên order record vẫn
        # còn trong DB, hiển thị "(Đã xoá)" ở trang đơn hàng.
        # Product.sources + inventory_items có cascade="all, delete-orphan" →
        # tự xoá theo.
        db.delete(product)
        db.commit()
        flash(request, "Sản phẩm đã được xóa! Đơn hàng liên quan vẫn được lưu trong mục Đơn hàng.")
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
    response = templates.TemplateResponse(request, "product_sources.html", {
        
        "api_products": api_products,
        "connections": connections,
        "all_products": all_products,
        "selected_conn": conn_id,
        "page": page,
        "total": total,
        "per_page": per_page,
        "flash": flash_msg,
    })
    # Same bfcache-busting as /products — after creating a product from a
    # source, "back" must show it in the list immediately, not a stale snapshot.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


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
        # Not one of this tenant's own source items — check whether it's a
        # product from an owner-shared connection instead (Chợ "Treo" on a
        # shared-catalog item never creates a tenant's own ApiConnection).
        from services import shared_catalog
        from tenancy import get_current_tenant
        shared_ap = shared_catalog.get_shared_api_product(db, api_product_id)
        if not shared_ap:
            flash(request, "Không tìm thấy sản phẩm nguồn!", "error")
            return RedirectResponse(url="/products/market", status_code=302)
        from services.market_pricing import default_sale_price
        floor = default_sale_price(db, shared_ap.external_price or 0.0)
        final_price = sale_price if sale_price > 0 else floor
        # Chợ-sourced item: never let a tenant list below cost+markup — the
        # real supplier cost must stay hidden, so the message quotes only
        # the (already-visible) minimum listing price, never the source price.
        if not request.state.is_owner and final_price < floor:
            flash(request, f"Giá treo tối thiểu cho sản phẩm này là {floor:,.0f}đ".replace(",", "."), "error")
            return RedirectResponse(url="/products/market", status_code=302)
        try:
            new_product = shared_catalog.attach_shared_product(db, get_current_tenant(), api_product_id, final_price)
            flash(request, "Sản phẩm đã được treo lên Chợ!")
            return RedirectResponse(url=f"/products/id/{new_product.id}", status_code=302)
        except ValueError as e:
            flash(request, str(e), "error")
        return RedirectResponse(url="/products/market", status_code=302)
    code = f"API-{ap.api_connection_id}-{ap.external_product_id}"
    existing = db.query(Product).filter(Product.product_code == code).first()
    if existing:
        if existing.is_active:
            flash(request, "Sản phẩm đã tồn tại!", "error")
            return RedirectResponse(url="/products/api-sources", status_code=302)
        # Previously treo'd then gỡ (unlisted) — re-treo by reactivating the
        # same product instead of blocking, so a source item can be listed/
        # unlisted repeatedly without ever losing its order history.
        existing.is_active = True
        source = db.query(ProductSource).filter(
            ProductSource.product_id == existing.id,
            ProductSource.api_product_id == ap.id,
        ).first()
        if source:
            source.is_active = True
        else:
            source = ProductSource(
                product_id=existing.id, api_product_id=ap.id, priority=1,
                is_active=True, last_cost=ap.external_price, last_stock=int(ap.external_stock or 0),
            )
            db.add(source)
        db.commit()
        flash(request, "Sản phẩm đã được treo lại!")
        return RedirectResponse(url=f"/products/id/{existing.id}", status_code=302)

    # Use sale_price if admin set it; otherwise default to source price + markup
    from services.market_pricing import default_sale_price
    final_price = sale_price if sale_price > 0 else default_sale_price(db, ap.external_price or 0.0)

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
        last_stock=int(ap.external_stock or 0),
    )
    db.add(source)
    db.commit()
    flash(request, "Sản phẩm đã được tạo từ nguồn API!")
    if product.is_active:
        from services.broadcast_service import notify_new_product_broadcast
        await notify_new_product_broadcast(product)
    return RedirectResponse(url=f"/products/id/{product.id}", status_code=302)


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
        last_stock=int(ap.external_stock or 0),
    )
    db.add(source)
    db.commit()
    flash(request, "Liên kết nguồn thành công!")
    return RedirectResponse(url=f"/products/id/{product_id}", status_code=302)


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

    # Same hide-source-price / enforce-floor rule as the list views — see
    # services/shared_catalog.is_shared_from_admin_product.
    from services import shared_catalog as _shared_catalog
    from services.market_pricing import default_sale_price as _default_sale_price
    product.is_shared_from_admin = _shared_catalog.is_shared_from_admin_product(db, product.id)
    product.hide_source_price = product.is_shared_from_admin and not request.state.is_owner
    product.min_allowed_sale_price = _default_sale_price(db, product.source_price or 0.0) if product.is_shared_from_admin else None

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
