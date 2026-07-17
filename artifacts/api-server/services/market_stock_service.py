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
    effective_unit_cost = source_price * (1 + platform_fee_percent / 100)
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
import logging
from sqlalchemy.orm import Session

from models import Product, SourceType, AdminUser

logger = logging.getLogger(__name__)


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
    per-product slice — see module docstring).

    cost_price resolution order:
      1. product.source_price (kept in sync by api_service price sync loop)
      2. ProductSource.last_cost fallback — covers products whose source_price
         was 0 at attach time or before the price-sync fix was deployed

    Returns 0 only when:
      - wallet is empty
      - cost_price cannot be resolved (product was never synced)
    """
    admin = db.query(AdminUser).filter(AdminUser.id == product.tenant_id).first()
    if not admin:
        logger.warning(
            f"WALLET_DEBUG product_id={product.id} reason=admin_not_found "
            f"tenant_id={product.tenant_id} → virtual=0"
        )
        return 0

    balance = float(admin.market_wallet_balance or 0.0)

    # Primary: product.source_price (synced by price_sync_results each tick)
    cost_price = float(product.source_price or 0.0)

    # Fallback: ProductSource.last_cost — written by our fixed sync loop even
    # before source_price is backfilled, handles the "source_price=0 at
    # attach / stale from before price-sync fix" case.
    if cost_price <= 0:
        from models import ProductSource
        src = (
            db.query(ProductSource)
            .execution_options(skip_tenant_filter=True)
            .filter(
                ProductSource.product_id == product.id,
                ProductSource.is_active == True,
            )
            .order_by(ProductSource.priority)
            .first()
        )
        cost_price = float(src.last_cost or 0.0) if src else 0.0
        logger.info(
            f"WALLET_DEBUG product_id={product.id} source_price=0 "
            f"fallback_last_cost={cost_price} src_id={src.id if src else None}"
        )

    if cost_price <= 0:
        logger.warning(
            f"WALLET_DEBUG product_id={product.id} cost_price=0 "
            f"balance={balance} → virtual=0 reason=no_cost_price"
        )
        return 0

    from services.market_pricing import get_platform_fee_percent
    fee_pct = get_platform_fee_percent(db) or 0.0
    effective_unit_cost = cost_price * (1 + fee_pct / 100)

    if effective_unit_cost <= 0:
        logger.warning(
            f"WALLET_DEBUG product_id={product.id} effective_unit_cost<=0 "
            f"cost_price={cost_price} fee_pct={fee_pct} → virtual=0"
        )
        return 0

    virtual = max(0, int(balance // effective_unit_cost))
    logger.info(
        f"WALLET_DEBUG product_id={product.id} "
        f"source_price={product.source_price} cost_price_used={cost_price} "
        f"fee_pct={fee_pct} effective_unit_cost={effective_unit_cost:.2f} "
        f"balance={balance} max_by_wallet={virtual}"
    )
    return virtual


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
