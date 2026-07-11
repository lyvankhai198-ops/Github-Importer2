from sqlalchemy.orm import Session
from models import Product, ProductSource, ApiProduct


def get_active_products_for_bot(db: Session) -> list:
    products = db.query(Product).filter(Product.is_active == True).all()
    result = []
    for p in products:
        stock = 0
        for src in p.sources:
            if src.is_active and src.last_stock:
                stock += src.last_stock
        result.append({"product": p, "stock": stock})
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
    sources = db.query(ProductSource).filter(
        ProductSource.product_id == product_id,
        ProductSource.is_active == True
    ).all()
    for src in sources:
        if src.last_stock and src.last_stock > 0:
            return True
    return False


def get_best_source(db: Session, product_id: int):
    sources = db.query(ProductSource).filter(
        ProductSource.product_id == product_id,
        ProductSource.is_active == True
    ).order_by(ProductSource.priority).all()
    for src in sources:
        if src.last_stock and src.last_stock > 0:
            return src
    return None
