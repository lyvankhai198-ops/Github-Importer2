"""
Ví chợ ("market wallet") virtual stock — caps how many units of a
chợ-sourced (source_type=api) product a non-owner tenant's bot may still
sell, based on their prepaid market_wallet_balance.

Formula — pooled wallet, NOT split evenly across attached products:
    virtual_units = floor(tenant.market_wallet_balance / product.source_price)

The wallet is one shared pool: every attached product is checked against
the FULL current balance, not a pre-divided slice of it. This matches how
the balance is actually spent — a real debit only happens once per
fulfilled order (see debit_for_sale), so pre-partitioning the balance across
every listed SKU produced false "Hết hàng" whenever a tenant listed more
than one product, even though the pooled balance could cover a sale of any
one of them. Two products both showing "in stock" from the same pool is
expected — the real balance is only debited (and can only go to 0) at the
moment a specific order is fulfilled, at which point every other product's
virtual stock recomputes live off the now-lower balance.

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


def get_virtual_stock(db: Session, product: Product) -> int:
    """
    Wallet-funded unit budget for one chợ-sourced product, floored to a
    whole unit, checked against the FULL pooled wallet balance (not a
    per-product slice — see module docstring). Returns 0 (never negative,
    never unlimited) when the wallet is empty or the product has no known
    cost price yet (source_price not backfilled) — a missing cost price
    must never be silently treated as "free"/unlimited stock.
    """
    admin = db.query(AdminUser).filter(AdminUser.id == product.tenant_id).first()
    if not admin:
        return 0
    cost_price = product.source_price or 0.0
    if cost_price <= 0:
        return 0
    balance = admin.market_wallet_balance or 0.0
    return max(0, int(balance // cost_price))
