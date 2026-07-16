"""
Ví chợ ("market wallet") virtual stock — caps how many units of a
chợ-sourced (source_type=api) product a non-owner tenant can still sell,
based on their own prepaid market_wallet_balance.

Correct flow:
  1. Tenant pre-funds their ví chợ on the web admin panel.
  2. This balance determines virtual stock: floor(balance / cost_per_unit),
     where cost_per_unit includes the platform fee percentage.
  3. When a tenant's bot customer pays successfully, the system auto-calls
     the admin's supplier API (using the admin's API key + supplier balance)
     and delivers the product.
  4. After successful fulfillment the TENANT's ví chợ is debited for
     (supplier cost + platform fee), tracked via market_wallet_service.

The OWNER's own products are never gated by this: the owner IS the real
supplier relationship, so their listings use ProductSource.last_stock-based
availability untouched.

Formula — pooled wallet, NOT split evenly across attached products:
    effective_unit_cost = product.source_price * (1 + platform_fee_percent / 100)
    virtual_units = floor(tenant.market_wallet_balance / effective_unit_cost)

The platform fee is included in the per-unit cost here because debit_for_sale
debits cost + fee together as one atomic transaction at sale time — if the
displayed count only accounted for source_price and ignored the fee, the last
"available" unit shown could fail with InsufficientBalanceError at purchase
since the real debit is larger.

The wallet is a shared pool: every attached product is checked against the
FULL current tenant balance, never a pre-divided slice. A real debit only
happens once per fulfilled order, so two products both showing "in stock"
from the same pool is expected — the balance only decreases when one specific
order is fulfilled, at which point all other products' virtual stock recomputes
live off the now-lower balance.

Always computed live (never a stored counter) so it stays correct the instant
the balance changes (top-up, withdrawal, or any fulfilled sale) with no
separate recompute step needed.
"""
from sqlalchemy.orm import Session

from models import Product, SourceType, AdminUser


def is_gated_by_market_wallet(db: Session, product: Product) -> bool:
    """True only for source_type=api products belonging to a non-owner tenant.
    The owner's own products are never gated — they use real supplier stock."""
    if product.source_type != SourceType.api:
        return False
    admin = db.query(AdminUser).filter(AdminUser.id == product.tenant_id).first()
    return bool(admin and not admin.is_owner)


def get_virtual_stock(db: Session, product: Product) -> int:
    """
    Wallet-funded unit budget for one chợ-sourced product, floored to a
    whole unit, checked against the FULL pooled tenant wallet balance (not a
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


def _has_shared_source(db: Session, product_id: int) -> bool:
    """True when this product has at least one active shared-from-admin source."""
    from models import ProductSource
    return bool(
        db.query(ProductSource)
        .execution_options(skip_tenant_filter=True)
        .filter(
            ProductSource.product_id == product_id,
            ProductSource.shared_from_admin == True,
            ProductSource.is_active == True,
        )
        .first()
    )
