import os
import logging
import asyncio
import uvicorn
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import text

from config import SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD, PORT
from database import engine, SessionLocal
from models import Base, AdminUser
from auth import hash_password

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info("APP_STARTING")
    Base.metadata.create_all(bind=engine)
    _run_migrations()
    _seed_payment_methods()
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

    # Create default admin user if none exists
    db = SessionLocal()
    try:
        if db.query(AdminUser).count() == 0:
            admin = AdminUser(
                username=ADMIN_USERNAME,
                password_hash=hash_password(ADMIN_PASSWORD),
                is_active=True,
            )
            db.add(admin)
            db.commit()
            print(f"[INFO] Admin user created: {ADMIN_USERNAME}")
    finally:
        db.close()

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

    # Auto-start the Telegram bot if it's configured + enabled, so it comes
    # back up on its own after a restart/redeploy without an admin visiting
    # the web UI. If the token is missing/invalid, the site still starts —
    # bot status simply stays "error" until fixed from /settings.
    from models import TelegramBotConfig
    from services.bot_service import bot_manager
    from crypto import decrypt
    db3 = SessionLocal()
    try:
        cfg = db3.query(TelegramBotConfig).first()
        if cfg and cfg.is_enabled:
            token = decrypt(cfg.bot_token_encrypted) if cfg.bot_token_encrypted else ""
            if token:
                await bot_manager.start_bot(token)
            else:
                logger.warning("TELEGRAM_BOT_AUTOSTART_SKIPPED: bot enabled but no token configured")
    finally:
        db3.close()

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    from services.bot_service import bot_manager as _bm
    if _bm.is_running():
        await _bm.stop_bot()

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


app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=86400 * 30)
app.add_middleware(GZipMiddleware)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
UPLOADS_DIR.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

from routers import auth, dashboard, products, orders, api_connections, users, settings, wallet
from routers import webhooks  # public endpoints — no session auth
from routers import api_clients, customer_api

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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
