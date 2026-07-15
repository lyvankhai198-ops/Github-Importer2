"""
Ví chợ ("market wallet") — tenant top-up/withdraw self-service page, plus
the owner's cross-tenant review page (all tenants' balances + deposit/
withdrawal request queues + the owner's own prepaid-to-supplier balance).

See services/market_wallet_service.py and services/market_stock_service.py
for the balance/virtual-stock mechanics this page surfaces.
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import (
    AdminUser, MarketWalletDeposit, MarketWalletWithdrawal, MarketWalletWithdrawalStatus,
    WalletDepositStatus, WalletCurrency, WalletTxType, PaymentMethod, Product, SourceType,
)
from services import market_wallet_service
from tenancy import get_owner_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def check_auth(request: Request, db: Session):
    admin_id = request.session.get("admin_id")
    if not admin_id:
        return None
    return db.query(AdminUser).filter(AdminUser.id == admin_id, AdminUser.is_active == True).first()


def check_owner(request: Request, db: Session):
    admin = check_auth(request, db)
    return admin if admin and admin.is_owner else None


def flash(request: Request, msg: str, type: str = "success"):
    request.session["flash"] = {"type": type, "msg": msg}


def _owner_crypto_payment_display(db: Session, method: str):
    """Owner's configured receiving address for a crypto method — PaymentMethod
    is tenant-scoped, so tenants must look this up bypassing their own scope
    (they never configure their own wallet address; the money always lands
    in the owner's real wallet, see models.py MarketWalletDeposit docstring)."""
    import json
    from crypto import decrypt
    owner_id = get_owner_tenant_id()
    pm = (
        db.query(PaymentMethod)
        .execution_options(skip_tenant_filter=True)
        .filter(
            PaymentMethod.tenant_id == owner_id,
            PaymentMethod.method_code == method,
            PaymentMethod.is_active == True,
        )
        .first()
    )
    if not pm:
        return None
    if method == "binance_pay":
        # get_binance_config() reads PaymentMethod under the CURRENT tenant
        # scope — a non-owner tenant would never see the owner's config that
        # way. Money for ví chợ always lands with the owner (see
        # MarketWalletDeposit docstring), so this must look up the owner's
        # row explicitly, same as the usdt_* branch above.
        import json as _json
        from crypto import decrypt as _decrypt
        pm = (
            db.query(PaymentMethod)
            .execution_options(skip_tenant_filter=True)
            .filter(
                PaymentMethod.tenant_id == owner_id,
                PaymentMethod.method_code == "binance_pay",
                PaymentMethod.is_active == True,
            )
            .first()
        )
        if not pm or not pm.config_encrypted:
            return None
        try:
            bnb_cfg = _json.loads(_decrypt(pm.config_encrypted) or "{}")
        except Exception:
            return None
        if not bnb_cfg.get("api_key") or not bnb_cfg.get("secret_key") or not bnb_cfg.get("receiver_binance_id"):
            return None
        return {
            "network": "BINANCE", "address": bnb_cfg.get("receiver_binance_id"),
            "required_confirmations": None,
            "expiry_minutes": int(bnb_cfg.get("order_expiry_minutes") or 30),
        }
    if not pm.config_encrypted:
        return None
    try:
        cfg = json.loads(decrypt(pm.config_encrypted) or "{}")
    except Exception:
        return None
    network_map = {"usdt_bep20": "BEP20", "usdt_trc20": "TRC20", "usdt_erc20": "ERC20"}
    network = network_map.get(method)
    address = cfg.get("wallet_address")
    if not network or not address:
        return None
    return {
        "network": network, "address": address,
        "required_confirmations": int(cfg.get("required_confirmations") or (20 if network == "TRC20" else 12)),
        "expiry_minutes": int(cfg.get("timeout_minutes") or 60),
    }


def _current_usdt_rate(db: Session) -> float:
    from services.exchange_rate_service import get_exchange_config
    return float(get_exchange_config(db).get("fixed_rate") or 26500.0)


# ── Tenant-facing page ───────────────────────────────────────────────────────

@router.get("/market-wallet", response_class=HTMLResponse)
async def market_wallet_page(request: Request, db: Session = Depends(get_db)):
    admin = check_auth(request, db)
    if not admin:
        return RedirectResponse(url="/login", status_code=302)

    balance = admin.market_wallet_balance or 0.0
    pending_withdraw = market_wallet_service.get_pending_withdrawal_total(db, admin.id)
    available = max(0.0, balance - pending_withdraw)
    n_attached = (
        db.query(Product)
        .filter(Product.tenant_id == admin.id, Product.source_type == SourceType.api, Product.is_active == True)
        .count()
    )
    transactions = market_wallet_service.list_market_wallet_transactions(db, admin.id, limit=50)
    deposits = (
        db.query(MarketWalletDeposit)
        .filter(MarketWalletDeposit.admin_user_id == admin.id)
        .order_by(MarketWalletDeposit.created_at.desc())
        .limit(20)
        .all()
    )
    withdrawals = (
        db.query(MarketWalletWithdrawal)
        .filter(MarketWalletWithdrawal.admin_user_id == admin.id)
        .order_by(MarketWalletWithdrawal.created_at.desc())
        .limit(20)
        .all()
    )
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "market_wallet.html", {
        "admin": admin,
        "balance": balance,
        "pending_withdraw": pending_withdraw,
        "available": available,
        "n_attached": n_attached,
        "transactions": transactions,
        "deposits": deposits,
        "withdrawals": withdrawals,
        "rate": _current_usdt_rate(db),
        "flash": flash_msg,
    })


@router.post("/market-wallet/deposit")
async def create_deposit(
    request: Request, db: Session = Depends(get_db),
    method: str = Form(...), vnd_amount: float = Form(...),
):
    admin = check_auth(request, db)
    if not admin:
        return RedirectResponse(url="/login", status_code=302)

    if vnd_amount <= 0:
        flash(request, "Số tiền nạp không hợp lệ!", "error")
        return RedirectResponse(url="/market-wallet", status_code=302)

    payment_info = _owner_crypto_payment_display(db, method)
    if not payment_info:
        flash(request, "Phương thức thanh toán này chưa được cấu hình!", "error")
        return RedirectResponse(url="/market-wallet", status_code=302)

    rate = _current_usdt_rate(db)
    usdt_amount = round(vnd_amount / rate, 4)

    from services.exchange_rate_service import generate_unique_crypto_amount
    final_usdt = generate_unique_crypto_amount(db, usdt_amount, payment_info["network"])

    deposit = MarketWalletDeposit(
        admin_user_id=admin.id,
        currency=WalletCurrency.USDT,
        amount=final_usdt,
        vnd_credit_amount=vnd_amount,
        method=method,
        reference_code=f"MW{admin.id}{int(datetime.utcnow().timestamp())}",
        status=WalletDepositStatus.pending,
        network=payment_info["network"],
        receiving_address=payment_info["address"],
        confirmations=0,
        required_confirmations=payment_info.get("required_confirmations"),
        expires_at=datetime.utcnow() + timedelta(minutes=payment_info.get("expiry_minutes") or 60),
    )
    db.add(deposit)
    db.commit()
    flash(request, f"Đã tạo yêu cầu nạp ví chợ — chuyển {final_usdt:.4f} USDT ({payment_info['network']}) tới {payment_info['address']}.")
    return RedirectResponse(url="/market-wallet", status_code=302)


@router.post("/market-wallet/withdraw")
async def create_withdrawal(
    request: Request, db: Session = Depends(get_db),
    amount: float = Form(...), account_info: str = Form(...),
):
    admin = check_auth(request, db)
    if not admin:
        return RedirectResponse(url="/login", status_code=302)

    balance = admin.market_wallet_balance or 0.0
    pending = market_wallet_service.get_pending_withdrawal_total(db, admin.id)
    available = balance - pending
    if amount <= 0 or amount > available:
        flash(request, f"Số dư khả dụng để rút chỉ còn {available:,.0f}đ.".replace(",", "."), "error")
        return RedirectResponse(url="/market-wallet", status_code=302)

    w = MarketWalletWithdrawal(
        admin_user_id=admin.id, currency=WalletCurrency.VND, amount=amount,
        account_info=account_info, status=MarketWalletWithdrawalStatus.pending,
    )
    db.add(w)
    db.commit()
    flash(request, "Đã gửi yêu cầu rút ví chợ, chờ admin xử lý.")
    return RedirectResponse(url="/market-wallet", status_code=302)


@router.post("/market-wallet/withdraw/{withdrawal_id}/cancel")
async def cancel_withdrawal(withdrawal_id: int, request: Request, db: Session = Depends(get_db)):
    admin = check_auth(request, db)
    if not admin:
        return RedirectResponse(url="/login", status_code=302)
    w = db.query(MarketWalletWithdrawal).filter(
        MarketWalletWithdrawal.id == withdrawal_id, MarketWalletWithdrawal.admin_user_id == admin.id,
    ).first()
    if w and w.status == MarketWalletWithdrawalStatus.pending:
        w.status = MarketWalletWithdrawalStatus.cancelled
        w.reviewed_at = datetime.utcnow()
        db.commit()
        flash(request, "Đã hủy yêu cầu rút.")
    else:
        flash(request, "Yêu cầu không tồn tại hoặc đã được xử lý!", "error")
    return RedirectResponse(url="/market-wallet", status_code=302)


# ── Owner-only cross-tenant review page ─────────────────────────────────────

@router.get("/market-wallet/admin", response_class=HTMLResponse)
async def market_wallet_admin_page(request: Request, db: Session = Depends(get_db), tab: str = "deposits"):
    owner = check_owner(request, db)
    if not owner:
        return RedirectResponse(url="/", status_code=302)

    tenants = (
        db.query(AdminUser)
        .execution_options(skip_tenant_filter=True)
        .order_by(AdminUser.is_owner.desc(), AdminUser.created_at.desc())
        .all()
    )
    deposits = (
        db.query(MarketWalletDeposit)
        .filter(MarketWalletDeposit.status == WalletDepositStatus.manual_review)
        .order_by(MarketWalletDeposit.created_at.desc())
        .all()
    )
    withdrawals = (
        db.query(MarketWalletWithdrawal)
        .filter(MarketWalletWithdrawal.status.in_([
            MarketWalletWithdrawalStatus.pending, MarketWalletWithdrawalStatus.approved,
        ]))
        .order_by(MarketWalletWithdrawal.created_at.desc())
        .all()
    )
    admin_map = {a.id: a for a in tenants}
    flash_msg = request.session.pop("flash", None)
    return templates.TemplateResponse(request, "market_wallet_admin.html", {
        "tenants": tenants,
        "deposits": deposits,
        "withdrawals": withdrawals,
        "admin_map": admin_map,
        "tab": tab,
        "flash": flash_msg,
    })


@router.post("/market-wallet/admin/deposit/{deposit_id}/confirm")
async def admin_confirm_deposit(deposit_id: int, request: Request, db: Session = Depends(get_db),
                                 admin_note: str = Form("")):
    owner = check_owner(request, db)
    if not owner:
        return RedirectResponse(url="/", status_code=302)
    deposit = db.query(MarketWalletDeposit).execution_options(skip_tenant_filter=True).filter(
        MarketWalletDeposit.id == deposit_id
    ).first()
    if not deposit:
        flash(request, "Yêu cầu không tồn tại!", "error")
        return RedirectResponse(url="/market-wallet/admin", status_code=302)
    if deposit.status != WalletDepositStatus.manual_review:
        flash(request, "Yêu cầu này không (còn) ở trạng thái cần xử lý!", "error")
        return RedirectResponse(url="/market-wallet/admin", status_code=302)

    now_iso = datetime.utcnow().isoformat(sep=" ")
    admin_username = str(request.session.get("admin_id", "owner"))
    try:
        market_wallet_service.credit_market_wallet(
            db, deposit.admin_user_id, WalletCurrency.VND, deposit.vnd_credit_amount or deposit.amount,
            WalletTxType.deposit, deposit_id=deposit.id,
            note=f"Nạp ví chợ (thủ công) — {deposit.reference_code}. {admin_note}".strip(),
            actor=admin_username,
            extra_updates=[(
                "UPDATE market_wallet_deposits SET status='credited', admin_note=?, "
                "verified_at=?, credited_at=?, confirmed_at=?, confirmed_by=? "
                "WHERE id=? AND status='manual_review'",
                (admin_note, now_iso, now_iso, now_iso, admin_username, deposit.id),
            )],
        )
        flash(request, f"Đã xác nhận nạp ví chợ {deposit.reference_code}!")
    except market_wallet_service.AlreadyProcessedError:
        flash(request, "Yêu cầu này đã được xử lý (double-click)!", "error")
    except Exception as e:
        flash(request, f"Lỗi xác nhận: {e}", "error")
    return RedirectResponse(url="/market-wallet/admin", status_code=302)


@router.post("/market-wallet/admin/deposit/{deposit_id}/reject")
async def admin_reject_deposit(deposit_id: int, request: Request, db: Session = Depends(get_db),
                                admin_note: str = Form("")):
    owner = check_owner(request, db)
    if not owner:
        return RedirectResponse(url="/", status_code=302)
    from sqlalchemy import text as sql_text
    now_iso = datetime.utcnow().isoformat(sep=" ")
    admin_username = str(request.session.get("admin_id", "owner"))
    rows = db.execute(
        sql_text(
            "UPDATE market_wallet_deposits SET status='failed', admin_note=:note, failed_reason=:note, "
            "confirmed_at=:ts, confirmed_by=:admin WHERE id=:id AND status='manual_review'"
        ),
        {"note": admin_note, "ts": now_iso, "admin": admin_username, "id": deposit_id},
    )
    if rows.rowcount == 0:
        db.rollback()
        flash(request, "Yêu cầu này không (còn) ở trạng thái cần xử lý!", "error")
    else:
        db.commit()
        flash(request, "Đã từ chối yêu cầu nạp ví chợ.")
    return RedirectResponse(url="/market-wallet/admin", status_code=302)


@router.post("/market-wallet/admin/withdraw/{withdrawal_id}/approve")
async def admin_approve_withdrawal(withdrawal_id: int, request: Request, db: Session = Depends(get_db),
                                    admin_note: str = Form("")):
    owner = check_owner(request, db)
    if not owner:
        return RedirectResponse(url="/", status_code=302)
    w = db.query(MarketWalletWithdrawal).execution_options(skip_tenant_filter=True).filter(
        MarketWalletWithdrawal.id == withdrawal_id
    ).first()
    if not w or w.status != MarketWalletWithdrawalStatus.pending:
        flash(request, "Yêu cầu không tồn tại hoặc đã xử lý!", "error")
        return RedirectResponse(url="/market-wallet/admin?tab=withdrawals", status_code=302)

    admin_username = str(request.session.get("admin_id", "owner"))
    try:
        # Debit happens on approval — see MarketWalletWithdrawal docstring —
        # atomically guarded so a double-click can never debit twice.
        market_wallet_service.debit_market_wallet(
            db, w.admin_user_id, w.currency, w.amount, WalletTxType.withdrawal,
            withdrawal_id=w.id, note=f"Rút ví chợ đã duyệt. {admin_note}".strip(), actor=admin_username,
            extra_updates=[(
                "UPDATE market_wallet_withdrawals SET status='approved', admin_note=?, reviewed_at=?, reviewed_by=? "
                "WHERE id=? AND status='pending'",
                (admin_note, datetime.utcnow().isoformat(sep=" "), admin_username, w.id),
            )],
        )
        flash(request, "Đã duyệt yêu cầu rút ví chợ — số dư đã bị trừ, hãy chuyển tiền cho khách thuê rồi đánh dấu Đã trả.")
    except market_wallet_service.InsufficientBalanceError:
        flash(request, "Số dư ví chợ của khách thuê không đủ để duyệt yêu cầu này!", "error")
    except market_wallet_service.AlreadyProcessedError:
        flash(request, "Yêu cầu này đã được xử lý!", "error")
    return RedirectResponse(url="/market-wallet/admin?tab=withdrawals", status_code=302)


@router.post("/market-wallet/admin/withdraw/{withdrawal_id}/reject")
async def admin_reject_withdrawal(withdrawal_id: int, request: Request, db: Session = Depends(get_db),
                                   admin_note: str = Form("")):
    owner = check_owner(request, db)
    if not owner:
        return RedirectResponse(url="/", status_code=302)
    from sqlalchemy import text as sql_text
    admin_username = str(request.session.get("admin_id", "owner"))
    rows = db.execute(
        sql_text(
            "UPDATE market_wallet_withdrawals SET status='rejected', admin_note=:note, "
            "reviewed_at=:ts, reviewed_by=:admin WHERE id=:id AND status='pending'"
        ),
        {"note": admin_note, "ts": datetime.utcnow().isoformat(sep=" "), "admin": admin_username, "id": withdrawal_id},
    )
    if rows.rowcount == 0:
        db.rollback()
        flash(request, "Yêu cầu không tồn tại hoặc đã xử lý!", "error")
    else:
        db.commit()
        flash(request, "Đã từ chối yêu cầu rút ví chợ (số dư không bị ảnh hưởng).")
    return RedirectResponse(url="/market-wallet/admin?tab=withdrawals", status_code=302)


@router.post("/market-wallet/admin/withdraw/{withdrawal_id}/mark-paid")
async def admin_mark_withdrawal_paid(withdrawal_id: int, request: Request, db: Session = Depends(get_db)):
    owner = check_owner(request, db)
    if not owner:
        return RedirectResponse(url="/", status_code=302)
    from sqlalchemy import text as sql_text
    admin_username = str(request.session.get("admin_id", "owner"))
    rows = db.execute(
        sql_text(
            "UPDATE market_wallet_withdrawals SET status='paid', paid_at=:ts, reviewed_by=:admin "
            "WHERE id=:id AND status='approved'"
        ),
        {"ts": datetime.utcnow().isoformat(sep=" "), "admin": admin_username, "id": withdrawal_id},
    )
    if rows.rowcount == 0:
        db.rollback()
        flash(request, "Yêu cầu không tồn tại hoặc chưa được duyệt!", "error")
    else:
        db.commit()
        flash(request, "Đã đánh dấu đã chuyển tiền.")
    return RedirectResponse(url="/market-wallet/admin?tab=withdrawals", status_code=302)


@router.post("/market-wallet/admin/owner-balance")
async def update_owner_balance(request: Request, db: Session = Depends(get_db), balance: float = Form(...)):
    """Manual entry/edit of the owner's own ví chợ balance (how much the
    owner has prepaid to the real upstream supplier) — used when the
    connected supplier doesn't expose a balance-check endpoint."""
    owner = check_owner(request, db)
    if not owner:
        return RedirectResponse(url="/", status_code=302)
    admin_username = str(request.session.get("admin_id", "owner"))
    delta = balance - (owner.market_wallet_balance or 0.0)
    if abs(delta) >= 0.01:
        if delta > 0:
            market_wallet_service.credit_market_wallet(
                db, owner.id, WalletCurrency.VND, delta, WalletTxType.admin_credit,
                note="Owner cập nhật số dư ví chợ thủ công", actor=admin_username,
            )
        else:
            try:
                market_wallet_service.debit_market_wallet(
                    db, owner.id, WalletCurrency.VND, -delta, WalletTxType.admin_debit,
                    note="Owner cập nhật số dư ví chợ thủ công", actor=admin_username,
                )
            except market_wallet_service.InsufficientBalanceError:
                flash(request, "Không thể đặt số dư âm!", "error")
                return RedirectResponse(url="/market-wallet/admin", status_code=302)
    flash(request, "Đã cập nhật số dư ví chợ của owner.")
    return RedirectResponse(url="/market-wallet/admin", status_code=302)
