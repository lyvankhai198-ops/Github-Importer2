from pathlib import Path
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from models import WalletDeposit, WalletDepositStatus, WalletCurrency, WalletTxType, User
from services import wallet_service
from services.bot_service import bot_manager

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def check_auth(request: Request):
    return request.session.get("admin_id")


def flash(request: Request, msg: str, type: str = "success"):
    request.session["flash"] = {"type": type, "msg": msg}


@router.get("/wallet", response_class=HTMLResponse)
async def wallet_deposits_list(request: Request, db: Session = Depends(get_db), status: str = "pending"):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)

    q = db.query(WalletDeposit)
    if status in ("pending", "confirmed", "rejected", "cancelled"):
        q = q.filter(WalletDeposit.status == WalletDepositStatus(status))
    deposits = q.order_by(WalletDeposit.created_at.desc()).limit(200).all()

    # Attach a display username for each deposit's shopper.
    user_map = {}
    for d in deposits:
        if d.telegram_user_id not in user_map:
            user_map[d.telegram_user_id] = db.query(User).filter(User.telegram_id == d.telegram_user_id).first()

    pending_count = db.query(WalletDeposit).filter(WalletDeposit.status == WalletDepositStatus.pending).count()
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "wallet.html", {
        
        "deposits": deposits,
        "user_map": user_map,
        "status": status,
        "pending_count": pending_count,
        "flash": flash_msg,
    })


@router.post("/wallet/{deposit_id}/confirm")
async def confirm_deposit(deposit_id: int, request: Request, db: Session = Depends(get_db),
                           admin_note: str = Form("")):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)

    from datetime import datetime
    deposit = db.query(WalletDeposit).filter(WalletDeposit.id == deposit_id).first()
    if not deposit:
        flash(request, "Yêu cầu nạp tiền không tồn tại!", "error")
        return RedirectResponse(url="/wallet", status_code=302)
    if deposit.status != WalletDepositStatus.pending:
        flash(request, "Yêu cầu này đã được xử lý!", "error")
        return RedirectResponse(url="/wallet", status_code=302)

    admin_id = request.session.get("admin_id", "admin")
    try:
        # The credit and the status flip happen in ONE atomic transaction
        # (extra_updates), guarded by "status = 'pending'" — so a
        # double-click or a retry after a partial failure can never credit
        # the wallet twice for the same deposit.
        wallet_service.credit_wallet(
            db, deposit.telegram_user_id, deposit.currency, deposit.amount, WalletTxType.deposit,
            deposit_id=deposit.id, note=f"Nạp tiền — {deposit.reference_code}", actor=str(admin_id),
            extra_updates=[(
                "UPDATE wallet_deposits SET status = 'confirmed', admin_note = ?, "
                "confirmed_at = ?, confirmed_by = ? WHERE id = ? AND status = 'pending'",
                (admin_note, datetime.utcnow().isoformat(sep=" "), str(admin_id), deposit.id),
            )],
        )
        db.refresh(deposit)
        flash(request, f"Đã xác nhận nạp tiền {deposit.reference_code}!")

        if bot_manager.is_running():
            from bot.notifier import notify_user_wallet_deposit_confirmed
            from bot.i18n import get_user_lang
            lang = get_user_lang(db, deposit.telegram_user_id)
            await notify_user_wallet_deposit_confirmed(
                bot_manager._application.bot, deposit.telegram_user_id, deposit, lang=lang,
            )
    except wallet_service.AlreadyProcessedError:
        flash(request, "Yêu cầu này đã được xử lý (có thể do double-click)!", "error")
    except Exception as e:
        flash(request, f"Lỗi xác nhận: {e}", "error")
    return RedirectResponse(url="/wallet", status_code=302)


@router.post("/wallet/{deposit_id}/reject")
async def reject_deposit(deposit_id: int, request: Request, db: Session = Depends(get_db),
                          admin_note: str = Form("")):
    if not check_auth(request):
        return RedirectResponse(url="/login", status_code=302)

    from datetime import datetime
    deposit = db.query(WalletDeposit).filter(WalletDeposit.id == deposit_id).first()
    if not deposit:
        flash(request, "Yêu cầu nạp tiền không tồn tại!", "error")
        return RedirectResponse(url="/wallet", status_code=302)

    admin_id = request.session.get("admin_id", "admin")

    # Guarded, atomic transition — the WHERE status='pending' clause means
    # this can never flip a deposit that a concurrent /confirm already
    # moved out of pending (no reject-after-confirm race), and a stale
    # double-submit of /reject itself is a no-op past the first call.
    rows = db.execute(
        text(
            "UPDATE wallet_deposits SET status = 'rejected', admin_note = :note, "
            "confirmed_at = :ts, confirmed_by = :admin WHERE id = :id AND status = 'pending'"
        ),
        {"note": admin_note, "ts": datetime.utcnow().isoformat(sep=" "), "admin": str(admin_id), "id": deposit_id},
    )
    if rows.rowcount == 0:
        db.rollback()
        flash(request, "Yêu cầu này đã được xử lý!", "error")
        return RedirectResponse(url="/wallet", status_code=302)
    db.commit()
    db.refresh(deposit)
    flash(request, f"Đã từ chối yêu cầu nạp tiền {deposit.reference_code}.")

    if bot_manager.is_running():
        from bot.notifier import notify_user_wallet_deposit_rejected
        from bot.i18n import get_user_lang
        lang = get_user_lang(db, deposit.telegram_user_id)
        await notify_user_wallet_deposit_rejected(
            bot_manager._application.bot, deposit.telegram_user_id, deposit, lang=lang,
        )
    return RedirectResponse(url="/wallet", status_code=302)
