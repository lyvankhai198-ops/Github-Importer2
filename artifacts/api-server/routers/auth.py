from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from database import get_db
from auth import authenticate_admin, create_session, destroy_session

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("admin_id"):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    admin = authenticate_admin(db, username, password)
    if not admin:
        return templates.TemplateResponse(request, "login.html", {
            
            "error": "Tên đăng nhập hoặc mật khẩu không đúng."
        })
    create_session(request, admin.id)
    return RedirectResponse(url="/", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    destroy_session(request)
    return RedirectResponse(url="/login", status_code=302)


@router.post("/logout")
async def logout_post(request: Request):
    destroy_session(request)
    return RedirectResponse(url="/login", status_code=302)
