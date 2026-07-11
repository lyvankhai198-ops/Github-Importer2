import os
import uvicorn
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from config import SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD, PORT
from database import engine, SessionLocal
from models import Base, AdminUser
from auth import hash_password

BASE_DIR = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    Base.metadata.create_all(bind=engine)
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
