"""
Tests for auto price-adjustment ("giữ nguyên phần chênh lệch"): a Product's
price_margin (sale_price - source_price) is preserved when the supplier
source price changes, subject to auto_adjust_price on/off and an optional
per-product approval threshold for abnormal jumps.
"""
import pytest

from models import Product, DeliveryMode, SourceType, ProductPriceHistory
from services.price_sync_service import (
    handle_source_price_change, apply_admin_price_edit, approve_pending_price,
)


def make_product(db_session, **kwargs):
    p = Product(
        name=kwargs.pop("name", "TestPriceAdjust"),
        product_code=kwargs.pop("product_code", "TPA-1"),
        sale_price=kwargs.pop("sale_price", 130000.0),
        source_price=kwargs.pop("source_price", 100000.0),
        auto_adjust_price=kwargs.pop("auto_adjust_price", True),
        delivery_mode=DeliveryMode.manual_admin,
        source_type=SourceType.manual,
        is_active=True,
        min_quantity=1,
        **kwargs,
    )
    p.price_margin = p.sale_price - p.source_price
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.mark.asyncio
async def test_price_increase_auto_adjusts_sale_price(db_session):
    p = make_product(db_session, sale_price=130000.0, source_price=100000.0, auto_adjust_price=True)
    result = await handle_source_price_change(db_session, p, 120000.0)
    db_session.refresh(p)
    assert result["action"] == "applied"
    assert p.source_price == 120000.0
    assert p.sale_price == 150000.0  # margin (30k) preserved
    assert p.price_margin == 30000.0


@pytest.mark.asyncio
async def test_price_decrease_auto_adjusts_sale_price(db_session):
    p = make_product(db_session, sale_price=150000.0, source_price=120000.0, auto_adjust_price=True)
    result = await handle_source_price_change(db_session, p, 100000.0)
    db_session.refresh(p)
    assert result["action"] == "applied"
    assert p.source_price == 100000.0
    assert p.sale_price == 130000.0
    assert p.price_margin == 30000.0


@pytest.mark.asyncio
async def test_auto_adjust_off_leaves_sale_price_untouched(db_session):
    p = make_product(db_session, sale_price=130000.0, source_price=100000.0, auto_adjust_price=False)
    result = await handle_source_price_change(db_session, p, 120000.0)
    db_session.refresh(p)
    assert result["action"] == "applied"
    assert result["sale_price_changed"] is False
    assert p.source_price == 120000.0
    assert p.sale_price == 130000.0  # untouched


def test_manual_sale_price_edit_recomputes_margin(db_session):
    p = make_product(db_session, sale_price=130000.0, source_price=100000.0, auto_adjust_price=False)
    apply_admin_price_edit(db_session, p, 100000.0, 150000.0)
    db_session.commit()
    assert p.sale_price == 150000.0
    assert p.price_margin == 50000.0


@pytest.mark.asyncio
async def test_idempotent_resync_with_unchanged_price_is_noop(db_session):
    p = make_product(db_session, sale_price=130000.0, source_price=100000.0, auto_adjust_price=True)
    await handle_source_price_change(db_session, p, 120000.0)
    db_session.refresh(p)
    hist_count = db_session.query(ProductPriceHistory).filter(ProductPriceHistory.product_id == p.id).count()

    result = await handle_source_price_change(db_session, p, 120000.0)  # same price again

    assert result["action"] == "noop"
    assert db_session.query(ProductPriceHistory).filter(ProductPriceHistory.product_id == p.id).count() == hist_count


@pytest.mark.asyncio
async def test_above_threshold_jump_requires_approval(db_session):
    p = make_product(
        db_session, sale_price=130000.0, source_price=100000.0, auto_adjust_price=True,
        require_admin_approval_above_percent=50.0,
    )
    result = await handle_source_price_change(db_session, p, 200000.0)  # +100% > 50% threshold
    db_session.refresh(p)

    assert result["action"] == "pending_approval"
    assert p.price_pending_approval is True
    assert p.pending_new_source_price == 200000.0
    # Nothing applied yet — source/sale price stay at their old values.
    assert p.source_price == 100000.0
    assert p.sale_price == 130000.0


@pytest.mark.asyncio
async def test_approving_pending_price_applies_margin_preserving_formula(db_session):
    p = make_product(
        db_session, sale_price=130000.0, source_price=100000.0, auto_adjust_price=True,
        require_admin_approval_above_percent=50.0,
    )
    await handle_source_price_change(db_session, p, 200000.0)
    db_session.refresh(p)

    result = await approve_pending_price(db_session, p)
    db_session.refresh(p)

    assert result["action"] == "applied"
    assert p.price_pending_approval is False
    assert p.pending_new_source_price is None
    assert p.source_price == 200000.0
    assert p.sale_price == 230000.0  # 200k + preserved 30k margin
