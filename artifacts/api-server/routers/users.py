from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from models import User, Order, WalletCurrency, WalletTxType
from services.bot_service import bot_manager
from services import wallet_service

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def check_auth(request: Request):
    return request.session.get("admin_id")


def flash(request: Request, msg: str, type: str = "success"):
    request.session["flash"] = {"type": type, "msg": msg}


@router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, db: Session = Depends(get_db), search: str = "", page: int = 1):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    q = db.query(User)
    if search:
        q = q.filter(
            User.username.ilike(f"%{search}%") |
            User.first_name.ilike(f"%{search}%") |
            User.telegram_id.ilike(f"%{search}%")
        )
    total = q.count()
    per_page = 20
    users = q.order_by(User.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "users.html", {
        
        "users": users,
        "search": search,
        "page": page,
        "total": total,
        "per_page": per_page,
        "flash": flash_msg,
        "import_result": request.session.pop("import_result", None),
    })


@router.post("/users/import")
async def import_users_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    mode: str = Form("update_info"),
):
    """Import legacy-bot users (chat_id/username/full_name/balance/created_at)
    from a CSV or Excel file, deduped by telegram_id (chat_id). See
    services.user_import for the exact merge rules per mode."""
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)

    from services.user_import import parse_import_file, import_users, VALID_MODES
    if mode not in VALID_MODES:
        flash(request, "Chế độ import không hợp lệ!", "error")
        return RedirectResponse(url="/users", status_code=302)
    if not file or not file.filename:
        flash(request, "Vui lòng chọn file CSV/Excel để import!", "error")
        return RedirectResponse(url="/users", status_code=302)

    try:
        content = await file.read()
        rows = parse_import_file(file.filename, content)
        if not rows:
            flash(request, "File không có dữ liệu người dùng nào!", "error")
            return RedirectResponse(url="/users", status_code=302)
        result = import_users(db, rows, mode)
    except ValueError as e:
        flash(request, f"Lỗi đọc file: {e}", "error")
        return RedirectResponse(url="/users", status_code=302)
    except Exception as e:
        flash(request, f"Import thất bại: {e}", "error")
        return RedirectResponse(url="/users", status_code=302)

    request.session["import_result"] = result
    flash(request, f"Import xong: {result['success']}/{result['total']} thành công, {result['duplicates']} trùng, {result['errors']} lỗi.")
    return RedirectResponse(url="/users", status_code=302)


@router.post("/users/test-send")
async def test_send_message(request: Request, chat_id: str = Form(...)):
    """📢 Gửi tin thử — sends a fixed test message to a chosen Chat ID so
    the admin can confirm the (new) bot token can actually reach a
    carried-over legacy-bot user before running a full broadcast."""
    if not check_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    chat_id = chat_id.strip()
    if not chat_id:
        return JSONResponse({"success": False, "error": "Thiếu Chat ID"}, status_code=400)
    if not bot_manager.is_running():
        return JSONResponse({"success": False, "error": "Bot chưa khởi động!"})
    ok = await bot_manager.send_message(chat_id, "✅ Đây là tin nhắn thử từ bot. Nếu bạn nhận được tin này, bot đã kết nối thành công tới Chat ID của bạn.")
    return JSONResponse({"success": ok, "error": None if ok else "Không gửi được — kiểm tra Chat ID hoặc user đã chặn bot."})


@router.get("/users/{telegram_id}", response_class=HTMLResponse)
async def user_detail(telegram_id: str, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        flash(request, "Người dùng không tồn tại!", "error")
        return RedirectResponse(url="/users", status_code=302)
    orders = db.query(Order).filter(Order.telegram_user_id == telegram_id).order_by(Order.created_at.desc()).all()
    wallet_txs = wallet_service.list_wallet_transactions(db, telegram_id, limit=50)
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "users.html", {
        
        "detail_user": user,
        "user_orders": orders,
        "wallet_txs": wallet_txs,
        "users": [],
        "search": "",
        "page": 1,
        "total": 0,
        "per_page": 20,
        "flash": flash_msg,
    })


@router.post("/users/{telegram_id}/wallet/adjust")
async def adjust_wallet(
    telegram_id: str, request: Request, db: Session = Depends(get_db),
    currency: str = Form(...), direction: str = Form(...),
    amount: float = Form(...), note: str = Form(...),
):
    """Admin manual credit/debit of a customer's wallet. Requires a note,
    is fully ledgered, is atomic (see services.wallet_service), and never
    lets a balance go negative."""
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        flash(request, "Người dùng không tồn tại!", "error")
        return RedirectResponse(url="/users", status_code=302)

    note = (note or "").strip()
    if not note:
        flash(request, "Vui lòng nhập lý do điều chỉnh!", "error")
        return RedirectResponse(url=f"/users/{telegram_id}", status_code=302)
    if currency not in ("VND", "USDT") or direction not in ("credit", "debit") or amount <= 0:
        flash(request, "Dữ liệu điều chỉnh không hợp lệ!", "error")
        return RedirectResponse(url=f"/users/{telegram_id}", status_code=302)

    admin_id = request.session.get("admin_id", "admin")
    try:
        if direction == "credit":
            wallet_service.credit_wallet(
                db, telegram_id, currency, amount, WalletTxType.admin_credit,
                note=note, actor=str(admin_id),
            )
        else:
            wallet_service.debit_wallet(
                db, telegram_id, currency, amount, WalletTxType.admin_debit,
                note=note, actor=str(admin_id),
            )
        flash(request, "Đã điều chỉnh số dư ví!")

        if bot_manager.is_running():
            from bot.notifier import notify_user_wallet_admin_adjustment
            from bot.i18n import get_user_lang
            lang = get_user_lang(db, telegram_id)
            await notify_user_wallet_admin_adjustment(
                bot_manager._application.bot, telegram_id, currency, amount, note,
                is_credit=(direction == "credit"), lang=lang,
            )
    except wallet_service.InsufficientBalanceError as e:
        flash(request, f"Số dư không đủ để trừ: hiện có {e.balance}, cần {e.amount}.", "error")
    except Exception as e:
        flash(request, f"Lỗi điều chỉnh ví: {e}", "error")
    return RedirectResponse(url=f"/users/{telegram_id}", status_code=302)


@router.post("/users/{telegram_id}/ban")
async def ban_user(telegram_id: str, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user:
        user.is_banned = True
        db.commit()
        flash(request, f"Người dùng {telegram_id} đã bị cấm!")
    return RedirectResponse(url=f"/users/{telegram_id}", status_code=302)


@router.post("/users/{telegram_id}/unban")
async def unban_user(telegram_id: str, request: Request, db: Session = Depends(get_db)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user:
        user.is_banned = False
        db.commit()
        flash(request, f"Người dùng {telegram_id} đã được bỏ cấm!")
    return RedirectResponse(url=f"/users/{telegram_id}", status_code=302)


@router.post("/users/{telegram_id}/message")
async def send_message(telegram_id: str, request: Request, db: Session = Depends(get_db), message: str = Form(...)):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)
    if bot_manager.is_running():
        success = await bot_manager.send_message(telegram_id, message)
        if success:
            flash(request, "Tin nhắn đã được gửi!")
        else:
            flash(request, "Không thể gửi tin nhắn!", "error")
    else:
        flash(request, "Bot chưa khởi động!", "error")
    return RedirectResponse(url=f"/users/{telegram_id}", status_code=302)
