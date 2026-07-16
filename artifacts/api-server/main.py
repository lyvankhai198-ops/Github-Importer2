import os
import logging
import asyncio
import uvicorn
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import text

from config import SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD, PORT
from database import engine, SessionLocal
from models import Base, AdminUser
from auth import hash_password
import tenancy  # noqa: F401 — registers the tenant-scoping SQLAlchemy event listeners on import

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"

# Background tasks started at lifespan startup, cancelled cleanly at shutdown
# so a restart never ends up with duplicate schedulers/pollers.
_background_tasks: list = []


def _run_migrations():
    """
    Add new columns to existing tables without dropping data.
    Each ALTER TABLE is wrapped in try/except — already-existing columns are silently skipped.
    """
    migrations = [
        # ApiProduct new fields
        "ALTER TABLE api_products ADD COLUMN external_description TEXT",
        "ALTER TABLE api_products ADD COLUMN external_min_quantity INTEGER",
        "ALTER TABLE api_products ADD COLUMN external_max_quantity INTEGER",
        "ALTER TABLE api_products ADD COLUMN external_warranty VARCHAR(255)",
        "ALTER TABLE api_products ADD COLUMN external_duration VARCHAR(255)",
        "ALTER TABLE api_products ADD COLUMN external_image_url VARCHAR(1000)",
        # Product new fields
        "ALTER TABLE products ADD COLUMN min_quantity INTEGER DEFAULT 1",
        "ALTER TABLE products ADD COLUMN warranty VARCHAR(255)",
        "ALTER TABLE products ADD COLUMN duration VARCHAR(255)",
        "ALTER TABLE products ADD COLUMN description_en TEXT",
        "ALTER TABLE products ADD COLUMN name_en VARCHAR(255)",
        "ALTER TABLE products ADD COLUMN price_usdt FLOAT DEFAULT 0.0",
        # Order core new fields
        "ALTER TABLE orders ADD COLUMN source_unit_price FLOAT",
        "ALTER TABLE orders ADD COLUMN external_order_code VARCHAR(255)",
        "ALTER TABLE orders ADD COLUMN delivery_items TEXT",
        "ALTER TABLE orders ADD COLUMN partial_count INTEGER",
        # Payment fields on Order (SePay)
        "ALTER TABLE orders ADD COLUMN payment_status VARCHAR(20)",
        "ALTER TABLE orders ADD COLUMN payment_method VARCHAR(50)",
        "ALTER TABLE orders ADD COLUMN payment_code VARCHAR(50)",
        "ALTER TABLE orders ADD COLUMN expected_amount FLOAT",
        "ALTER TABLE orders ADD COLUMN paid_amount FLOAT DEFAULT 0.0",
        "ALTER TABLE orders ADD COLUMN payment_expires_at DATETIME",
        "ALTER TABLE orders ADD COLUMN paid_at DATETIME",
        "ALTER TABLE orders ADD COLUMN payment_transaction_id VARCHAR(255)",
        "ALTER TABLE orders ADD COLUMN payment_raw_data TEXT",
        "CREATE INDEX IF NOT EXISTS ix_orders_payment_code ON orders (payment_code)",
        # QR / message tracking
        "ALTER TABLE orders ADD COLUMN payment_message_id INTEGER",
        "ALTER TABLE orders ADD COLUMN payment_chat_id INTEGER",
        "ALTER TABLE orders ADD COLUMN payment_message_type VARCHAR(20)",
        "ALTER TABLE orders ADD COLUMN product_message_id INTEGER",
        "ALTER TABLE orders ADD COLUMN quantity_prompt_message_id INTEGER",
        "ALTER TABLE orders ADD COLUMN origin_products_page INTEGER DEFAULT 0",
        # Crypto payment fields on Order
        "ALTER TABLE orders ADD COLUMN payment_currency VARCHAR(20)",
        "ALTER TABLE orders ADD COLUMN exchange_rate FLOAT",
        "ALTER TABLE orders ADD COLUMN expected_crypto_amount FLOAT",
        "ALTER TABLE orders ADD COLUMN received_crypto_amount FLOAT",
        "ALTER TABLE orders ADD COLUMN payment_address VARCHAR(200)",
        "ALTER TABLE orders ADD COLUMN payment_memo VARCHAR(100)",
        "ALTER TABLE orders ADD COLUMN payment_txid VARCHAR(200)",
        "ALTER TABLE orders ADD COLUMN payment_network VARCHAR(50)",
        "ALTER TABLE orders ADD COLUMN confirmations INTEGER DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN required_confirmations INTEGER",
        # User language
        "ALTER TABLE users ADD COLUMN language_code VARCHAR(10) DEFAULT 'vi'",
        # Product bot display fields
        "ALTER TABLE products ADD COLUMN telegram_icon VARCHAR(100)",
        "ALTER TABLE products ADD COLUMN is_pinned BOOLEAN DEFAULT 0",
        # TelegramBotConfig new settings
        "ALTER TABLE telegram_bot_config ADD COLUMN shop_name VARCHAR(255)",
        "ALTER TABLE telegram_bot_config ADD COLUMN show_out_of_stock BOOLEAN DEFAULT 1",
        "ALTER TABLE telegram_bot_config ADD COLUMN allow_manual_order_when_out_of_stock BOOLEAN DEFAULT 0",
        "ALTER TABLE telegram_bot_config ADD COLUMN products_per_page INTEGER DEFAULT 15",
        "ALTER TABLE telegram_bot_config ADD COLUMN default_product_icon VARCHAR(20) DEFAULT '📦'",
        "ALTER TABLE telegram_bot_config ADD COLUMN default_language VARCHAR(10) DEFAULT 'vi'",
        # Local inventory ("kho tài khoản") support
        "ALTER TABLE products ADD COLUMN allow_manual_order BOOLEAN DEFAULT 0",
        "ALTER TABLE telegram_bot_config ADD COLUMN notify_users_when_restocked BOOLEAN DEFAULT 0",
        "ALTER TABLE telegram_bot_config ADD COLUMN allow_partial_delivery BOOLEAN DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS inventory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            username VARCHAR(500),
            password VARCHAR(500),
            raw_value TEXT,
            email VARCHAR(255),
            expiry VARCHAR(100),
            note TEXT,
            cost_price FLOAT DEFAULT 0.0,
            status VARCHAR(20) NOT NULL DEFAULT 'available',
            reserved_order_id INTEGER,
            sold_order_id INTEGER,
            created_at DATETIME,
            updated_at DATETIME,
            reserved_at DATETIME,
            sold_at DATETIME,
            FOREIGN KEY(product_id) REFERENCES products(id),
            FOREIGN KEY(reserved_order_id) REFERENCES orders(id),
            FOREIGN KEY(sold_order_id) REFERENCES orders(id)
        )""",
        "CREATE INDEX IF NOT EXISTS ix_inventory_items_product_status ON inventory_items (product_id, status)",
        # Forced language-picker gate for brand-new users (see User.language_selected)
        "ALTER TABLE users ADD COLUMN language_selected BOOLEAN DEFAULT 0",
        # Manual-edit-safe API sync: tracks which Product fields an admin has
        # hand-edited so the next API sync never silently overwrites them.
        "ALTER TABLE products ADD COLUMN manually_edited_fields TEXT",
        # Per-product "notify me when back in stock" waiting list.
        """CREATE TABLE IF NOT EXISTS restock_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            telegram_user_id VARCHAR(50) NOT NULL,
            created_at DATETIME,
            FOREIGN KEY(product_id) REFERENCES products(id),
            UNIQUE(product_id, telegram_user_id)
        )""",
        # Freeze flags for auto-translated EN name/description — once an
        # admin hand-types either field, auto-translation must stop
        # overwriting it on the next save/sync.
        "ALTER TABLE products ADD COLUMN name_en_locked BOOLEAN DEFAULT 0",
        "ALTER TABLE products ADD COLUMN description_en_locked BOOLEAN DEFAULT 0",
        # Remembers the exact Vietnamese source text that was last translated
        # into description_en, so auto-translation can tell "source changed,
        # needs re-translating" apart from "already translated, nothing to do"
        # without re-calling the translator (and re-billing) on every sync.
        "ALTER TABLE products ADD COLUMN description_en_source TEXT",
        # Translation bookkeeping (see services/product_sync.sync_translations).
        # Existing rows default to source_language='vi'/status='pending' via
        # the ALTER TABLE default and are backfilled precisely in main.py's
        # startup routine right after migrations run (see _backfill_translation_bookkeeping).
        "ALTER TABLE products ADD COLUMN source_language VARCHAR(5) DEFAULT 'vi'",
        "ALTER TABLE products ADD COLUMN translation_status VARCHAR(20) DEFAULT 'pending'",
        "ALTER TABLE products ADD COLUMN translation_source_hash VARCHAR(64)",
        "ALTER TABLE products ADD COLUMN translated_at DATETIME",
        "ALTER TABLE products ADD COLUMN translation_error TEXT",
        # Telegram custom emoji icon picker (see models.EmojiIcon)
        "ALTER TABLE products ADD COLUMN telegram_custom_emoji_id VARCHAR(100)",
        """CREATE TABLE IF NOT EXISTS emoji_icons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(100) NOT NULL,
            custom_emoji_id VARCHAR(100) NOT NULL UNIQUE,
            fallback_emoji VARCHAR(20) NOT NULL DEFAULT '⭐',
            sticker_set_name VARCHAR(255),
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            created_at DATETIME
        )""",
        # Legacy-bot user import: wallet balance carried over, plus automatic
        # blocked-in-Telegram detection (distinct from admin-issued is_banned).
        "ALTER TABLE users ADD COLUMN balance FLOAT DEFAULT 0.0",
        "ALTER TABLE users ADD COLUMN is_blocked BOOLEAN DEFAULT 0",
        # Customer wallet feature: separate deposit/pay-with-wallet balances,
        # kept distinct from the legacy `balance` column above. New
        # wallet_transactions/wallet_deposits tables are created automatically
        # by Base.metadata.create_all (new tables, no ALTER needed).
        "ALTER TABLE users ADD COLUMN wallet_vnd FLOAT DEFAULT 0.0",
        "ALTER TABLE users ADD COLUMN wallet_usdt FLOAT DEFAULT 0.0",
        "ALTER TABLE orders ADD COLUMN refunded_to_wallet BOOLEAN DEFAULT 0",
        # Customer programmatic API (api_clients / api_request_logs tables are
        # new and created automatically by Base.metadata.create_all).
        "ALTER TABLE orders ADD COLUMN api_client_id INTEGER",
        "ALTER TABLE orders ADD COLUMN client_order_id VARCHAR(200)",
        # Enforces per-client idempotency for API-originated orders. Partial
        # index (SQLite supports WHERE on CREATE INDEX) — a table-level
        # UniqueConstraint can't be added via ALTER TABLE on an existing table.
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_api_client_order "
        "ON orders (api_client_id, client_order_id) WHERE client_order_id IS NOT NULL",
        # Automatic wallet-deposit verification (VND via SePay webhook, USDT
        # via the same on-chain monitors / Binance Pay sweep used for order
        # payments). Reuses payment_transactions/crypto_transactions for
        # anti-replay dedup instead of new tables/indexes.
        "ALTER TABLE wallet_deposits ADD COLUMN network VARCHAR(50)",
        "ALTER TABLE wallet_deposits ADD COLUMN receiving_address VARCHAR(200)",
        "ALTER TABLE wallet_deposits ADD COLUMN payment_content VARCHAR(100)",
        "ALTER TABLE wallet_deposits ADD COLUMN chat_id INTEGER",
        "ALTER TABLE wallet_deposits ADD COLUMN deposit_message_id INTEGER",
        "ALTER TABLE wallet_deposits ADD COLUMN external_transaction_id VARCHAR(255)",
        "ALTER TABLE wallet_deposits ADD COLUMN confirmations INTEGER DEFAULT 0",
        "ALTER TABLE wallet_deposits ADD COLUMN required_confirmations INTEGER",
        "ALTER TABLE wallet_deposits ADD COLUMN raw_transaction_data TEXT",
        "ALTER TABLE wallet_deposits ADD COLUMN expires_at DATETIME",
        "ALTER TABLE wallet_deposits ADD COLUMN detected_at DATETIME",
        "ALTER TABLE wallet_deposits ADD COLUMN verified_at DATETIME",
        "ALTER TABLE wallet_deposits ADD COLUMN credited_at DATETIME",
        "ALTER TABLE wallet_deposits ADD COLUMN failed_reason TEXT",
        "CREATE INDEX IF NOT EXISTS ix_wallet_deposits_ext_tx ON wallet_deposits (external_transaction_id)",
        "CREATE INDEX IF NOT EXISTS ix_wallet_deposits_status ON wallet_deposits (status)",
        "ALTER TABLE payment_transactions ADD COLUMN matched_deposit_id INTEGER",
        "ALTER TABLE crypto_transactions ADD COLUMN matched_deposit_id INTEGER",
        # Generic supplier fields (shared across adapters): item_type
        # distinguishes instantly-delivered "account" products from "slot"
        # products (which only create a pending request for the seller to
        # fulfill), plus the seller/category tags a supplier's listing exposes.
        "ALTER TABLE api_products ADD COLUMN external_item_type VARCHAR(20)",
        "ALTER TABLE api_products ADD COLUMN external_seller VARCHAR(255)",
        "ALTER TABLE api_products ADD COLUMN external_category VARCHAR(100)",

        # AI Center Buyer supplier integration (canboso.com/api/telegram-buyer):
        # slot-vs-account distinction, USD pricing, and per-product purchase
        # requirements (customer email / slot months) surfaced by its own
        # products endpoint (independent of the generic fields above).
        "ALTER TABLE api_products ADD COLUMN external_is_slot_product BOOLEAN",
        "ALTER TABLE api_products ADD COLUMN external_slot_durations TEXT",
        "ALTER TABLE api_products ADD COLUMN external_requires_customer_email BOOLEAN",
        "ALTER TABLE api_products ADD COLUMN external_requires_slot_months BOOLEAN",
        "ALTER TABLE api_products ADD COLUMN external_currency VARCHAR(20)",
        "ALTER TABLE api_products ADD COLUMN external_usd_price FLOAT",

        "ALTER TABLE telegram_bot_config ADD COLUMN notify_new_products BOOLEAN DEFAULT 1",
        "ALTER TABLE telegram_bot_config ADD COLUMN notify_restock BOOLEAN DEFAULT 1",
        "ALTER TABLE telegram_bot_config ADD COLUMN broadcast_batch_size INTEGER DEFAULT 25",
        "ALTER TABLE telegram_bot_config ADD COLUMN broadcast_delay_ms INTEGER DEFAULT 300",

        # ── Order search / issue reporting / wallet refunds ─────────────────
        # Warranty snapshot at purchase time + refund bookkeeping on Order.
        "ALTER TABLE orders ADD COLUMN warranty_days INTEGER",
        "ALTER TABLE orders ADD COLUMN refunded_amount FLOAT DEFAULT 0.0",
        "ALTER TABLE orders ADD COLUMN refunded_at DATETIME",
        "ALTER TABLE orders ADD COLUMN refunded_by VARCHAR(100)",
        """CREATE TABLE IF NOT EXISTS order_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            telegram_user_id VARCHAR(50) NOT NULL,
            telegram_chat_id VARCHAR(50),
            issue_text TEXT,
            media_type VARCHAR(20),
            telegram_file_id VARCHAR(300),
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            calculated_refund_amount FLOAT,
            calculated_refund_currency VARCHAR(10),
            created_at DATETIME,
            handled_by VARCHAR(100),
            handled_at DATETIME,
            resolution_note TEXT,
            FOREIGN KEY(order_id) REFERENCES orders(id),
            FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_id)
        )""",
        "CREATE INDEX IF NOT EXISTS ix_order_issues_order ON order_issues (order_id)",
        "CREATE INDEX IF NOT EXISTS ix_order_issues_user ON order_issues (telegram_user_id)",

        # ── Membership rank system ("Cấp bậc") ──────────────────────────────
        """CREATE TABLE IF NOT EXISTS ranks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(100) NOT NULL,
            emoji VARCHAR(20) NOT NULL DEFAULT '🏅',
            min_spend FLOAT NOT NULL DEFAULT 0.0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            created_at DATETIME,
            updated_at DATETIME
        )""",
        "ALTER TABLE users ADD COLUMN rank_id INTEGER",

        # ── Automatic product sync / restock notification dedup ledger ──────
        """CREATE TABLE IF NOT EXISTS notification_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type VARCHAR(30) NOT NULL,
            product_id INTEGER,
            source_id INTEGER,
            previous_stock INTEGER,
            current_stock INTEGER,
            added_quantity INTEGER,
            event_key VARCHAR(150) NOT NULL,
            created_at DATETIME,
            sent_at DATETIME,
            status VARCHAR(20) DEFAULT 'sent',
            FOREIGN KEY(product_id) REFERENCES products(id)
        )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_events_key ON notification_events (event_key)",
        "CREATE INDEX IF NOT EXISTS ix_notification_events_product ON notification_events (product_id)",

        # ── Auto price-adjustment ("giữ nguyên phần chênh lệch") ─────────────
        "ALTER TABLE products ADD COLUMN source_price FLOAT",
        "ALTER TABLE products ADD COLUMN price_margin FLOAT",
        "ALTER TABLE products ADD COLUMN auto_adjust_price BOOLEAN DEFAULT 0",
        "ALTER TABLE products ADD COLUMN last_source_price FLOAT",
        "ALTER TABLE products ADD COLUMN last_sale_price FLOAT",
        "ALTER TABLE products ADD COLUMN last_price_updated_at DATETIME",
        "ALTER TABLE products ADD COLUMN min_sale_price FLOAT",
        "ALTER TABLE products ADD COLUMN max_sale_price FLOAT",
        "ALTER TABLE products ADD COLUMN require_admin_approval_above_percent FLOAT",
        "ALTER TABLE products ADD COLUMN price_pending_approval BOOLEAN DEFAULT 0",
        "ALTER TABLE products ADD COLUMN pending_new_source_price FLOAT",
        "ALTER TABLE telegram_bot_config ADD COLUMN notify_users_on_price_change BOOLEAN DEFAULT 0",
        # Admin-only price-change alerts (customers are never notified —
        # that logic was fully removed, not just gated off).
        "ALTER TABLE telegram_bot_config ADD COLUMN notify_admin_on_price_change BOOLEAN DEFAULT 1",
        """CREATE TABLE IF NOT EXISTS product_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            source_connection_id INTEGER,
            old_source_price FLOAT,
            new_source_price FLOAT,
            old_sale_price FLOAT,
            new_sale_price FLOAT,
            margin FLOAT,
            change_type VARCHAR(30) NOT NULL,
            changed_by VARCHAR(100),
            created_at DATETIME,
            FOREIGN KEY(product_id) REFERENCES products(id),
            FOREIGN KEY(source_connection_id) REFERENCES api_connections(id)
        )""",
        "CREATE INDEX IF NOT EXISTS ix_product_price_history_product ON product_price_history (product_id)",

        # ── Multi-tenant rental support ──────────────────────────────────────
        # Every AdminUser IS a tenant (see models.AdminUser / tenancy.py).
        "ALTER TABLE admin_users ADD COLUMN is_owner BOOLEAN DEFAULT 0",
        "ALTER TABLE admin_users ADD COLUMN expires_at DATETIME",
        "ALTER TABLE admin_users ADD COLUMN display_name VARCHAR(255)",
        "ALTER TABLE admin_users ADD COLUMN notes TEXT",
        # tenant_id on every tenant-scoped table (see models.TenantScopedMixin
        # subclasses). Nullable — backfilled to the owner's id right after
        # this migration list runs (see _backfill_tenant_ids below).
        "ALTER TABLE settings ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE telegram_bot_config ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE payment_methods ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE sepay_config ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE users ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE ranks ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE emoji_icons ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE products ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE restock_subscriptions ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE inventory_items ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE api_connections ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE api_products ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE product_sources ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE orders ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE payment_transactions ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE crypto_transactions ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE wallet_transactions ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE wallet_deposits ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE api_clients ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE api_request_logs ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE order_source_attempts ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE order_issues ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE activity_logs ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE notification_events ADD COLUMN tenant_id INTEGER",
        "ALTER TABLE product_price_history ADD COLUMN tenant_id INTEGER",
        "CREATE INDEX IF NOT EXISTS ix_products_tenant ON products (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_orders_tenant ON orders (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_users_tenant ON users (tenant_id)",

        # ── Ví chợ ("market wallet") — task #4 ──────────────────────────────
        "ALTER TABLE admin_users ADD COLUMN market_wallet_balance FLOAT DEFAULT 0.0",
        "ALTER TABLE orders ADD COLUMN market_wallet_debited BOOLEAN DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS market_wallet_deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_user_id INTEGER NOT NULL,
            currency VARCHAR(10) NOT NULL,
            amount FLOAT NOT NULL,
            vnd_credit_amount FLOAT,
            method VARCHAR(50),
            reference_code VARCHAR(50),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            admin_note TEXT,
            created_at DATETIME,
            confirmed_at DATETIME,
            confirmed_by VARCHAR(100),
            network VARCHAR(50),
            receiving_address VARCHAR(200),
            external_transaction_id VARCHAR(255),
            confirmations INTEGER DEFAULT 0,
            required_confirmations INTEGER,
            raw_transaction_data TEXT,
            expires_at DATETIME,
            detected_at DATETIME,
            verified_at DATETIME,
            credited_at DATETIME,
            failed_reason TEXT,
            FOREIGN KEY(admin_user_id) REFERENCES admin_users(id)
        )""",
        "CREATE INDEX IF NOT EXISTS ix_market_wallet_deposits_admin ON market_wallet_deposits (admin_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_market_wallet_deposits_ref ON market_wallet_deposits (reference_code)",
        "CREATE INDEX IF NOT EXISTS ix_market_wallet_deposits_extid ON market_wallet_deposits (external_transaction_id)",
        """CREATE TABLE IF NOT EXISTS market_wallet_withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_user_id INTEGER NOT NULL,
            currency VARCHAR(10) NOT NULL,
            amount FLOAT NOT NULL,
            account_info TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            admin_note TEXT,
            created_at DATETIME,
            reviewed_at DATETIME,
            reviewed_by VARCHAR(100),
            paid_at DATETIME,
            FOREIGN KEY(admin_user_id) REFERENCES admin_users(id)
        )""",
        "CREATE INDEX IF NOT EXISTS ix_market_wallet_withdrawals_admin ON market_wallet_withdrawals (admin_user_id)",
        """CREATE TABLE IF NOT EXISTS market_wallet_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_user_id INTEGER NOT NULL,
            currency VARCHAR(10) NOT NULL,
            tx_type VARCHAR(20) NOT NULL,
            amount FLOAT NOT NULL,
            balance_before FLOAT NOT NULL,
            balance_after FLOAT NOT NULL,
            order_id INTEGER,
            deposit_id INTEGER,
            withdrawal_id INTEGER,
            note TEXT,
            actor VARCHAR(100),
            created_at DATETIME,
            FOREIGN KEY(admin_user_id) REFERENCES admin_users(id)
        )""",
        "CREATE INDEX IF NOT EXISTS ix_market_wallet_transactions_admin ON market_wallet_transactions (admin_user_id)",
        "ALTER TABLE crypto_transactions ADD COLUMN matched_market_deposit_id INTEGER",
        "ALTER TABLE payment_transactions ADD COLUMN matched_market_deposit_id INTEGER",
        # "Chợ dùng chung nguồn API của admin" — see services/shared_catalog.py
        "ALTER TABLE api_connections ADD COLUMN is_shared_with_tenants BOOLEAN DEFAULT 0",
        "ALTER TABLE product_sources ADD COLUMN shared_from_admin BOOLEAN DEFAULT 0",
        # Ví chợ bank-transfer (SePay) deposits — reference code in transfer content
        "ALTER TABLE market_wallet_deposits ADD COLUMN payment_content VARCHAR(100)",
        # Delivery message tracking so "🛍 Mua tiếp" can clean up the whole
        # purchase thread before showing a fresh product list.
        "ALTER TABLE orders ADD COLUMN delivery_message_id INTEGER",
        "ALTER TABLE orders ADD COLUMN delivery_file_message_id INTEGER",
    ]
    with engine.connect() as conn:
        ran_language_selected_migration = False
        ran_price_usdt_migration = False
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
                if "language_selected" in sql:
                    ran_language_selected_migration = True
                if "price_usdt" in sql:
                    ran_price_usdt_migration = True
            except Exception:
                pass  # column / index already exists

        # Backfill price_usdt for any product where it's still 0 despite a
        # non-zero sale_price — covers products that predate the column (they
        # got the ALTER TABLE default of 0.0, not a real computed value) and
        # any product created directly in the DB outside the admin UI. Safe
        # to run on every startup: never overwrites an already-computed value.
        try:
            from services.exchange_rate_service import get_exchange_config
            from services.normalize import compute_price_usdt
            db = SessionLocal()
            try:
                from models import Product
                rate = float(get_exchange_config(db).get("fixed_rate") or 26500.0)
                stale = db.query(Product).filter(Product.price_usdt == 0.0, Product.sale_price > 0).all()
                for product in stale:
                    product.price_usdt = compute_price_usdt(product.sale_price, rate)
                if stale:
                    db.commit()
            finally:
                db.close()
        except Exception:
            logger.exception("price_usdt backfill failed")

        # Backward-compat: existing products stored with the old plain "manual"
        # delivery_mode become "manual_admin" (no inventory, admin delivers by hand).
        # This keeps their behavior identical to before this feature was added.
        try:
            conn.execute(text("UPDATE products SET delivery_mode = 'manual_admin' WHERE delivery_mode = 'manual'"))
            conn.commit()
        except Exception:
            pass

        # Grandfather in existing users: they've already been using the bot,
        # so don't suddenly force a language picker on them. Only brand-new
        # rows created after this migration start with language_selected=0.
        if ran_language_selected_migration:
            try:
                conn.execute(text("UPDATE users SET language_selected = 1"))
                conn.commit()
            except Exception:
                pass

        # Wallet deposits predating auto-verification used "confirmed"/
        # "rejected" for the manual-admin flow — remap them onto the new
        # terminal status names so existing history still loads correctly
        # under the current WalletDepositStatus enum.
        try:
            conn.execute(text("UPDATE wallet_deposits SET status = 'credited' WHERE status = 'confirmed'"))
            conn.execute(text("UPDATE wallet_deposits SET status = 'failed' WHERE status = 'rejected'"))
            conn.commit()
        except Exception:
            pass

        # Backfill source_price/price_margin for existing products from their
        # already-known data: the primary (lowest-priority) active
        # ProductSource.last_cost for API-linked products. auto_adjust_price
        # defaults to False, so nothing about existing sale prices changes —
        # this only seeds the fields so admins can opt in per product.
        try:
            db = SessionLocal()
            try:
                from models import Product, ProductSource
                candidates = db.query(Product).filter(Product.source_price.is_(None)).all()
                for product in candidates:
                    src = (
                        db.query(ProductSource)
                        .filter(ProductSource.product_id == product.id, ProductSource.is_active == True)
                        .order_by(ProductSource.priority.asc(), ProductSource.id.asc())
                        .first()
                    )
                    cost = src.last_cost if src else None
                    product.source_price = cost if cost is not None else 0.0
                    product.price_margin = float(round((product.sale_price or 0.0) - product.source_price))
                if candidates:
                    db.commit()
                    logger.info(f"PRICE_ADJUST_BACKFILL: initialized source_price/price_margin for {len(candidates)} products")
            finally:
                db.close()
        except Exception:
            logger.exception("price_margin backfill failed")

        # Backfill translation bookkeeping for existing products (added by
        # this migration) without touching any existing name/description
        # text: mark rows that already have a translated counterpart as
        # "translated" with a hash of their current Vietnamese description,
        # everything else as "pending" so the next save/sync/view fills it
        # in normally. source_language defaults to 'vi' (existing shop data
        # is Vietnamese-authored).
        try:
            import hashlib
            db = SessionLocal()
            try:
                from models import Product
                candidates = db.query(Product).filter(Product.translation_status.is_(None)).all()
                backfilled = 0
                for product in candidates:
                    product.source_language = product.source_language or "vi"
                    if product.description and product.description_en:
                        product.translation_status = "translated"
                        product.translation_source_hash = hashlib.sha256(
                            product.description.encode("utf-8")
                        ).hexdigest()
                        product.translated_at = product.updated_at
                    else:
                        product.translation_status = "pending"
                    backfilled += 1
                if candidates:
                    db.commit()
                    logger.info(f"TRANSLATION_BOOKKEEPING_BACKFILL: initialized {backfilled} products")
            finally:
                db.close()
        except Exception:
            logger.exception("translation bookkeeping backfill failed")


def _fix_legacy_unique_constraints():
    """
    Pre-multi-tenant, several columns had a table-wide UNIQUE constraint
    that's now wrong: two tenants must be able to reuse the same Setting.key,
    PaymentMethod.method_code, Product.product_code, or have their own
    customers reuse the same real Telegram account (User.telegram_id) across
    tenants. SQLite bakes an
    inline UNIQUE into the table's own CREATE TABLE statement as a
    constraint-backed index that plain DROP INDEX refuses to touch — the
    only way to remove it is the standard SQLite table-rebuild dance:
    rename the old table aside, let SQLAlchemy create a fresh one from the
    current model (no longer unique=True), copy every row across by the
    columns the two versions share, drop the old table, then add the real
    (tenant_id, column) composite unique index.
    """
    targets = [
        ("settings", "key", "ux_settings_tenant_key"),
        ("payment_methods", "method_code", "ux_payment_methods_tenant_code"),
        ("products", "product_code", "ux_products_tenant_code"),
        ("users", "telegram_id", "ux_users_tenant_telegram_id"),
    ]
    with engine.connect() as conn:
        for table, col, new_index_name in targets:
            try:
                # Already rebuilt (composite index exists) on a previous boot? skip.
                existing_indexes = [r[1] for r in conn.execute(text(f"PRAGMA index_list({table})")).fetchall()]
                if new_index_name in existing_indexes:
                    continue

                old_cols = [r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()]
                if not old_cols:
                    continue  # table doesn't exist yet on a brand-new DB

                old_table = f"{table}_pretenant_old"
                conn.execute(text(f"DROP TABLE IF EXISTS {old_table}"))
                conn.execute(text(f"ALTER TABLE {table} RENAME TO {old_table}"))
                conn.commit()

                # SQLite keeps plain (non-constraint) indexes attached to a
                # table across a rename, under their original name — e.g.
                # `ix_settings_id` now points at settings_pretenant_old. Drop
                # those so the fresh table below can recreate them under the
                # same names. The constraint-backed unique index can't be
                # dropped this way (same error as before), but that's fine:
                # it's going away for good once old_table is dropped below.
                for row in conn.execute(text(f"PRAGMA index_list({old_table})")).fetchall():
                    try:
                        conn.execute(text(f"DROP INDEX IF EXISTS {row[1]}"))
                        conn.commit()
                    except Exception:
                        conn.rollback()

                # Recreate the table from the current SQLAlchemy model — this
                # table no longer declares `unique=True` on the target column.
                Base.metadata.tables[table].create(bind=engine, checkfirst=True)

                new_cols = [r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()]
                shared_cols = [c for c in new_cols if c in old_cols]
                cols_sql = ", ".join(shared_cols)
                conn.execute(text(f"INSERT INTO {table} ({cols_sql}) SELECT {cols_sql} FROM {old_table}"))
                conn.execute(text(f"DROP TABLE {old_table}"))
                conn.execute(text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {new_index_name} ON {table} (tenant_id, {col})"
                ))
                conn.commit()
                logger.info(f"TENANCY_REBUILT_TABLE_FOR_COMPOSITE_UNIQUE: {table}.{col}")
            except Exception:
                conn.rollback()
                logger.exception(f"TENANCY_UNIQUE_INDEX_FIX_FAILED: {table}.{col}")


def _backfill_tenant_ids():
    """
    Assign every pre-existing NULL tenant_id row to the platform owner (the
    original single admin account, created before multi-tenant support
    existed). Must run AFTER the owner AdminUser row is guaranteed to exist
    and be marked is_owner=True (see lifespan startup order).
    """
    from models import AdminUser
    tenant_tables = [
        "settings", "telegram_bot_config", "payment_methods", "sepay_config",
        "users", "ranks", "emoji_icons", "products", "restock_subscriptions",
        "inventory_items", "api_connections", "api_products", "product_sources",
        "orders", "payment_transactions", "crypto_transactions",
        "wallet_transactions", "wallet_deposits", "api_clients",
        "api_request_logs", "order_source_attempts", "order_issues",
        "activity_logs", "notification_events", "product_price_history",
    ]
    db = SessionLocal()
    try:
        owner = db.query(AdminUser).filter(AdminUser.is_owner == True).order_by(AdminUser.id.asc()).first()
        if not owner:
            return
        with engine.connect() as conn:
            for table in tenant_tables:
                try:
                    conn.execute(text(f"UPDATE {table} SET tenant_id = :tid WHERE tenant_id IS NULL"), {"tid": owner.id})
                    conn.commit()
                except Exception:
                    logger.exception(f"TENANCY_BACKFILL_FAILED: {table}")
    finally:
        db.close()


def _seed_payment_methods():
    """Insert default PaymentMethod rows if not present."""
    from models import PaymentMethod
    db = SessionLocal()
    try:
        defaults = [
            ("binance_pay",  "🟡 Binance Pay",  "🟡 Binance Pay",  False),
            ("usdt_bep20",   "🟨 USDT BEP20",   "🟨 USDT BEP20",   False),
            ("usdt_trc20",   "🔴 USDT TRC20",   "🔴 USDT TRC20",   False),
            ("usdt_erc20",   "🔵 USDT ERC20",   "🔵 USDT ERC20",   False),
        ]
        for code, vi, en, active in defaults:
            exists = db.query(PaymentMethod).filter(PaymentMethod.method_code == code).first()
            if not exists:
                db.add(PaymentMethod(
                    method_code=code,
                    display_name_vi=vi,
                    display_name_en=en,
                    is_active=active,
                ))
        db.commit()
    finally:
        db.close()


def _seed_ranks():
    """Insert default membership rank ("Cấp bậc") rows if the table is empty.
    Purely a first-boot convenience — admins can rename/re-threshold/reorder/
    disable any of these from Web Admin afterwards without touching code."""
    from models import Rank
    db = SessionLocal()
    try:
        if db.query(Rank).count() > 0:
            return
        defaults = [
            ("🥉", "Thành viên mới",       0),
            ("🥈", "Đồng",                  500_000),
            ("🥇", "Bạc",                   2_000_000),
            ("💎", "Vàng",                  5_000_000),
            ("👑", "Bạch Kim",              10_000_000),
            ("⚜️", "Kim Cương",             20_000_000),
            ("🏆", "Đại lý",                50_000_000),
            ("🚀", "Đại lý VIP",            100_000_000),
            ("🔥", "Tổng đại lý",           200_000_000),
            ("💠", "Nhà phân phối",         500_000_000),
            ("🌟", "Nhà phân phối cấp cao", 1_000_000_000),
            ("👑", "Đối tác chiến lược",    2_000_000_000),
        ]
        for i, (emoji, name, min_spend) in enumerate(defaults):
            db.add(Rank(name=name, emoji=emoji, min_spend=min_spend, sort_order=i, is_active=True))
        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info("APP_STARTING")
    Base.metadata.create_all(bind=engine)
    _run_migrations()
    UPLOADS_DIR.mkdir(exist_ok=True)
    logger.info("DATABASE_READY")

    # A freshly-started process never has a live bot task yet, regardless of
    # whatever status was persisted from a previous process's lifetime — reset
    # it so the admin UI doesn't show a stale "running"/"reconnecting" badge
    # before the auto-start block below (if any) sets the real status.
    from models import TelegramBotConfig as _TBC, BotStatus as _BS
    db0 = SessionLocal()
    try:
        cfg0 = db0.query(_TBC).first()
        if cfg0 and cfg0.bot_status != _BS.stopped:
            cfg0.bot_status = _BS.stopped
            db0.commit()
    finally:
        db0.close()

    # Create default admin user if none exists. This first-ever admin account
    # is always the platform owner (see models.AdminUser / tenancy.py) —
    # every additional tenant account is created afterwards from /tenants.
    db = SessionLocal()
    try:
        if db.query(AdminUser).count() == 0:
            admin = AdminUser(
                username=ADMIN_USERNAME,
                password_hash=hash_password(ADMIN_PASSWORD),
                is_active=True,
                is_owner=True,
            )
            db.add(admin)
            db.commit()
            print(f"[INFO] Admin user created: {ADMIN_USERNAME}")
        elif db.query(AdminUser).filter(AdminUser.is_owner == True).count() == 0:
            # Pre-multi-tenant DB being upgraded: the earliest admin account
            # becomes the owner.
            first = db.query(AdminUser).order_by(AdminUser.id.asc()).first()
            if first:
                first.is_owner = True
                db.commit()
    finally:
        db.close()

    # Multi-tenant foundation: every pre-existing row belongs to the owner,
    # and the three columns that used to be globally unique (Setting.key,
    # PaymentMethod.method_code, Product.product_code) need their unique
    # index rebuilt as (tenant_id, column) — see tenancy.py for why.
    _backfill_tenant_ids()
    _fix_legacy_unique_constraints()

    owner_tenant_id = tenancy.get_owner_tenant_id()

    # Everything below this point (seeding, schedulers, background workers,
    # the bot) runs with no HTTP request to derive a tenant from, so it's
    # explicitly scoped to the owner tenant. Today the owner is the only
    # tenant with a running bot/worker set — per-tenant bot & worker
    # multiplexing (one set per rented-out tenant) is deferred follow-up
    # work; see replit.md. asyncio.create_task() captures the current
    # contextvars context at creation time, so tasks created inside this
    # `with` block stay scoped to owner_tenant_id for their entire lifetime.
    tenant_scope_token = tenancy.tenant_scope(owner_tenant_id)
    tenant_scope_token.__enter__()

    _seed_payment_methods()
    _seed_ranks()

    # Start API sync schedulers
    from models import ApiConnection
    from services.api_service import start_sync_scheduler
    db2 = SessionLocal()
    try:
        connections = db2.query(ApiConnection).filter(ApiConnection.is_active == True).all()
        for conn in connections:
            start_sync_scheduler(conn.id, conn.sync_interval_minutes)
        logger.info(f"SYNC_SCHEDULER_STARTED: {len(connections)} connection(s)")
    finally:
        db2.close()

    # Start payment expiry background loop
    from services.payment_service import expire_payment_orders_loop
    _background_tasks.append(asyncio.create_task(expire_payment_orders_loop()))

    # Start crypto monitor workers (each independent — one crash won't affect others)
    from services.crypto_monitor import (
        bep20_monitor_loop, trc20_monitor_loop, erc20_monitor_loop, binance_pay_loop,
        expire_wallet_deposits_loop,
    )
    _background_tasks.append(asyncio.create_task(bep20_monitor_loop()))
    _background_tasks.append(asyncio.create_task(trc20_monitor_loop()))
    _background_tasks.append(asyncio.create_task(erc20_monitor_loop()))
    _background_tasks.append(asyncio.create_task(binance_pay_loop()))
    _background_tasks.append(asyncio.create_task(expire_wallet_deposits_loop()))

    # Auto-start every tenant's Telegram bot that's configured + enabled, so
    # they all come back up on their own after a restart/redeploy without an
    # admin visiting the web UI. Each tenant gets its own BotManager (see
    # services/bot_service.py) so they run concurrently and independently —
    # one tenant's missing/invalid token only leaves THAT bot in "error"
    # state, it doesn't block or get confused with anyone else's bot.
    from models import TelegramBotConfig
    from services.bot_service import get_bot_manager
    from crypto import decrypt
    db3 = SessionLocal()
    try:
        cfgs = (
            db3.query(TelegramBotConfig)
            .execution_options(skip_tenant_filter=True)
            .filter(TelegramBotConfig.is_enabled == True)
            .all()
        )
        started = 0
        for cfg in cfgs:
            token = decrypt(cfg.bot_token_encrypted) if cfg.bot_token_encrypted else ""
            if not token:
                logger.warning(f"TELEGRAM_BOT_AUTOSTART_SKIPPED tenant={cfg.tenant_id}: bot enabled but no token configured")
                continue
            with tenancy.tenant_scope(cfg.tenant_id):
                await get_bot_manager(cfg.tenant_id).start_bot(token)
            started += 1
        logger.info(f"TELEGRAM_BOT_AUTOSTART: {started}/{len(cfgs)} tenant bot(s) started")
    finally:
        db3.close()

    tenant_scope_token.__exit__(None, None, None)

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    from services.bot_service import get_all_bot_managers
    for _tenant_id, _mgr in get_all_bot_managers().items():
        if _mgr.is_running():
            await _mgr.stop_bot()

    from services.api_service import stop_all_sync_schedulers
    stop_all_sync_schedulers()

    for task in _background_tasks:
        if not task.done():
            task.cancel()
    for task in _background_tasks:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
    logger.info("APP_SHUTDOWN_COMPLETE")


app = FastAPI(title="AI Center Web Bot Manager", lifespan=lifespan)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.middleware("http")
async def tenant_context_middleware(request: Request, call_next):
    """
    Sets the current-tenant contextvar (see tenancy.py) from the logged-in
    admin's session on every request, so every ORM query for the rest of
    this request is automatically scoped to that admin's own data. Also
    enforces rental expiry: once an account's expires_at has passed, it's
    auto-locked (is_active flips False) on its next request and the session
    is cleared — no separate scheduled job needed. Must run AFTER
    SessionMiddleware has parsed request.session (see the add_middleware
    order below: SessionMiddleware is added *after* this function, which
    makes it the outer layer that runs first).
    """
    from datetime import datetime as _dt
    from tenancy import set_current_tenant, reset_current_tenant
    from models import AdminUser as _AdminUser

    tenant_id = None
    request.state.is_owner = False
    admin_id = request.session.get("admin_id")
    if admin_id:
        db = SessionLocal()
        try:
            admin = db.query(_AdminUser).filter(_AdminUser.id == admin_id).first()
            if admin:
                if admin.is_active and admin.expires_at and admin.expires_at < _dt.utcnow():
                    admin.is_active = False
                    db.commit()
                if admin.is_active:
                    tenant_id = admin.id
                    request.state.is_owner = admin.is_owner
                else:
                    request.session.clear()
        finally:
            db.close()

    token = set_current_tenant(tenant_id)
    try:
        response = await call_next(request)
    finally:
        reset_current_tenant(token)
    return response


@app.middleware("http")
async def api_request_logger(request, call_next):
    """
    Logs every inbound /api/v1/* request once it's identified to a client
    (see routers/customer_api.require_api_client, which sets
    request.state.api_client_id as soon as the key resolves — before any
    locked/revoked/rate-limit check, so those rejections get logged too).
    Unresolvable keys (unknown/missing) are never attributed to a client
    and are intentionally not logged here.
    """
    response = await call_next(request)
    client_id = getattr(request.state, "api_client_id", None)
    if client_id:
        try:
            from models import ApiRequestLog
            db = SessionLocal()
            try:
                db.add(ApiRequestLog(
                    api_client_id=client_id,
                    method=request.method,
                    endpoint=request.url.path,
                    status_code=response.status_code,
                    ip_address=request.client.host if request.client else None,
                ))
                db.commit()
            finally:
                db.close()
        except Exception:
            logger.exception("api_request_logger failed")
    return response


@app.middleware("http")
async def _no_cache_admin_pages(request: Request, call_next):
    """
    Admin pages are session-gated and change on every action (add/edit
    connection, sync, etc). Without explicit no-store headers, mobile
    browsers/intermediate proxies can serve a stale cached HTML snapshot
    after a real change (e.g. showing a connection as saved when it no
    longer is), which looks exactly like a data-loss bug from the user's
    side. Static assets (/static/...) are intentionally excluded so they
    stay cacheable.
    """
    response = await call_next(request)
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=86400 * 30)
app.add_middleware(GZipMiddleware)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
UPLOADS_DIR.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

from routers import auth, dashboard, products, orders, api_connections, users, settings, wallet
from routers import webhooks  # public endpoints — no session auth
from routers import api_clients, customer_api
from routers import ranks
from routers import emoji_icons
from routers import github_webhook
from routers import tenants  # owner-only tenant account management
from routers import market_wallet  # ví chợ (nạp/rút) for tenants & owner review

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(products.router)
app.include_router(orders.router)
app.include_router(api_connections.router)
app.include_router(users.router)
app.include_router(settings.router)
app.include_router(wallet.router)
app.include_router(api_clients.router)
app.include_router(webhooks.router)  # POST /webhooks/sepay
app.include_router(customer_api.router)  # public inbound customer REST API (X-API-Key auth)
app.include_router(ranks.router)
app.include_router(emoji_icons.router)
app.include_router(github_webhook.router)  # POST /github-webhook — VPS auto-deploy (public, HMAC-signed)
app.include_router(tenants.router)  # owner-only tenant account management
app.include_router(market_wallet.router)  # ví chợ (nạp/rút) for tenants & owner review


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
