from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey, Enum as SAEnum, UniqueConstraint
from sqlalchemy.orm import relationship, declared_attr
from database import Base
import enum


class TenantScopedMixin:
    """
    Mixin for every model whose rows belong to exactly one tenant (shop
    owner account — see AdminUser.is_owner / AdminUser doubling as tenant
    identity). `tenant_id` is declared here via @declared_attr (not as a
    plain Column) so `with_loader_criteria(TenantScopedMixin, ...)` in
    tenancy.py can resolve `TenantScopedMixin.tenant_id` directly — its
    lambda-caching/analysis step accesses the attribute on the base class
    itself, which only works if the column is actually defined on the
    mixin. See tenancy.py for the actual filtering + auto-assignment
    machinery this enables.
    """
    @declared_attr
    def tenant_id(cls):
        return Column(Integer, ForeignKey("admin_users.id"), nullable=True, index=True)


class BotStatus(str, enum.Enum):
    stopped = "stopped"
    starting = "starting"
    running = "running"
    reconnecting = "reconnecting"
    error = "error"


class SourceType(str, enum.Enum):
    manual = "manual"
    api = "api"


class DeliveryMode(str, enum.Enum):
    manual = "manual"              # legacy value — treated as manual_admin everywhere
    manual_admin = "manual_admin"  # no local inventory; admin delivers manually
    manual_stock = "manual_stock"  # local inventory ("kho tài khoản"); auto-delivered
    api_auto = "api_auto"


class InventoryStatus(str, enum.Enum):
    available = "available"
    reserved = "reserved"
    sold = "sold"
    faulty = "faulty"
    deleted = "deleted"


class AuthType(str, enum.Enum):
    x_api_key = "x_api_key"
    bearer = "bearer"


class ApiType(str, enum.Enum):
    zampto_standard = "zampto_standard"
    custom = "custom"
    canboso_market = "canboso_market"
    aicenter_buyer = "aicenter_buyer"


class OrderStatus(str, enum.Enum):
    pending_manual = "pending_manual"
    pending_payment = "pending_payment"        # waiting for customer payment
    processing_api = "processing_api"
    completed = "completed"
    partial_delivery = "partial_delivery"
    failed = "failed"
    api_failed = "api_failed"                  # paid but API source failed
    payment_expired = "payment_expired"        # payment window expired
    cancelled = "cancelled"
    paid_waiting_stock = "paid_waiting_stock"  # paid but source ran out of stock
    waiting_manual_verification = "waiting_manual_verification"  # waiting admin approval (Binance manual)
    delivery_failed = "delivery_failed"        # paid, stock reserved, but Telegram delivery failed
    pending_seller_fulfillment = "pending_seller_fulfillment"  # paid; slot-type API item awaiting seller processing


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    partial = "partial"
    paid = "paid"
    overpaid = "overpaid"
    expired = "expired"
    failed = "failed"
    detected = "detected"      # crypto tx found, not enough confirmations yet
    confirming = "confirming"  # confirmations accumulating
    late_payment = "late_payment"  # received after expiry


class WalletCurrency(str, enum.Enum):
    VND = "VND"
    USDT = "USDT"


class WalletTxType(str, enum.Enum):
    deposit = "deposit"              # confirmed top-up
    purchase = "purchase"            # debited to pay for an order
    refund = "refund"                # auto-refund after a wallet-paid order failed
    admin_credit = "admin_credit"    # manual admin top-up
    admin_debit = "admin_debit"      # manual admin deduction
    withdrawal = "withdrawal"        # ví chợ: paid out to a tenant on withdrawal approval
    platform_fee = "platform_fee"    # ví chợ: 2% platform fee note (combined with purchase debit)


class MarketWalletWithdrawalStatus(str, enum.Enum):
    pending = "pending"      # requested, awaiting owner review
    approved = "approved"    # owner approved, transfer not yet marked sent
    paid = "paid"            # owner marked the payout as sent — terminal
    rejected = "rejected"    # owner rejected — terminal, balance untouched
    cancelled = "cancelled"  # tenant cancelled before review — terminal


class WalletDepositStatus(str, enum.Enum):
    pending = "pending"            # created, awaiting an incoming transfer/on-chain tx
    detected = "detected"          # a matching on-chain tx was seen (crypto only)
    confirming = "confirming"      # confirmations accumulating (crypto only)
    credited = "credited"          # auto-verified (or admin-approved) and wallet credited — terminal
    expired = "expired"            # deposit window passed with nothing received — terminal
    failed = "failed"              # verification failed / admin rejected from manual_review — terminal
    manual_review = "manual_review"  # auto-verification couldn't complete; admin must credit or fail it
    cancelled = "cancelled"        # shopper cancelled before anything arrived — terminal


class ApiClientStatus(str, enum.Enum):
    active = "active"    # can authenticate + place orders
    locked = "locked"    # admin-suspended, key kept, can be unlocked
    revoked = "revoked"  # permanently disabled (regenerate needed to use again)


class IssueStatus(str, enum.Enum):
    open = "open"            # just reported, not yet looked at
    reviewing = "reviewing"  # admin is actively looking into it
    refunded = "refunded"    # admin refunded the order to the buyer's wallet — terminal
    rejected = "rejected"    # admin rejected the report with a reason — terminal
    resolved = "resolved"    # admin marked it handled without a refund — terminal


def now():
    return datetime.utcnow()


class AdminUser(Base):
    """
    An admin login. Also doubles as the "tenant" identity for multi-tenant
    rental: every AdminUser IS a tenant, and every TenantScopedMixin row's
    tenant_id points back to one of these rows (see tenancy.py). The very
    first admin account ever created (is_owner=True) is the platform owner
    who creates/manages the other tenant accounts from /tenants — tenants
    themselves never see or manage other tenants.
    """
    __tablename__ = "admin_users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    # True only for the platform owner (first admin account). Owners manage
    # tenant accounts; tenants are regular shop-admin users scoped to their
    # own data everywhere else.
    is_owner = Column(Boolean, default=False, nullable=False)
    # Rental expiry. NULL = never expires (always true for the owner).
    # Enforced in the tenant-context middleware (main.py): once past this
    # date the account is auto-locked (is_active flips to False) on its next
    # request, rather than a background job — no admin-config schedule needed.
    expires_at = Column(DateTime, nullable=True)
    display_name = Column(String(255), nullable=True)  # shown to the owner in the tenant list
    notes = Column(Text, nullable=True)
    # ── Ví chợ ("market wallet") ────────────────────────────────────────────
    # Balance a tenant pre-funds so their bot may list/sell chợ-sourced
    # (source_type=api) products — see services/market_wallet_service.py and
    # services/market_stock_service.py. The owner's own row uses this same
    # column to record how much THEY have prepaid to the real upstream
    # supplier (CanBoSo/Zampto) — auto-fetched via the generic API connection
    # engine's balance endpoint if the connected supplier exposes one,
    # otherwise entered/edited by hand from the owner's ví chợ page. Never
    # written directly — always through market_wallet_service credit/debit.
    market_wallet_balance = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class Setting(Base, TenantScopedMixin):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True, index=True)
    # NOTE: uniqueness on `key` alone would collide across tenants (every
    # tenant needs its own "exchange_rate_config" row, etc). The real
    # constraint is (tenant_id, key) — enforced by a rebuilt index in
    # main.py's migrations (SQLite can't ALTER an existing UNIQUE
    # constraint in place), not by this column declaration.
    key = Column(String(100), nullable=False)
    value = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class TelegramBotConfig(Base, TenantScopedMixin):
    __tablename__ = "telegram_bot_config"
    id = Column(Integer, primary_key=True, index=True)
    bot_token_encrypted = Column(Text, nullable=True)
    admin_telegram_id = Column(String(100), nullable=True)
    welcome_message = Column(Text, nullable=True)
    support_username = Column(String(100), nullable=True)
    shop_name = Column(String(255), nullable=True)
    is_enabled = Column(Boolean, default=False)
    bot_status = Column(SAEnum(BotStatus), default=BotStatus.stopped)
    bot_username = Column(String(100), nullable=True)
    bot_name = Column(String(255), nullable=True)
    # Product list display settings
    show_out_of_stock = Column(Boolean, default=True)
    allow_manual_order_when_out_of_stock = Column(Boolean, default=False)
    products_per_page = Column(Integer, default=15)
    default_product_icon = Column(String(20), default="📦")
    default_language = Column(String(10), default="vi")
    # Local inventory ("kho tài khoản") settings
    notify_users_when_restocked = Column(Boolean, default=False)
    allow_partial_delivery = Column(Boolean, default=False)
    # Broadcast-style "new product" / "restock" announcements to ALL active
    # users (distinct from notify_users_when_restocked above, which only
    # pings users with a paid_waiting_stock order for that product).
    notify_new_products = Column(Boolean, default=True)
    notify_restock = Column(Boolean, default=True)
    # Auto price-adjustment notifications: ADMIN-ONLY. Customers must never
    # be notified about source/sale price changes — that logic has been
    # permanently removed from services/price_sync_service.py, not just
    # gated off. This column is kept (unused) so any historical DB value
    # doesn't error out; it is never read or written anymore.
    notify_users_on_price_change = Column(Boolean, default=False)
    # Whether the admin Telegram account gets pinged on every source-price
    # change (both when auto_adjust_price is on and off). Default on.
    notify_admin_on_price_change = Column(Boolean, default=True)
    broadcast_batch_size = Column(Integer, default=25)
    broadcast_delay_ms = Column(Integer, default=300)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class NotificationEvent(Base, TenantScopedMixin):
    """
    Dedup/audit ledger for automatic "new product" / "restock" broadcasts.
    event_key is unique so the same real-world event (a specific product
    becoming newly available, or reaching a specific stock total) can never
    be announced twice, no matter how many times a scheduler tick, API sync,
    or admin action re-triggers the underlying check.
    """
    __tablename__ = "notification_events"
    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(30), nullable=False)  # "new_product" | "restock"
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    source_id = Column(Integer, nullable=True)  # api_connection_id when triggered by an API sync, else NULL
    previous_stock = Column(Integer, nullable=True)
    current_stock = Column(Integer, nullable=True)
    added_quantity = Column(Integer, nullable=True)
    event_key = Column(String(150), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=now)
    sent_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="sent")  # "sent" | "skipped" | "failed"


class ProductPriceHistory(Base, TenantScopedMixin):
    """
    Audit trail of every source_price/sale_price change on a Product, so
    admins can see exactly when and why a price moved (see
    services/price_sync_service.py). One row per change event — never
    updated in place.
    """
    __tablename__ = "product_price_history"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    source_connection_id = Column(Integer, ForeignKey("api_connections.id"), nullable=True)
    old_source_price = Column(Float, nullable=True)
    new_source_price = Column(Float, nullable=True)
    old_sale_price = Column(Float, nullable=True)
    new_sale_price = Column(Float, nullable=True)
    margin = Column(Float, nullable=True)
    # "source_sync" | "admin_edit" | "auto_adjust" | "manual_override"
    change_type = Column(String(30), nullable=False)
    changed_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=now)

    product = relationship("Product")


class SepayConfig(Base, TenantScopedMixin):
    """SePay payment gateway configuration. Sensitive fields are Fernet-encrypted."""
    __tablename__ = "sepay_config"
    id = Column(Integer, primary_key=True, index=True)
    is_enabled = Column(Boolean, default=False)
    bank_name = Column(String(255), nullable=True)
    account_number = Column(String(100), nullable=True)
    account_name = Column(String(255), nullable=True)
    bank_bin = Column(String(20), nullable=True)
    api_token_encrypted = Column(Text, nullable=True)        # NEVER logged
    webhook_secret_encrypted = Column(Text, nullable=True)   # NEVER logged
    payment_prefix = Column(String(20), default="AIC")
    payment_timeout_minutes = Column(Integer, default=15)
    allow_overpay = Column(Boolean, default=True)
    auto_refund_partial = Column(Boolean, default=False)
    test_mode = Column(Boolean, default=False)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class PaymentMethod(Base, TenantScopedMixin):
    """
    Configurable payment methods: sepay, binance_pay, usdt_bep20, usdt_trc20.
    config_encrypted stores a JSON blob (Fernet-encrypted) with method-specific keys.
    """
    __tablename__ = "payment_methods"
    id = Column(Integer, primary_key=True, index=True)
    # NOTE: real uniqueness is (tenant_id, method_code) — see Setting above
    # for why. Rebuilt in main.py's migrations.
    method_code = Column(String(50), nullable=False)   # sepay|binance_pay|usdt_bep20|usdt_trc20
    display_name_vi = Column(String(100), nullable=False)
    display_name_en = Column(String(100), nullable=False)
    is_active = Column(Boolean, default=False)
    config_encrypted = Column(Text, nullable=True)   # JSON, Fernet-encrypted — NEVER logged
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class User(Base, TenantScopedMixin):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    # NOT globally unique — the same real Telegram account can be a customer
    # of multiple tenants' shops. Uniqueness is enforced per-tenant via the
    # composite index (tenant_id, telegram_id) — see _fix_legacy_unique_constraints
    # in main.py for the SQLite rebuild that migrates this off the old
    # single-column UNIQUE constraint.
    telegram_id = Column(String(50), nullable=False)
    username = Column(String(100), nullable=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    language_code = Column(String(10), default="vi", nullable=False)
    # True once the user has explicitly picked a language (vs. the "vi" default
    # assigned at row-creation time). Brand-new users see a forced language
    # picker on /start until this becomes True.
    language_selected = Column(Boolean, default=False, nullable=False)
    total_orders = Column(Integer, default=0)
    total_spent = Column(Float, default=0.0)
    balance = Column(Float, default=0.0, nullable=False)  # wallet balance carried over from legacy-bot import
    # New wallet balances (deposit/pay-with-wallet feature). Kept separate from
    # the legacy `balance` column above — different provenance, never auto-merged.
    wallet_vnd = Column(Float, default=0.0, nullable=False)
    wallet_usdt = Column(Float, default=0.0, nullable=False)
    is_banned = Column(Boolean, default=False)
    # True once a broadcast/DM send gets a Telegram "Forbidden" (user blocked
    # the bot). Distinct from is_banned (admin action) — this is detected
    # automatically and excludes the user from future broadcasts.
    is_blocked = Column(Boolean, default=False, nullable=False)
    last_active_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)
    # Current membership rank (see Rank below). Recomputed from live total-spend
    # after every successfully paid order — see services/rank_service.py.
    rank_id = Column(Integer, ForeignKey("ranks.id"), nullable=True)

    orders = relationship("Order", back_populates="user", foreign_keys="Order.telegram_user_id")
    rank = relationship("Rank", foreign_keys=[rank_id])


class Rank(Base, TenantScopedMixin):
    """
    Admin-configurable membership tier ("Cấp bậc"). A user's rank is derived
    from their live total spend (SUM of paid orders), never from a manually
    assigned value — see services.rank_service.compute_total_spent(). Fully
    editable from the Web Admin "Cấp bậc" page: name/emoji/threshold/order/
    active can all change without a code deploy.
    """
    __tablename__ = "ranks"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    emoji = Column(String(20), nullable=False, default="🏅")
    min_spend = Column(Float, nullable=False, default=0.0)  # VND threshold to reach this rank
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class EmojiIcon(Base, TenantScopedMixin):
    """
    Admin-managed library of Telegram custom emoji, used by the "Chọn icon
    sản phẩm" picker on the product add/edit pages (see
    routers/emoji_icons.py). Populated either by importing a whole custom
    emoji sticker pack (services/telegram_emoji.fetch_custom_emoji_stickers,
    e.g. from https://t.me/addemoji/IconsEmoji_JABA) or by an admin typing
    in a single icon's name/custom_emoji_id/fallback_emoji by hand when
    auto-import isn't possible (no bot token configured, pack unreachable, etc).
    Products never store an image for this — only the Telegram custom_emoji_id
    (Product.telegram_custom_emoji_id), which Telegram itself renders.
    """
    __tablename__ = "emoji_icons"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    custom_emoji_id = Column(String(100), unique=True, nullable=False)
    fallback_emoji = Column(String(20), nullable=False, default="⭐")
    sticker_set_name = Column(String(255), nullable=True)  # which pack this came from, if imported
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=now)


class Product(Base, TenantScopedMixin):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    # NOTE: real uniqueness is (tenant_id, product_code) — two tenants may
    # legitimately pick the same code. Rebuilt in main.py's migrations.
    product_code = Column(String(100), nullable=False)
    name = Column(String(255), nullable=False)
    name_en = Column(String(255), nullable=True)    # English name (optional; falls back to name)
    description = Column(Text, nullable=True)
    description_en = Column(Text, nullable=True)   # English description (optional)
    image_path = Column(String(500), nullable=True)
    sale_price = Column(Float, default=0.0)
    price_usdt = Column(Float, default=0.0)   # auto-computed from sale_price + current exchange rate
    # ── Auto price-adjustment ("giữ nguyên phần chênh lệch") ────────────────
    # source_price is the current known supplier/cost price (backfilled from
    # the primary ProductSource.last_cost for API-linked products, or set by
    # hand for manual products). price_margin = sale_price - source_price is
    # a STORED snapshot, not derived live — it's only recomputed when an
    # admin edits sale_price/source_price by hand, or the very first time
    # auto_adjust_price is turned on. When the supplier price changes on the
    # next sync, sale_price is recomputed as new_source_price + price_margin
    # (if auto_adjust_price is on) so the admin's markup is preserved.
    source_price = Column(Float, nullable=True)
    price_margin = Column(Float, nullable=True)
    auto_adjust_price = Column(Boolean, default=False, nullable=False)
    last_source_price = Column(Float, nullable=True)
    last_sale_price = Column(Float, nullable=True)
    last_price_updated_at = Column(DateTime, nullable=True)
    # Price guard-rails (all optional; null/0 = no limit — see services/price_sync_service.py)
    min_sale_price = Column(Float, nullable=True)
    max_sale_price = Column(Float, nullable=True)
    # If a source-price sync would raise the price by more than this percent,
    # the change is NOT auto-applied — it's parked as a pending approval
    # (price_pending_approval + pending_new_source_price) until an admin
    # approves or rejects it. Null = no cap, always auto-apply per the margin formula.
    require_admin_approval_above_percent = Column(Float, nullable=True)
    price_pending_approval = Column(Boolean, default=False, nullable=False)
    pending_new_source_price = Column(Float, nullable=True)
    min_quantity = Column(Integer, default=1)
    warranty = Column(String(255), nullable=True)
    duration = Column(String(255), nullable=True)
    source_type = Column(SAEnum(SourceType), default=SourceType.manual)
    delivery_mode = Column(SAEnum(DeliveryMode), default=DeliveryMode.manual)
    is_active = Column(Boolean, default=True)
    is_pinned = Column(Boolean, default=False)             # pinned products sort first
    telegram_icon = Column(String(100), nullable=True)     # fallback emoji shown in bot list / used when no custom emoji is set
    # Telegram custom emoji "document_id" chosen from the icon library (see
    # EmojiIcon below). When set, the bot renders it via
    # <tg-emoji emoji-id="...">fallback</tg-emoji> in HTML messages —
    # telegram_icon above is always kept in sync as the fallback character
    # shown to non-Premium users and anywhere HTML entities aren't supported
    # (e.g. inline keyboard button text). Locked/unlocked together with
    # telegram_icon via services.product_sync.apply_admin_icon_edit.
    telegram_custom_emoji_id = Column(String(100), nullable=True)
    allow_manual_order = Column(Boolean, default=False)     # allow ordering while out of stock (manual_admin-style)
    sold_count = Column(Integer, default=0)
    # Comma-separated subset of {"description", "image_path", "warranty", "duration"}.
    # Any field name in this set was explicitly edited by an admin and must
    # never be silently overwritten by the next automatic API sync.
    manually_edited_fields = Column(Text, nullable=True)
    # True once an admin has hand-written/edited name_en or description_en —
    # auto-translation (see services/product_sync.ensure_en_fields) must
    # never overwrite it again. Auto-generated (not admin-typed) text leaves
    # this False so it can still be regenerated/filled in later.
    name_en_locked = Column(Boolean, default=False)
    description_en_locked = Column(Boolean, default=False)
    # The exact Vietnamese `description` text that was last translated into
    # description_en. Lets auto-translation detect "source changed since we
    # last translated it" without re-calling the translator on every sync.
    description_en_source = Column(Text, nullable=True)
    # ── Translation bookkeeping (see services/product_sync.sync_translations) ──
    # Which side (name/description or name_en/description_en) the admin/API
    # source actually supplied — "vi" (default) or "en". Decides which
    # direction auto-translation runs: vi->en fills name_en/description_en
    # (existing behavior); en->vi fills name/description instead.
    source_language = Column(String(5), nullable=True, default="vi")
    # "pending" | "translated" | "failed" | "manual" (frozen by an admin
    # hand-edit of the target language — see description_en_locked).
    translation_status = Column(String(20), nullable=True, default="pending")
    # SHA-256 of the source-language description last (successfully or not)
    # sent to the translator, so an unchanged source is never re-translated.
    translation_source_hash = Column(String(64), nullable=True)
    translated_at = Column(DateTime, nullable=True)
    translation_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    sources = relationship("ProductSource", back_populates="product", cascade="all, delete-orphan")
    # passive_deletes=True: khi xoá Product, SQLAlchemy KHÔNG cố null hoá
    # orders.product_id (NOT NULL ở DB). Thay vào đó để DB tự xử lý — vì
    # SQLite mặc định tắt FK enforcement, order record vẫn tồn tại với
    # product_id trỏ đến product đã bị xoá (dangling ref), cho phép lịch sử
    # đơn hàng tiếp tục xem được với "(Đã xoá)" hiển thị thay product name.
    orders = relationship("Order", back_populates="product", passive_deletes=True)
    inventory_items = relationship("InventoryItem", back_populates="product", cascade="all, delete-orphan")


class RestockSubscription(Base, TenantScopedMixin):
    """
    Per-product "notify me when back in stock" waiting list. Created when a
    shopper taps "🔔 Báo khi có hàng" on an out-of-stock product; consumed
    (deleted) once the admin restocks and the targeted notification is sent.
    """
    __tablename__ = "restock_subscriptions"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    telegram_user_id = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=now)

    product = relationship("Product")

    __table_args__ = (
        UniqueConstraint("product_id", "telegram_user_id", name="uq_restock_sub_product_user"),
    )


class InventoryItem(Base, TenantScopedMixin):
    """
    One row per individual stock credential ("kho tài khoản") for manual_stock products.
    Passwords/raw values must NEVER be written to ActivityLog or general logs.
    """
    __tablename__ = "inventory_items"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    username = Column(String(500), nullable=True)
    password = Column(String(500), nullable=True)
    raw_value = Column(Text, nullable=True)     # full original line, used for delivery + dedupe
    email = Column(String(255), nullable=True)
    expiry = Column(String(100), nullable=True)
    note = Column(Text, nullable=True)
    cost_price = Column(Float, nullable=True, default=0.0)
    status = Column(SAEnum(InventoryStatus), default=InventoryStatus.available, nullable=False)
    reserved_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    sold_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)
    reserved_at = Column(DateTime, nullable=True)
    sold_at = Column(DateTime, nullable=True)

    product = relationship("Product", back_populates="inventory_items")

    __table_args__ = (
        # Speeds up the hot-path "count available for product" / "pick N available" queries
    )


class ApiConnection(Base, TenantScopedMixin):
    __tablename__ = "api_connections"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    base_url = Column(String(500), nullable=False)
    api_key_encrypted = Column(Text, nullable=True)
    auth_type = Column(SAEnum(AuthType), default=AuthType.x_api_key)
    api_type = Column(SAEnum(ApiType), default=ApiType.zampto_standard)
    is_active = Column(Boolean, default=True)
    # Owner-only toggle: exposes this connection's synced ApiProduct catalog
    # to every tenant via "Kho hàng chung" so they can list ("treo chợ")
    # items from it without creating their own connection/API key. See
    # services/shared_catalog.py. Meaningless (ignored) on a non-owner
    # tenant's own connection.
    is_shared_with_tenants = Column(Boolean, default=False, nullable=False)
    sync_interval_minutes = Column(Integer, default=60)
    last_sync_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    api_products = relationship("ApiProduct", back_populates="connection", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="api_connection")


class ApiProduct(Base, TenantScopedMixin):
    __tablename__ = "api_products"
    id = Column(Integer, primary_key=True, index=True)
    api_connection_id = Column(Integer, ForeignKey("api_connections.id"), nullable=False)
    external_product_id = Column(String(255), nullable=False)
    external_name = Column(String(500), nullable=True)
    external_description = Column(Text, nullable=True)
    external_price = Column(Float, nullable=True)
    external_stock = Column(Integer, nullable=True)
    external_min_quantity = Column(Integer, nullable=True)
    external_max_quantity = Column(Integer, nullable=True)
    external_warranty = Column(String(255), nullable=True)
    external_duration = Column(String(255), nullable=True)
    external_image_url = Column(String(1000), nullable=True)
    external_status = Column(String(100), nullable=True)
    # ── Generic supplier fields (shared across adapters, not tied to one) ───
    # item_type distinguishes "account" (delivered instantly on purchase)
    # from "slot" (purchase just creates a request the seller must fulfill).
    # Left NULL for suppliers that don't have this concept — treated as
    # "account" (the pre-existing instant-delivery behavior) everywhere.
    external_item_type = Column(String(20), nullable=True)
    external_seller = Column(String(255), nullable=True)
    external_category = Column(String(100), nullable=True)  # category or emoji tag
    # ── AI Center Buyer-specific fields (canboso.com telegram-buyer API) ────
    external_is_slot_product = Column(Boolean, nullable=True)
    external_slot_durations = Column(Text, nullable=True)  # JSON list, e.g. "[1,3,6,12]"
    external_requires_customer_email = Column(Boolean, nullable=True)
    external_requires_slot_months = Column(Boolean, nullable=True)
    external_currency = Column(String(20), nullable=True)
    external_usd_price = Column(Float, nullable=True)
    raw_json = Column(Text, nullable=True)
    last_sync_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    connection = relationship("ApiConnection", back_populates="api_products")
    product_sources = relationship("ProductSource", back_populates="api_product")


class ProductSource(Base, TenantScopedMixin):
    __tablename__ = "product_sources"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    api_product_id = Column(Integer, ForeignKey("api_products.id"), nullable=False)
    priority = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    # True when this source was created via "Kho hàng chung" (see
    # services/shared_catalog.py) — api_product_id then points at an
    # ApiProduct owned by the OWNER's tenant, not this row's own tenant_id.
    # Fulfillment/sync code must resolve api_product/connection via
    # shared_catalog helpers (tenant-filter bypass by known id) rather than
    # the plain relationship, which the tenant-scoping filter would hide.
    shared_from_admin = Column(Boolean, default=False, nullable=False)
    last_cost = Column(Float, nullable=True)
    last_stock = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    product = relationship("Product", back_populates="sources")
    api_product = relationship("ApiProduct", back_populates="product_sources")
    order_attempts = relationship("OrderSourceAttempt", back_populates="product_source")


class Order(Base, TenantScopedMixin):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    order_code = Column(String(100), unique=True, nullable=False)
    telegram_user_id = Column(String(50), ForeignKey("users.telegram_id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, default=1)
    unit_price = Column(Float, default=0.0)
    total_price = Column(Float, default=0.0)
    source_unit_price = Column(Float, nullable=True)
    api_connection_id = Column(Integer, ForeignKey("api_connections.id"), nullable=True)
    external_order_id = Column(String(255), nullable=True)
    external_order_code = Column(String(255), nullable=True)
    delivery_data = Column(Text, nullable=True)
    delivery_items = Column(Text, nullable=True)
    partial_count = Column(Integer, nullable=True)
    status = Column(SAEnum(OrderStatus), default=OrderStatus.pending_manual)
    notes = Column(Text, nullable=True)
    # ── Payment fields ────────────────────────────────────────────────────────
    payment_status = Column(SAEnum(PaymentStatus), nullable=True)
    payment_method = Column(String(50), nullable=True)            # bank_transfer|binance_pay|usdt_bep20|usdt_trc20
    payment_code = Column(String(50), nullable=True, index=True)  # SePay transfer content code
    expected_amount = Column(Float, nullable=True)
    paid_amount = Column(Float, nullable=True, default=0.0)
    payment_expires_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    payment_transaction_id = Column(String(255), nullable=True)
    payment_raw_data = Column(Text, nullable=True)
    payment_message_id = Column(Integer, nullable=True)
    payment_chat_id = Column(Integer, nullable=True)
    payment_message_type = Column(String(20), nullable=True)      # "photo" | "text"
    product_message_id = Column(Integer, nullable=True)
    quantity_prompt_message_id = Column(Integer, nullable=True)
    # Delivery-result message(s) sent to the buyer once the order completes —
    # tracked the same way as the other *_message_id fields above so "🛍 Mua
    # tiếp" can clean up the whole purchase thread (card → quantity prompt →
    # payment → delivery) before showing a fresh product list.
    delivery_message_id = Column(Integer, nullable=True)
    delivery_file_message_id = Column(Integer, nullable=True)
    origin_products_page = Column(Integer, nullable=True, default=0)  # product-list page shopper was browsing before buying
    # ── Crypto payment fields ─────────────────────────────────────────────────
    payment_currency = Column(String(20), nullable=True)          # VND | USDT
    exchange_rate = Column(Float, nullable=True)                   # VND/USDT rate at order time
    expected_crypto_amount = Column(Float, nullable=True)          # exact USDT amount (with unique offset)
    received_crypto_amount = Column(Float, nullable=True)
    payment_address = Column(String(200), nullable=True)           # wallet address shown to user
    payment_memo = Column(String(100), nullable=True)              # memo/tag if required
    payment_txid = Column(String(200), nullable=True)              # blockchain tx hash
    payment_network = Column(String(50), nullable=True)            # BEP20 | TRC20 | BINANCE
    confirmations = Column(Integer, nullable=True, default=0)
    required_confirmations = Column(Integer, nullable=True)
    # ── Wallet fields ─────────────────────────────────────────────────────────
    # True once this order's total_price has been auto-refunded back to the
    # buyer's wallet_vnd after a fulfillment failure. Only ever set for orders
    # paid via payment_method == "wallet"; prevents double-refunding.
    refunded_to_wallet = Column(Boolean, default=False, nullable=False)
    # ── Ví chợ ("market wallet") debit guard ─────────────────────────────────
    # True once this order's cost + 2% platform fee has been debited from
    # the selling tenant's ví chợ balance (source_type=api products, non-owner
    # tenants only). Guards services.market_wallet_service so a retry after a
    # partial failure can never double-debit — see
    # services/payment_service.py's completion hook.
    market_wallet_debited = Column(Boolean, default=False, nullable=False)
    # ── Customer API fields ──────────────────────────────────────────────────
    # Set only for orders placed through the inbound customer REST API
    # (payment_method == "api_key"). client_order_id is the caller-supplied
    # idempotency key; uniqueness is enforced by a partial index (see
    # migrations in main.py) rather than a table-level UniqueConstraint,
    # since SQLite can't add one via ALTER TABLE on an existing table.
    api_client_id = Column(Integer, ForeignKey("api_clients.id"), nullable=True)
    client_order_id = Column(String(200), nullable=True)
    # ── Warranty / issue-refund fields ──────────────────────────────────────────
    # Snapshot of the product's warranty length (in days) taken at the moment
    # this order was created. Refund calculations must always use this value
    # (never the product's *current* warranty), since an admin may edit the
    # product's warranty text after this order shipped.
    warranty_days = Column(Integer, nullable=True)
    # Set once an admin approves a "💰 Hoàn tiền về ví" refund for an
    # order_issues report on this order. refunded_amount > 0 is the guard
    # that prevents a second refund of the same order.
    refunded_amount = Column(Float, nullable=True, default=0.0)
    refunded_at = Column(DateTime, nullable=True)
    refunded_by = Column(String(100), nullable=True)   # admin username/telegram id
    # ─────────────────────────────────────────────────────────────────────────
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    user = relationship("User", back_populates="orders", foreign_keys=[telegram_user_id])
    product = relationship("Product", back_populates="orders")
    api_connection = relationship("ApiConnection", back_populates="orders")
    source_attempts = relationship("OrderSourceAttempt", back_populates="order", cascade="all, delete-orphan")
    payment_transactions = relationship(
        "PaymentTransaction", back_populates="matched_order",
        foreign_keys="PaymentTransaction.matched_order_id",
    )
    crypto_transactions = relationship(
        "CryptoTransaction", back_populates="matched_order",
        foreign_keys="CryptoTransaction.matched_order_id",
    )


class PaymentTransaction(Base, TenantScopedMixin):
    """One row per incoming SePay webhook. Unique on (provider, external_transaction_id)."""
    __tablename__ = "payment_transactions"
    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(50), default="sepay", nullable=False)
    external_transaction_id = Column(String(255), nullable=False)
    gateway = Column(String(100), nullable=True)
    transaction_date = Column(DateTime, nullable=True)
    account_number = Column(String(100), nullable=True)
    transfer_content = Column(Text, nullable=True)
    amount_in = Column(Float, default=0.0)
    amount_out = Column(Float, default=0.0)
    reference_code = Column(String(255), nullable=True)
    matched_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    matched_deposit_id = Column(Integer, ForeignKey("wallet_deposits.id"), nullable=True)
    matched_market_deposit_id = Column(Integer, ForeignKey("market_wallet_deposits.id"), nullable=True)
    match_status = Column(String(50), nullable=True)
    raw_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now)

    matched_order = relationship("Order", back_populates="payment_transactions",
                                  foreign_keys=[matched_order_id])
    matched_deposit = relationship("WalletDeposit", foreign_keys=[matched_deposit_id])

    __table_args__ = (
        UniqueConstraint("provider", "external_transaction_id", name="uq_payment_tx"),
    )


class CryptoTransaction(Base, TenantScopedMixin):
    """
    On-chain USDT transfers (BEP20 or TRC20) detected by the background monitor.
    Unique on (network, txid, log_index) to prevent double-processing.
    """
    __tablename__ = "crypto_transactions"
    id = Column(Integer, primary_key=True, index=True)
    network = Column(String(20), nullable=False)            # BEP20 | TRC20
    token_symbol = Column(String(20), nullable=True)        # USDT
    token_contract = Column(String(100), nullable=True)
    txid = Column(String(200), nullable=False)
    log_index = Column(Integer, nullable=True, default=0)
    from_address = Column(String(200), nullable=True)
    to_address = Column(String(200), nullable=True)
    amount = Column(Float, nullable=True)
    block_number = Column(Integer, nullable=True)
    confirmations = Column(Integer, nullable=True, default=0)
    matched_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    matched_deposit_id = Column(Integer, ForeignKey("wallet_deposits.id"), nullable=True)
    matched_market_deposit_id = Column(Integer, ForeignKey("market_wallet_deposits.id"), nullable=True)
    status = Column(String(30), nullable=True)  # detected|confirming|confirmed|unmatched|duplicate
    raw_json = Column(Text, nullable=True)
    detected_at = Column(DateTime, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)

    matched_order = relationship("Order", back_populates="crypto_transactions",
                                  foreign_keys=[matched_order_id])
    matched_deposit = relationship("WalletDeposit", foreign_keys=[matched_deposit_id])

    __table_args__ = (
        UniqueConstraint("network", "txid", "log_index", name="uq_crypto_tx"),
    )


class WalletTransaction(Base, TenantScopedMixin):
    """
    Immutable ledger row for every wallet balance change (deposit, purchase
    debit, auto-refund, or manual admin credit/debit). balance_before/after
    let the admin/user history show a running total without recomputation.
    """
    __tablename__ = "wallet_transactions"
    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(String(50), ForeignKey("users.telegram_id"), nullable=False, index=True)
    currency = Column(SAEnum(WalletCurrency), nullable=False)
    tx_type = Column(SAEnum(WalletTxType), nullable=False)
    amount = Column(Float, nullable=False)           # always positive magnitude
    balance_before = Column(Float, nullable=False)
    balance_after = Column(Float, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    deposit_id = Column(Integer, ForeignKey("wallet_deposits.id"), nullable=True)
    note = Column(Text, nullable=True)
    actor = Column(String(100), nullable=True)  # "system" | "admin" | admin username
    created_at = Column(DateTime, default=now)

    user = relationship("User", foreign_keys=[telegram_user_id])
    order = relationship("Order", foreign_keys=[order_id])


class WalletDeposit(Base, TenantScopedMixin):
    """
    A shopper-initiated top-up request. VND deposits are auto-credited from
    the SePay webhook (matched on reference_code in the transfer content);
    USDT deposits are auto-credited by the same on-chain monitors / Binance
    Pay History sweep used for order payments. Admin manual credit/reject is
    only used as a fallback once a deposit reaches `manual_review`.
    """
    __tablename__ = "wallet_deposits"
    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(String(50), ForeignKey("users.telegram_id"), nullable=False, index=True)
    currency = Column(SAEnum(WalletCurrency), nullable=False)
    amount = Column(Float, nullable=False)
    method = Column(String(50), nullable=True)  # bank_transfer | binance_pay | usdt_bep20 | usdt_trc20 | usdt_erc20
    reference_code = Column(String(50), nullable=True, index=True)  # shown to shopper, helps admin match the transfer
    status = Column(SAEnum(WalletDepositStatus), default=WalletDepositStatus.pending, nullable=False)
    admin_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now)
    confirmed_at = Column(DateTime, nullable=True)
    confirmed_by = Column(String(100), nullable=True)

    # ── Auto-verification fields ────────────────────────────────────────────
    network = Column(String(50), nullable=True)             # BEP20 | TRC20 | ERC20 | BINANCE | None (VND)
    receiving_address = Column(String(200), nullable=True)   # wallet/bank account shown to the shopper
    payment_content = Column(String(100), nullable=True)     # exact bank-transfer content to match (VND)
    chat_id = Column(Integer, nullable=True)                 # telegram chat the deposit request message lives in
    deposit_message_id = Column(Integer, nullable=True)      # instruction message id, edited/deleted on outcome
    external_transaction_id = Column(String(255), nullable=True, index=True)
    confirmations = Column(Integer, nullable=True, default=0)
    required_confirmations = Column(Integer, nullable=True)
    raw_transaction_data = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    detected_at = Column(DateTime, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    credited_at = Column(DateTime, nullable=True)
    failed_reason = Column(Text, nullable=True)

    user = relationship("User", foreign_keys=[telegram_user_id])


# ── Ví chợ ("market wallet") — tenant funding of chợ-sourced listings ─────────
#
# Deliberately NOT TenantScopedMixin, same reasoning as AdminUser itself:
# admin_user_id below IS the tenant identity (AdminUser.id), and both the
# owner's cross-tenant review page and the background crypto monitor (which
# only ever runs scoped to the owner tenant, see tenancy.py) must be able to
# see every tenant's rows regardless of whichever tenant_id is current in
# the contextvar. Filtering these by admin_user_id is always explicit in the
# routers/services that use them — never implicit via the tenant-scoping
# event listener.

class MarketWalletDeposit(Base):
    """
    A tenant-initiated (or owner's own) ví chợ top-up request. Mirrors
    WalletDeposit's crypto-matching fields exactly so services/crypto_monitor.py
    can reuse the same on-chain scanning loops — the money always lands in
    the OWNER's real wallet address (tenants never configure their own), so
    matching is purely by unique expected amount, same as customer deposits.
    """
    __tablename__ = "market_wallet_deposits"
    id = Column(Integer, primary_key=True, index=True)
    admin_user_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False, index=True)
    # `currency`/`amount` describe the actual on-chain transfer being matched
    # (always USDT — ví chợ top-ups are crypto-only, see spec). The wallet
    # balance itself (AdminUser.market_wallet_balance) is VND-denominated —
    # matching Product.source_price's currency, which the virtual-stock
    # formula divides it by — so `vnd_credit_amount` is the VND amount
    # actually credited once this deposit confirms, locked in at the
    # exchange rate in effect when the request was created.
    currency = Column(SAEnum(WalletCurrency), nullable=False)
    amount = Column(Float, nullable=False)
    vnd_credit_amount = Column(Float, nullable=True)
    method = Column(String(50), nullable=True)  # binance_pay | usdt_bep20 | usdt_trc20
    reference_code = Column(String(50), nullable=True, index=True)
    status = Column(SAEnum(WalletDepositStatus), default=WalletDepositStatus.pending, nullable=False)
    admin_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now)
    confirmed_at = Column(DateTime, nullable=True)
    confirmed_by = Column(String(100), nullable=True)
    # ── Auto-verification fields (mirrors WalletDeposit) ────────────────────
    network = Column(String(50), nullable=True)             # BEP20 | TRC20 | BINANCE | VND (bank transfer)
    receiving_address = Column(String(200), nullable=True)
    # For VND/bank-transfer deposits: the exact content the tenant must put in
    # the "Nội dung chuyển khoản" field so SePay can auto-match the transfer.
    payment_content = Column(String(100), nullable=True)
    external_transaction_id = Column(String(255), nullable=True, index=True)
    confirmations = Column(Integer, nullable=True, default=0)
    required_confirmations = Column(Integer, nullable=True)
    raw_transaction_data = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    detected_at = Column(DateTime, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    credited_at = Column(DateTime, nullable=True)
    failed_reason = Column(Text, nullable=True)

    admin_user = relationship("AdminUser", foreign_keys=[admin_user_id])


class MarketWalletWithdrawal(Base):
    """A tenant's request to withdraw unused ví chợ balance back out. Owner
    approves, then marks paid once the manual transfer is actually sent —
    the balance is only debited on approval, never on the initial request,
    so a pending request can be cancelled without any wallet mutation."""
    __tablename__ = "market_wallet_withdrawals"
    id = Column(Integer, primary_key=True, index=True)
    admin_user_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False, index=True)
    currency = Column(SAEnum(WalletCurrency), nullable=False)
    amount = Column(Float, nullable=False)
    account_info = Column(Text, nullable=True)  # bank account / crypto address to pay out to
    status = Column(SAEnum(MarketWalletWithdrawalStatus), default=MarketWalletWithdrawalStatus.pending, nullable=False)
    admin_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now)
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by = Column(String(100), nullable=True)
    paid_at = Column(DateTime, nullable=True)

    admin_user = relationship("AdminUser", foreign_keys=[admin_user_id])


class MarketWalletTransaction(Base):
    """Immutable ledger row for every ví chợ balance change (deposit, sale
    debit [cost + 2% fee combined], withdrawal, or manual owner credit/debit)."""
    __tablename__ = "market_wallet_transactions"
    id = Column(Integer, primary_key=True, index=True)
    admin_user_id = Column(Integer, ForeignKey("admin_users.id"), nullable=False, index=True)
    currency = Column(SAEnum(WalletCurrency), nullable=False)
    tx_type = Column(SAEnum(WalletTxType), nullable=False)
    amount = Column(Float, nullable=False)  # always positive magnitude
    balance_before = Column(Float, nullable=False)
    balance_after = Column(Float, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    deposit_id = Column(Integer, ForeignKey("market_wallet_deposits.id"), nullable=True)
    withdrawal_id = Column(Integer, ForeignKey("market_wallet_withdrawals.id"), nullable=True)
    note = Column(Text, nullable=True)
    actor = Column(String(100), nullable=True)  # "system" | "owner" | admin username
    created_at = Column(DateTime, default=now)

    admin_user = relationship("AdminUser", foreign_keys=[admin_user_id])
    order = relationship("Order", foreign_keys=[order_id])


class ApiClient(Base, TenantScopedMixin):
    """
    One row per customer who has generated a programmatic API key (bot menu
    "🔗 API"). A customer may only have one active client at a time — the
    same row is reused across generate/regenerate/revoke.
    key_hash is an HMAC-SHA256 digest (see services/api_key_service.py),
    never the raw key — the raw value is shown to the customer exactly once,
    at generation/regeneration time, and cannot be recovered afterwards.
    """
    __tablename__ = "api_clients"
    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(String(50), ForeignKey("users.telegram_id"), nullable=False, index=True)
    name = Column(String(100), nullable=True)
    key_hash = Column(String(128), nullable=True, unique=True, index=True)
    key_prefix = Column(String(20), nullable=True)   # shown in masked form, e.g. "sk_live_ab12"
    status = Column(SAEnum(ApiClientStatus), default=ApiClientStatus.active, nullable=False)
    permissions = Column(Text, nullable=True)  # JSON list, e.g. ["products:read","orders:read","orders:create"]
    rate_limit_per_minute = Column(Integer, default=30, nullable=False)
    daily_limit = Column(Integer, default=2000, nullable=False)
    total_requests = Column(Integer, default=0, nullable=False)
    total_orders = Column(Integer, default=0, nullable=False)
    total_revenue_vnd = Column(Float, default=0.0, nullable=False)
    total_revenue_usdt = Column(Float, default=0.0, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    user = relationship("User", foreign_keys=[telegram_user_id])


class ApiRequestLog(Base, TenantScopedMixin):
    """One row per inbound customer-API request, written by the logging
    middleware right after the response is produced (see main.py)."""
    __tablename__ = "api_request_logs"
    id = Column(Integer, primary_key=True, index=True)
    api_client_id = Column(Integer, ForeignKey("api_clients.id"), nullable=False, index=True)
    method = Column(String(10), nullable=True)
    endpoint = Column(String(255), nullable=True)
    status_code = Column(Integer, nullable=True)
    ip_address = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=now, index=True)

    client = relationship("ApiClient")


class OrderSourceAttempt(Base, TenantScopedMixin):
    __tablename__ = "order_source_attempts"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_source_id = Column(Integer, ForeignKey("product_sources.id"), nullable=False)
    attempt_number = Column(Integer, default=1)
    status = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    external_order_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    order = relationship("Order", back_populates="source_attempts")
    product_source = relationship("ProductSource", back_populates="order_attempts")


class OrderIssue(Base, TenantScopedMixin):
    """
    A shopper's "⚠️ Báo lỗi" report against a delivered order, sent straight
    to the admin for review (view order / reply / refund to wallet / reject
    / mark resolved). `telegram_user_id` mirrors Order.telegram_user_id (the
    reporter — normally the order's buyer) rather than users.id, to match
    the rest of the schema's telegram-id-keyed convention.
    """
    __tablename__ = "order_issues"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    telegram_user_id = Column(String(50), ForeignKey("users.telegram_id"), nullable=False, index=True)
    telegram_chat_id = Column(String(50), nullable=True)
    issue_text = Column(Text, nullable=True)
    media_type = Column(String(20), nullable=True)         # photo | video | document | None
    telegram_file_id = Column(String(300), nullable=True)
    status = Column(SAEnum(IssueStatus), default=IssueStatus.open, nullable=False)
    # Refund pre-computed at report time for the admin's reference; the
    # actual refund action always RE-computes the amount at click time
    # (warranty keeps ticking down between report and admin action).
    calculated_refund_amount = Column(Float, nullable=True)
    calculated_refund_currency = Column(SAEnum(WalletCurrency), nullable=True)
    created_at = Column(DateTime, default=now)
    handled_by = Column(String(100), nullable=True)
    handled_at = Column(DateTime, nullable=True)
    resolution_note = Column(Text, nullable=True)

    order = relationship("Order")
    user = relationship("User", foreign_keys=[telegram_user_id])


class ActivityLog(Base, TenantScopedMixin):
    __tablename__ = "activity_logs"
    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    user_type = Column(String(50), nullable=True)
    user_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)
