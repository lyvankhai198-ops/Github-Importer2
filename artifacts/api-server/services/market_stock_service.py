"""
Ví chợ ("market wallet") virtual stock — caps how many units of a
chợ-sourced (source_type=api) product a non-owner tenant's bot may still
sell, based on their prepaid market_wallet_balance.

Formula (spec section "Số lượng hàng theo ví chợ"):
    budget_per_product = tenant.market_wallet_balance / (số sản phẩm nguồn
                          chợ đang gắn — active source_type=api products for
                          this tenant)
    virtual_units = floor(budget_per_product / product.source_price)

Always computed live — never a stored counter — so it stays correct the
instant the wallet balance changes (sale, top-up, withdrawal) or a product
is attached/detached, with no separate recompute step to remember to run.

The owner's own products are NEVER gated by this: the owner IS the real
supplier relationship, so their listings keep using the existing
ProductSource.last_stock-based availability untouched.
"""
from sqlalchemy.orm import Session

from models import Product, SourceType, AdminUser


def is_gated_by_market_wallet(db: Session, product: Product) -> bool:
    """True only for source_type=api products belonging to a non-owner tenant."""
    if product.source_type != SourceType.api:
        return False
    admin = db.query(AdminUser).filter(AdminUser.id == product.tenant_id).first()
    return bool(admin and not admin.is_owner)


def get_attached_market_product_count(db: Session, tenant_id: int) -> int:
    """Number of active chợ-sourced (source_type=api) products this tenant
    currently has attached/listed — the wallet balance is split evenly
    across all of them."""
    return (
        db.query(Product)
        .filter(
            Product.tenant_id == tenant_id,
            Product.source_type == SourceType.api,
            Product.is_active == True,
        )
        .count()
    )


def get_virtual_stock(db: Session, product: Product) -> int:
    """
    Wallet-funded unit budget for one chợ-sourced product, floored to a
    whole unit. Returns 0 (never negative, never unlimited) when the wallet
    is empty, nothing is attached yet, or the product has no known cost
    price yet (source_price not backfilled) — a missing cost price must
    never be silently treated as "free"/unlimited stock.
    """
    admin = db.query(AdminUser).filter(AdminUser.id == product.tenant_id).first()
    if not admin:
        return 0
    cost_price = product.source_price or 0.0
    if cost_price <= 0:
        return 0
    n_attached = get_attached_market_product_count(db, product.tenant_id)
    if n_attached <= 0:
        return 0
    budget_per_product = (admin.market_wallet_balance or 0.0) / n_attached
    return max(0, int(budget_per_product // cost_price))
