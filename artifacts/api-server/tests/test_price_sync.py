"""
Tests for auto price-adjustment ("giữ nguyên phần chênh lệch"): a Product's
price_margin (sale_price - source_price) is preserved when the supplier
source price changes, subject to auto_adjust_price on/off and an optional
per-product approval threshold for abnormal jumps. Also covers the
admin-only notification contract: customers must never be notified about a
price change, regardless of direction or auto_adjust_price setting.
"""
import pytest

from models import Product, DeliveryMode, SourceType, ProductPriceHistory, TelegramBotConfig
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


def _make_bot_config(db_session, admin_telegram_id="admin-1", notify_admin_on_price_change=True):
    cfg = TelegramBotConfig(
        admin_telegram_id=admin_telegram_id,
        notify_admin_on_price_change=notify_admin_on_price_change,
    )
    db_session.add(cfg)
    db_session.commit()
    return cfg


class _FakeBotManager:
    def __init__(self, running=True):
        self._running = running
        self.sent = []

    def is_running(self):
        return self._running

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return True


@pytest.mark.asyncio
async def test_price_increase_sends_admin_only_notification_with_exact_template(db_session, monkeypatch):
    _make_bot_config(db_session)
    fake_bot = _FakeBotManager()
    monkeypatch.setattr("services.bot_service.bot_manager", fake_bot)
    broadcast_calls = []
    monkeypatch.setattr(
        "services.broadcast_service._broadcast_message_with_buy_button",
        lambda *a, **kw: broadcast_calls.append((a, kw)),
    )

    p = make_product(db_session, sale_price=100000.0, source_price=70000.0, auto_adjust_price=True)
    result = await handle_source_price_change(db_session, p, 100000.0)
    db_session.refresh(p)

    assert result["action"] == "applied"
    assert p.sale_price == 130000.0  # 100k + preserved 30k margin
    assert p.price_margin == 30000.0

    # Admin-only: exactly one Telegram message, to the admin chat.
    assert len(fake_bot.sent) == 1
    chat_id, text, _ = fake_bot.sent[0]
    assert chat_id == "admin-1"
    assert "✅ ĐÃ TỰ ĐỘNG CẬP NHẬT GIÁ" in text
    assert "📦 Sản phẩm:" in text
    assert "🏦 Giá nguồn cũ:" in text
    assert "🏦 Giá nguồn mới:" in text
    assert "📊 Chênh lệch giữ nguyên:" in text
    assert "💰 Giá bán cũ:" in text
    assert "💰 Giá bán mới:" in text
    # No "buy now" button/keyboard on admin price alerts.
    assert "Mua ngay" not in text
    # Customers were never broadcast to.
    assert broadcast_calls == []


@pytest.mark.asyncio
async def test_price_decrease_sends_admin_only_notification(db_session, monkeypatch):
    _make_bot_config(db_session)
    fake_bot = _FakeBotManager()
    monkeypatch.setattr("services.bot_service.bot_manager", fake_bot)
    broadcast_calls = []
    monkeypatch.setattr(
        "services.broadcast_service._broadcast_message_with_buy_button",
        lambda *a, **kw: broadcast_calls.append((a, kw)),
    )

    p = make_product(db_session, sale_price=100000.0, source_price=70000.0, auto_adjust_price=True)
    result = await handle_source_price_change(db_session, p, 50000.0)
    db_session.refresh(p)

    assert result["action"] == "applied"
    assert p.sale_price == 80000.0  # 50k + preserved 30k margin
    assert p.price_margin == 30000.0
    assert len(fake_bot.sent) == 1
    assert broadcast_calls == []


@pytest.mark.asyncio
async def test_auto_adjust_off_still_notifies_admin_only(db_session, monkeypatch):
    _make_bot_config(db_session)
    fake_bot = _FakeBotManager()
    monkeypatch.setattr("services.bot_service.bot_manager", fake_bot)
    broadcast_calls = []
    monkeypatch.setattr(
        "services.broadcast_service._broadcast_message_with_buy_button",
        lambda *a, **kw: broadcast_calls.append((a, kw)),
    )

    p = make_product(db_session, sale_price=100000.0, source_price=70000.0, auto_adjust_price=False)
    result = await handle_source_price_change(db_session, p, 90000.0)
    db_session.refresh(p)

    assert result["sale_price_changed"] is False
    assert p.sale_price == 100000.0  # untouched
    assert p.source_price == 90000.0  # still updated
    assert len(fake_bot.sent) == 1
    chat_id, text, _ = fake_bot.sent[0]
    assert "Tự động điều chỉnh giá đang tắt" in text
    assert broadcast_calls == []


@pytest.mark.asyncio
async def test_admin_notify_toggle_off_suppresses_admin_message_too(db_session, monkeypatch):
    _make_bot_config(db_session, notify_admin_on_price_change=False)
    fake_bot = _FakeBotManager()
    monkeypatch.setattr("services.bot_service.bot_manager", fake_bot)

    p = make_product(db_session, sale_price=100000.0, source_price=70000.0, auto_adjust_price=True)
    await handle_source_price_change(db_session, p, 100000.0)

    assert fake_bot.sent == []


def test_no_user_price_change_notification_function_exists():
    """The customer-facing price-change broadcast path must be fully
    removed, not just gated off."""
    import services.price_sync_service as mod
    assert not hasattr(mod, "notify_users_price_changed")


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
