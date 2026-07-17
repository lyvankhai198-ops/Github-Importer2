import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from models import Product, ProductSource, ApiProduct

logger = logging.getLogger(__name__)


def get_product_stock_status(product_id: int, db: Session) -> dict:
    """
    Returns {"stock": N, "status": "in_stock"|"out_of_stock"|"unavailable"} for a product.
    - api_auto: aggregated across all active sources with recent sync.
    - manual_stock: computed live from inventory_items (never a stored counter).
    - manual_admin (and legacy "manual"): unlimited / always accepting orders.
    """
    from models import DeliveryMode
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return {"stock": 0, "status": "unavailable"}

    if product.delivery_mode == DeliveryMode.manual_stock:
        from services.inventory_service import get_available_count
        stock = get_available_count(db, product_id)
        if stock <= 0:
            return {"stock": 0, "status": "out_of_stock"}
        return {"stock": stock, "status": "in_stock"}

    if product.delivery_mode != DeliveryMode.api_auto:
        # manual_admin / legacy manual — unlimited, always "accepting orders"
        return {"stock": 999, "status": "in_stock"}

    sources = db.query(ProductSource).filter(
        ProductSource.product_id == product_id,
        ProductSource.is_active == True,
    ).all()

    if not sources:
        return {"stock": 0, "status": "unavailable"}

    total_stock = 0
    any_synced = False
    any_error = False

    from services.shared_catalog import resolve_api_product
    for src in sources:
        ap = resolve_api_product(db, src)
        if not ap:
            any_error = True
            logger.warning(
                f"STOCK_DEBUG product_id={product_id} src_id={src.id} "
                f"api_product_id={src.api_product_id} reason=api_product_not_found"
            )
            continue
        if ap.last_sync_at is None:
            any_error = True
            logger.warning(
                f"STOCK_DEBUG product_id={product_id} src_id={src.id} "
                f"api_product_id={ap.id} reason=never_synced"
            )
            continue
        # Log staleness but do NOT gate on it — use last known stock even
        # if the sync is old. Treating a stale sync as "unavailable" causes
        # false "hết hàng" whenever background sync falls slightly behind.
        age = datetime.utcnow() - ap.last_sync_at
        if age > timedelta(minutes=10):
            logger.warning(
                f"STOCK_DEBUG product_id={product_id} src_id={src.id} "
                f"api_product_id={ap.id} reason=stale(warn_only) age_seconds={age.total_seconds():.0f} "
                f"last_sync_at={ap.last_sync_at.isoformat()} external_stock={ap.external_stock}"
            )
        any_synced = True
        # Prefer last_stock (written by sync loop) but fall back to
        # ap.external_stock directly — this covers products that were
        # treo'd after the last sync so last_stock hasn't been written yet,
        # or where the sync loop previously skipped the update due to the
        # shared-catalog tenant-filter bug (now fixed in api_service.py).
        stock_val = src.last_stock if src.last_stock is not None else int(ap.external_stock or 0)
        logger.info(
            f"STOCK_DEBUG product_id={product_id} src_id={src.id} api_product_id={ap.id} "
            f"is_active={src.is_active} src_last_stock={src.last_stock} "
            f"ap_external_stock={ap.external_stock} stock_used={stock_val} age_seconds={age.total_seconds():.0f}"
        )
        total_stock += max(0, stock_val)

    if not any_synced:
        logger.warning(f"STOCK_DEBUG product_id={product_id} result=unavailable any_error={any_error} n_sources={len(sources)}")
        return {"stock": 0, "status": "unavailable"}

    # Ví chợ gating — a non-owner tenant's real supplier availability is
    # further capped by how many units their prepaid market wallet can still
    # fund (see services/market_stock_service.py). The owner's own listings
    # are never capped.
    from services.market_stock_service import is_gated_by_market_wallet, get_virtual_stock
    pre_wallet_stock = total_stock
    gated = is_gated_by_market_wallet(db, product)
    if gated:
        virtual = get_virtual_stock(db, product)
        total_stock = min(total_stock, virtual)
        logger.info(
            f"STOCK_DEBUG product_id={product_id} wallet_gated=True "
            f"source_price={product.source_price} pre_wallet_stock={pre_wallet_stock} "
            f"virtual_stock={virtual} final={total_stock}"
        )
    else:
        logger.info(
            f"STOCK_DEBUG product_id={product_id} wallet_gated=False "
            f"(owner product or non-api) total_stock={total_stock}"
        )

    if total_stock <= 0:
        return {"stock": 0, "status": "out_of_stock"}
    return {"stock": total_stock, "status": "in_stock"}


def get_active_products_for_bot(db: Session, show_out_of_stock: bool = True) -> list:
    """
    Returns sorted list of {product, stock, status} for the bot product list.
    Sort order:
      1. in_stock / unavailable (can browse) before out_of_stock
      2. Within each group: is_pinned DESC, sold_count DESC, name ASC
    If show_out_of_stock is False, out_of_stock products are excluded.
    """
    from models import DeliveryMode
    products = db.query(Product).filter(Product.is_active == True).all()
    result = []
    for p in products:
        info = get_product_stock_status(p.id, db)
        status = info["status"]
        if not show_out_of_stock and status == "out_of_stock":
            continue
        # manual_admin (and legacy "manual"): no local inventory tracked —
        # always shown as "accepting orders" rather than a stock count.
        if p.delivery_mode != DeliveryMode.manual_stock and p.delivery_mode != DeliveryMode.api_auto:
            status = "accepting_orders"
        result.append({
            "product": p,
            "stock": info["stock"],
            "status": status,
        })

    from services.normalize import compute_brand_key

    def _sort_key(item):
        p = item["product"]
        status = item["status"]
        # Group: 0 = available/unavailable/accepting_orders (shown first), 1 = out_of_stock
        group = 1 if status == "out_of_stock" else 0
        # Within a group: brand_key ASC, then product name ASC, then duration
        # ASC when detectable — keeps every variant of the same brand
        # (e.g. all "Grok ..." products) contiguous, never interleaved with
        # another brand.
        brand_key = compute_brand_key(p.name)
        name = (p.name or "").lower()
        duration = (p.duration or "").lower()
        return (group, brand_key, name, duration)

    result.sort(key=_sort_key)
    return result


def get_product_detail(db: Session, product_id: int):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return None
    sources = db.query(ProductSource).filter(
        ProductSource.product_id == product_id,
        ProductSource.is_active == True
    ).order_by(ProductSource.priority).all()
    return {"product": product, "sources": sources}


def get_product_availability(db: Session, product_id: int) -> bool:
    info = get_product_stock_status(product_id, db)
    return info["status"] == "in_stock"


def get_best_source(db: Session, product_id: int):
    sources = db.query(ProductSource).filter(
        ProductSource.product_id == product_id,
        ProductSource.is_active == True
    ).order_by(ProductSource.priority).all()

    # Ví chợ gating — never hand back a source (and therefore never let the
    # order flow call the real supplier API) once a non-owner tenant's
    # prepaid wallet budget for this product has run out, even if the real
    # supplier still has stock. See services/market_stock_service.py.
    product = db.query(Product).filter(Product.id == product_id).first()
    if product:
        from services.market_stock_service import is_gated_by_market_wallet, get_virtual_stock
        if is_gated_by_market_wallet(db, product) and get_virtual_stock(db, product) <= 0:
            return None

    for src in sources:
        if src.last_stock and src.last_stock > 0:
            return src
    return None


def get_product_sources_count(db: Session, product_id: int) -> int:
    """Return number of active sources for a product."""
    return db.query(ProductSource).filter(
        ProductSource.product_id == product_id,
        ProductSource.is_active == True
    ).count()
