import os
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

BASE_DIR = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"


def _run_migrations():
    """Add new columns to existing tables without dropping data."""
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
        # Order new fields
        "ALTER TABLE orders ADD COLUMN source_unit_price FLOAT",
        "ALTER TABLE orders ADD COLUMN external_order_code VARCHAR(255)",
        "ALTER TABLE orders ADD COLUMN delivery_items TEXT",
        "ALTER TABLE orders ADD COLUMN partial_count INTEGER",
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                # Column already exists — safe to ignore
                pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    Base.metadata.create_all(bind=engine)
    _run_migrations()
    UPLOADS_DIR.mkdir(exist_ok=True)

    db = SessionLocal()
    try:
        admin_count = db.query(AdminUser).count()
        if admin_count == 0:
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

    # Start sync schedulers for active API connections
    from models import ApiConnection
    from services.api_service import start_sync_scheduler
    db2 = SessionLocal()
    try:
        connections = db2.query(ApiConnection).filter(ApiConnection.is_active == True).all()
        for conn in connections:
            start_sync_scheduler(conn.id, conn.sync_interval_minutes)
    finally:
        db2.close()

    yield
    # Shutdown
    from services.bot_service import bot_manager
    if bot_manager.is_running():
        await bot_manager.stop_bot()


app = FastAPI(title="AI Center Web Bot Manager", lifespan=lifespan)

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=86400 * 30)
app.add_middleware(GZipMiddleware)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
UPLOADS_DIR.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

from routers import auth, dashboard, products, orders, api_connections, users, settings

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(products.router)
app.include_router(orders.router)
app.include_router(api_connections.router)
app.include_router(users.router)
app.include_router(settings.router)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
