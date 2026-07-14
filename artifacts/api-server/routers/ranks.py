from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from models import Rank

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def check_auth(request: Request):
    return request.session.get("admin_id")


def flash(request: Request, msg: str, type: str = "success"):
    request.session["flash"] = {"type": type, "msg": msg}


@router.get("/ranks", response_class=HTMLResponse)
async def ranks_list(request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    ranks = db.query(Rank).order_by(Rank.sort_order.asc(), Rank.min_spend.asc()).all()
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "ranks.html", {
        "ranks": ranks,
        "flash": flash_msg,
    })


@router.post("/ranks/add")
async def add_rank(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    emoji: str = Form("🏅"),
    min_spend: float = Form(0.0),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    max_order = db.query(Rank).count()
    db.add(Rank(name=name.strip(), emoji=emoji.strip() or "🏅", min_spend=max(0.0, min_spend), sort_order=max_order, is_active=True))
    db.commit()
    flash(request, f"Đã thêm cấp bậc \"{name}\".")
    return RedirectResponse(url="/ranks", status_code=302)


@router.post("/ranks/{rank_id}/edit")
async def edit_rank(
    rank_id: int,
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    emoji: str = Form("🏅"),
    min_spend: float = Form(0.0),
):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    rank = db.query(Rank).filter(Rank.id == rank_id).first()
    if not rank:
        flash(request, "Không tìm thấy cấp bậc.", "error")
        return RedirectResponse(url="/ranks", status_code=302)
    rank.name = name.strip()
    rank.emoji = emoji.strip() or "🏅"
    rank.min_spend = max(0.0, min_spend)
    db.commit()
    flash(request, f"Đã lưu cấp bậc \"{rank.name}\".")
    return RedirectResponse(url="/ranks", status_code=302)


@router.post("/ranks/{rank_id}/toggle")
async def toggle_rank(rank_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    rank = db.query(Rank).filter(Rank.id == rank_id).first()
    if rank:
        rank.is_active = not rank.is_active
        db.commit()
        flash(request, f"Cấp bậc \"{rank.name}\" đã {'bật' if rank.is_active else 'tắt'}.")
    return RedirectResponse(url="/ranks", status_code=302)


@router.post("/ranks/{rank_id}/delete")
async def delete_rank(rank_id: int, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    rank = db.query(Rank).filter(Rank.id == rank_id).first()
    if rank:
        from models import User
        db.query(User).filter(User.rank_id == rank.id).update({User.rank_id: None})
        db.delete(rank)
        db.commit()
        flash(request, f"Đã xoá cấp bậc \"{rank.name}\".")
    return RedirectResponse(url="/ranks", status_code=302)


@router.post("/ranks/{rank_id}/move")
async def move_rank(rank_id: int, request: Request, db: Session = Depends(get_db), direction: str = Form(...)):
    """Swap sort_order with the immediate neighbor above/below."""
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    ranks = db.query(Rank).order_by(Rank.sort_order.asc(), Rank.min_spend.asc()).all()
    idx = next((i for i, r in enumerate(ranks) if r.id == rank_id), None)
    if idx is None:
        return RedirectResponse(url="/ranks", status_code=302)
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if 0 <= swap_idx < len(ranks):
        ranks[idx].sort_order, ranks[swap_idx].sort_order = ranks[swap_idx].sort_order, ranks[idx].sort_order
        # Guarantee distinct sort_order values even if defaults collided.
        for i, r in enumerate(sorted(ranks, key=lambda r: (r.sort_order, r.min_spend))):
            r.sort_order = i
        db.commit()
    return RedirectResponse(url="/ranks", status_code=302)
