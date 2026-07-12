from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from models import Product, ProductSource, ApiProduct


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

    for src in sources:
        ap = src.api_product
        if not ap:
            any_error = True
            continue
        if ap.last_sync_at is None:
            any_error = True
            continue
        # If last sync is too old, treat as error
        age = datetime.utcnow() - ap.last_sync_at
        if age > timedelta(minutes=10):
            any_error = True
            continue
        any_synced = True
        total_stock += max(0, src.last_stock or 0)

    if not any_synced:
        return {"stock": 0, "status": "unavailable"}
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

    def _sort_key(item):
        p = item["product"]
        status = item["status"]
        # Group: 0 = available/unavailable/accepting_orders (shown first), 1 = out_of_stock
        group = 1 if status == "out_of_stock" else 0
        pinned = 0 if getattr(p, "is_pinned", False) else 1   # pinned=True → 0 sorts first
        sold = -(p.sold_count or 0)                            # higher sold_count first
        name = p.name.lower()
        return (group, pinned, sold, name)

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
