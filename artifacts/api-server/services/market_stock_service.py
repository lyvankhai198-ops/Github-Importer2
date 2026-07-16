"""
Ví chợ ("market wallet") virtual stock — caps how many units of a
shared-catalog product can still be sold, based on the OWNER's
(admin's) prepaid market_wallet_balance.

Correct flow:
  1. Owner pre-funds their ví chợ (via bank transfer / MWDEP-* code).
  2. When a tenant's customer buys a shared-catalog product, the system
     automatically calls the supplier API using the owner's API key and
     the owner's pre-funded balance on the supplier side.
  3. After a successful purchase the owner's ví chợ is debited for the
     supplier cost — tracking how much of their pre-funded budget has
     been spent across all tenant sales.
  4. The tenant's customers pay via SePay to the owner's bank; the owner
     earns the margin (sale price − supplier cost).

The tenant has NO ví chợ in this model. "Virtual stock" = how many
more units the owner can still afford to buy from the supplier given
their current ví chợ balance and the product's source_price.

Formula (pooled — one owner wallet, all shared-catalog products):
    effective_unit_cost = product.source_price
    virtual_units = floor(owner.market_wallet_balance / effective_unit_cost)

The wallet is a shared pool: every shared-catalog product is checked
against the FULL current owner balance, not a pre-divided slice. A real
debit only happens once per fulfilled order (see
payment_service._debit_market_wallet_for_sale), so two products both
showing "in stock" from the same pool is correct — the balance is only
reduced at the moment one specific order is fulfilled.

Always computed live — never a stored counter — so stock display stays
accurate the instant the owner's balance changes (top-up, withdrawal, or
any fulfilled sale).

Owner's OWN products sold directly in the owner's bot are NEVER gated:
those use the owner's real supplier account balance, tracked externally,
not through this internal wallet mechanism.
"""
from sqlalchemy.orm import Session

from models import Product, SourceType, AdminUser


def _get_owner(db: Session) -> AdminUser | None:
    """The platform owner's AdminUser row — the one who pre-funds the ví chợ."""
    from tenancy import get_owner_tenant_id
    owner_id = get_owner_tenant_id()
    if not owner_id:
        return None
    return (
        db.query(AdminUser)
        .filter(AdminUser.id == owner_id)
        .first()
    )


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


def is_gated_by_market_wallet(db: Session, product: Product) -> bool:
    """True for source_type=api products that are sourced from the owner's
    shared catalog — their availability is capped by the OWNER's ví chợ
    balance, not the tenant's.  Owner's own direct products are never gated."""
    if product.source_type != SourceType.api:
        return False
    return _has_shared_source(db, product.id)


def get_virtual_stock(db: Session, product: Product) -> int:
    """
    How many more units the owner can still afford to buy from the
    supplier, given their current ví chợ balance and the product's
    source_price. Returns 0 if the owner's wallet is empty, the balance
    is unknown, or the product has no known cost price yet.
    """
    owner = _get_owner(db)
    if not owner:
        return 0
    cost_price = product.source_price or 0.0
    if cost_price <= 0:
        return 0
    balance = owner.market_wallet_balance or 0.0
    return max(0, int(balance // cost_price))
