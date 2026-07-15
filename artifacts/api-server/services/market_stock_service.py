"""
Ví chợ ("market wallet") virtual stock — caps how many units of a
chợ-sourced (source_type=api) product a non-owner tenant's bot may still
sell, based on their prepaid market_wallet_balance.

Formula — pooled wallet, NOT split evenly across attached products:
    effective_unit_cost = product.source_price * (1 + platform_fee_percent / 100)
    virtual_units = floor(tenant.market_wallet_balance / effective_unit_cost)

The platform fee is included in the per-unit cost used here because
debit_for_sale debits cost + fee together as one atomic transaction at
sale time (see market_wallet_service.debit_for_sale) — if the displayed
count only accounted for source_price and ignored the fee, the last
"available" unit shown to a tenant could fail with InsufficientBalanceError
at the moment of actual purchase, since the real debit is larger than the
raw cost used to compute the displayed number.

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
    from services.market_pricing import get_platform_fee_percent
    fee_pct = get_platform_fee_percent(db) or 0.0
    effective_unit_cost = cost_price * (1 + fee_pct / 100)
    if effective_unit_cost <= 0:
        return 0
    balance = admin.market_wallet_balance or 0.0
    return max(0, int(balance // effective_unit_cost))
