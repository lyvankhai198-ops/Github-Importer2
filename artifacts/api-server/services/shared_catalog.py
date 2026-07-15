"""
"Chợ dùng chung" — lets a non-owner tenant list ("treo chợ") products that
are actually sourced from an API connection the OWNER already configured
(CanBoSo, Zampto, etc.), instead of requiring every tenant to create their
own ApiConnection + API key for the same supplier.

How it fits the existing schema (no bridge table needed):
  - ApiConnection gains `is_shared_with_tenants` — an owner-only toggle that
    exposes that connection's synced ApiProduct catalog to tenants.
  - ProductSource gains `shared_from_admin` — set True when a tenant attaches
    a product from that shared catalog. The ProductSource row still belongs
    to the tenant (tenant_id = tenant), but its `api_product_id` points at
    an ApiProduct owned by the OWNER's tenant, not the tenant's own.
  - Order fulfillment for a `shared_from_admin` source therefore calls the
    supplier using the OWNER's ApiConnection/API key — the tenant never
    sees or touches real supplier credentials.

Why every resolver below bypasses the tenant filter unconditionally: the
caller already holds a legitimate, tenant-scoped `ProductSource`/`ApiProduct`
row obtained through a normal (filtered) query — these helpers only follow
an already-known foreign key from that row to its target row. That target
may legitimately belong to a different tenant (the owner) for a shared
listing, or the same tenant for a normal listing; either way nothing is
exposed that the caller didn't already have a valid pointer to.
"""
import logging
from datetime import datetime
from sqlalchemy.orm import Session

from models import ApiConnection, ApiProduct, Product, ProductSource, SourceType, DeliveryMode
from tenancy import get_owner_tenant_id

logger = logging.getLogger(__name__)


def resolve_api_product(db: Session, source: ProductSource) -> ApiProduct | None:
    """The ApiProduct a ProductSource points to, regardless of which tenant
    owns it. Use this instead of `source.api_product` anywhere fulfillment
    or sync code needs the linked supplier item."""
    if source is None or not source.api_product_id:
        return None
    return (
        db.query(ApiProduct)
        .execution_options(skip_tenant_filter=True)
        .filter(ApiProduct.id == source.api_product_id)
        .first()
    )


def resolve_api_connection(db: Session, api_product: ApiProduct) -> ApiConnection | None:
    """The ApiConnection an ApiProduct belongs to, regardless of which tenant
    owns it. Use this instead of `api_product.connection` for fulfillment."""
    if api_product is None or not api_product.api_connection_id:
        return None
    return (
        db.query(ApiConnection)
        .execution_options(skip_tenant_filter=True)
        .filter(ApiConnection.id == api_product.api_connection_id)
        .first()
    )


def resolve_product(db: Session, source: ProductSource) -> Product | None:
    """The Product a ProductSource belongs to, regardless of which tenant
    owns it. Needed during the owner's sync tick, which runs scoped to the
    owner tenant but must still update products that shared-attaching
    tenants own."""
    if source is None or not source.product_id:
        return None
    return (
        db.query(Product)
        .execution_options(skip_tenant_filter=True)
        .filter(Product.id == source.product_id)
        .first()
    )


def get_shared_connections(db: Session) -> list[ApiConnection]:
    """Active, owner-shared API connections a tenant may browse and attach
    products from."""
    owner_id = get_owner_tenant_id()
    return (
        db.query(ApiConnection)
        .execution_options(skip_tenant_filter=True)
        .filter(
            ApiConnection.tenant_id == owner_id,
            ApiConnection.is_active == True,
            ApiConnection.is_shared_with_tenants == True,
        )
        .order_by(ApiConnection.name.asc())
        .all()
    )


def get_shared_connection(db: Session, connection_id: int) -> ApiConnection | None:
    owner_id = get_owner_tenant_id()
    return (
        db.query(ApiConnection)
        .execution_options(skip_tenant_filter=True)
        .filter(
            ApiConnection.id == connection_id,
            ApiConnection.tenant_id == owner_id,
            ApiConnection.is_shared_with_tenants == True,
        )
        .first()
    )


def get_shared_products(db: Session, connection_id: int) -> list[ApiProduct]:
    """Catalog of a shared connection's synced items, for a tenant to browse.
    Callers must render only display fields (name/price/stock) — never the
    connection's API key/base URL."""
    return (
        db.query(ApiProduct)
        .execution_options(skip_tenant_filter=True)
        .filter(ApiProduct.api_connection_id == connection_id)
        .order_by(ApiProduct.external_name.asc())
        .all()
    )


def get_shared_api_product(db: Session, api_product_id: int) -> ApiProduct | None:
    owner_id = get_owner_tenant_id()
    ap = (
        db.query(ApiProduct)
        .execution_options(skip_tenant_filter=True)
        .filter(ApiProduct.id == api_product_id, ApiProduct.tenant_id == owner_id)
        .first()
    )
    if not ap:
        return None
    conn = resolve_api_connection(db, ap)
    if not conn or not conn.is_shared_with_tenants:
        return None
    return ap


def get_attached_shared_sources(db: Session, tenant_id: int) -> list[ProductSource]:
    """This tenant's own shared-catalog attachments (for a "my shared
    listings" management view)."""
    return (
        db.query(ProductSource)
        .filter(ProductSource.tenant_id == tenant_id, ProductSource.shared_from_admin == True)
        .all()
    )


def get_shared_sources_for_api_product(db: Session, api_product_id: int) -> list[ProductSource]:
    """Every tenant's ProductSource attached to a given shared ApiProduct —
    used to propagate a sync tick's price/stock update across tenants."""
    return (
        db.query(ProductSource)
        .execution_options(skip_tenant_filter=True)
        .filter(
            ProductSource.api_product_id == api_product_id,
            ProductSource.shared_from_admin == True,
        )
        .all()
    )


def attach_shared_product(
    db: Session,
    tenant_id: int,
    api_product_id: int,
    sale_price: float,
    product_code: str | None = None,
) -> Product:
    """Tenant "treo chợ" flow: create a Product (owned by the tenant) plus a
    ProductSource that points at the owner's ApiProduct, without the tenant
    ever creating their own ApiConnection. Raises ValueError if the
    ApiProduct isn't actually shared or was already attached by this tenant.
    """
    ap = get_shared_api_product(db, api_product_id)
    if not ap:
        raise ValueError("Sản phẩm này không có trong kho hàng chung của admin")

    existing_source = (
        db.query(ProductSource)
        .filter(
            ProductSource.tenant_id == tenant_id,
            ProductSource.api_product_id == api_product_id,
            ProductSource.shared_from_admin == True,
        )
        .first()
    )
    if existing_source:
        existing_product = db.query(Product).filter(Product.id == existing_source.product_id).first()
        if existing_product and existing_product.is_active:
            raise ValueError("Bạn đã treo sản phẩm này lên Chợ rồi")
        # Previously treo'd then gỡ (unlisted) — re-treo by reactivating
        # instead of blocking, so it can be listed/unlisted repeatedly
        # without losing order history.
        if existing_product:
            existing_product.is_active = True
            existing_product.sale_price = float(sale_price or existing_product.sale_price or 0.0)
            existing_source.is_active = True
            db.commit()
            db.refresh(existing_product)
            return existing_product

    now = datetime.utcnow()
    code = (product_code or ap.external_product_id or "").strip() or f"shared-{ap.id}"
    # Uniqueness is (tenant_id, product_code) — this tenant may already have
    # an unrelated product using the same code (e.g. re-using the supplier's
    # raw external id), so fall back to a guaranteed-unique suffix rather
    # than failing the attach.
    if db.query(Product).filter(Product.tenant_id == tenant_id, Product.product_code == code).first():
        code = f"{code}-shared{ap.id}"
    product = Product(
        tenant_id=tenant_id,
        product_code=code,
        name=ap.external_name or code,
        description=ap.external_description or "",
        image_path=ap.external_image_url or None,
        sale_price=float(sale_price or 0.0),
        source_price=ap.external_price or 0.0,
        price_margin=float(sale_price or 0.0) - (ap.external_price or 0.0),
        warranty=ap.external_warranty,
        duration=ap.external_duration,
        source_type=SourceType.api,
        delivery_mode=DeliveryMode.api_auto,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(product)
    db.flush()  # need product.id before creating the ProductSource

    source = ProductSource(
        tenant_id=tenant_id,
        product_id=product.id,
        api_product_id=ap.id,
        priority=1,
        is_active=True,
        shared_from_admin=True,
        last_cost=ap.external_price,
        last_stock=ap.external_stock,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    db.commit()
    db.refresh(product)
    logger.info(f"SHARED_CATALOG_ATTACHED: tenant_id={tenant_id} api_product_id={api_product_id} product_id={product.id}")
    return product


def detach_shared_product(db: Session, tenant_id: int, product_id: int) -> bool:
    """Un-list a shared-catalog product from this tenant's Chợ (deactivate,
    keep order history intact)."""
    product = (
        db.query(Product)
        .filter(Product.id == product_id, Product.tenant_id == tenant_id)
        .first()
    )
    if not product:
        return False
    is_shared = (
        db.query(ProductSource)
        .filter(ProductSource.product_id == product_id, ProductSource.shared_from_admin == True)
        .first()
    )
    if not is_shared:
        return False
    product.is_active = False
    db.commit()
    return True
