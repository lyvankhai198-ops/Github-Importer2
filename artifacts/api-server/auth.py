import bcrypt
from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from models import AdminUser


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def authenticate_admin(db: Session, username: str, password: str):
    user = db.query(AdminUser).filter(
        AdminUser.username == username,
        AdminUser.is_active == True
    ).first()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def create_session(request: Request, admin_id: int):
    request.session["admin_id"] = admin_id


def destroy_session(request: Request):
    request.session.clear()


def require_admin(request: Request, db: Session = Depends(get_db)):
    admin_id = request.session.get("admin_id")
    if not admin_id:
        return RedirectResponse(url="/login", status_code=302)
    admin = db.query(AdminUser).filter(
        AdminUser.id == admin_id,
        AdminUser.is_active == True
    ).first()
    if not admin:
        request.session.clear()
        return RedirectResponse(url="/login", status_code=302)
    return admin


def get_current_admin(request: Request, db: Session = Depends(get_db)):
    admin_id = request.session.get("admin_id")
    if not admin_id:
        return None
    return db.query(AdminUser).filter(
        AdminUser.id == admin_id,
        AdminUser.is_active == True
    ).first()
