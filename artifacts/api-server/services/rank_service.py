"""
Membership rank ("Cấp bậc") system.

Design:
  - A user's rank is NEVER stored as a manual/authoritative value by itself —
    it is derived from live total spend (see compute_total_spent) and only
    persisted to User.rank_id as a cache so the bot doesn't recompute it on
    every keystroke. Recomputation happens right after every order that
    actually gets paid (see the call sites in payment_service.process_paid_order
    and order_service.create_order).
  - Ranks themselves (name/emoji/threshold/order/active) are fully admin-
    editable from Web Admin → "Cấp bậc" (routers/ranks.py) — no hardcoded
    tier table in code, per spec section 5/7.
  - "Total spend" counts orders whose payment is confirmed, using whichever
    signal that order type actually sets:
      * payment_status in (paid, overpaid)              — SePay/crypto/Binance/wallet
      * payment_status IS NULL and status in (completed, — legacy instant-create
        partial_delivery)                                 path (no payment gate)
    This reads directly from the orders table (no hardcoded/cached totals),
    per spec section 7, and is robust across every existing payment path
    without needing to hook each one individually.
"""
import logging
from sqlalchemy import func, or_, and_
from sqlalchemy.orm import Session

from models import Order, OrderStatus, PaymentStatus, Rank, User

logger = logging.getLogger(__name__)


def compute_total_spent(db: Session, telegram_user_id: str) -> float:
    total = db.query(func.sum(Order.total_price)).filter(
        Order.telegram_user_id == telegram_user_id,
        or_(
            Order.payment_status.in_([PaymentStatus.paid, PaymentStatus.overpaid]),
            and_(
                Order.payment_status.is_(None),
                Order.status.in_([OrderStatus.completed, OrderStatus.partial_delivery]),
            ),
        ),
    ).scalar()
    return float(total or 0.0)


def compute_total_accounts_purchased(db: Session, telegram_user_id: str) -> int:
    """Sum of quantity across the same 'counts as a successful order' set used
    by compute_total_spent — i.e. how many accounts/items the user has
    actually received payment-confirmed orders for."""
    total = db.query(func.sum(Order.quantity)).filter(
        Order.telegram_user_id == telegram_user_id,
        or_(
            Order.payment_status.in_([PaymentStatus.paid, PaymentStatus.overpaid]),
            and_(
                Order.payment_status.is_(None),
                Order.status.in_([OrderStatus.completed, OrderStatus.partial_delivery]),
            ),
        ),
    ).scalar()
    return int(total or 0)


def get_active_ranks(db: Session):
    """All enabled ranks, ascending by threshold (lowest tier first)."""
    return (
        db.query(Rank)
        .filter(Rank.is_active == True)  # noqa: E712
        .order_by(Rank.sort_order.asc(), Rank.min_spend.asc())
        .all()
    )


def get_rank_for_spend(db: Session, spend: float):
    """Highest active rank whose threshold the spend has reached, or the
    lowest active rank if none match yet (e.g. a brand-new user)."""
    ranks = get_active_ranks(db)
    if not ranks:
        return None
    eligible = [r for r in ranks if spend >= (r.min_spend or 0)]
    return max(eligible, key=lambda r: r.min_spend) if eligible else ranks[0]


def get_next_rank(db: Session, current_rank: Rank):
    """The next active rank above current_rank, or None if already at the top."""
    if not current_rank:
        return None
    ranks = get_active_ranks(db)
    higher = [r for r in ranks if r.min_spend > current_rank.min_spend]
    return min(higher, key=lambda r: r.min_spend) if higher else None


def get_progress(spend: float, current_rank: Rank, next_rank: Rank) -> dict:
    """Progress toward next_rank as a dict: {is_max, percent, remaining}."""
    if not next_rank:
        return {"is_max": True, "percent": 100, "remaining": 0}
    base = current_rank.min_spend if current_rank else 0
    span = next_rank.min_spend - base
    done = spend - base
    percent = 0 if span <= 0 else max(0, min(100, (done / span) * 100))
    remaining = max(0, next_rank.min_spend - spend)
    return {"is_max": False, "percent": percent, "remaining": remaining}


def render_progress_bar(percent: float, width: int = 10) -> str:
    filled = max(0, min(width, round((percent or 0) / 100 * width)))
    return ("█" * filled) + ("░" * (width - filled))


async def recompute_user_rank(db: Session, telegram_user_id: str, bot=None) -> dict:
    """
    Recompute + persist a user's rank from their live total spend. If the
    rank actually changed to a strictly higher tier, sends the 🎉 upgrade DM
    (best-effort — never raises on notify failure) when a bot instance is
    given. Safe/idempotent to call repeatedly: a no-op if the rank hasn't
    changed since the last call.
    """
    result = {"changed": False, "old_rank": None, "new_rank": None, "spend": 0.0}
    try:
        user = db.query(User).filter(User.telegram_id == str(telegram_user_id)).first()
        if not user:
            return result

        spend = compute_total_spent(db, str(telegram_user_id))
        result["spend"] = spend
        new_rank = get_rank_for_spend(db, spend)
        old_rank = db.query(Rank).filter(Rank.id == user.rank_id).first() if user.rank_id else None
        result["old_rank"] = old_rank
        result["new_rank"] = new_rank

        if not new_rank or new_rank.id == user.rank_id:
            return result

        is_real_upgrade = (not old_rank) or (new_rank.min_spend > old_rank.min_spend)
        user.rank_id = new_rank.id
        db.commit()
        result["changed"] = True

        # Only fire the congrats DM for an actual upgrade with a prior rank —
        # first-ever assignment (brand-new user landing on the base tier)
        # shouldn't announce itself as a "congratulations" event.
        if is_real_upgrade and old_rank is not None and bot is not None:
            try:
                from bot.i18n import get_user_lang
                from bot.notifier import notify_user_rank_upgrade
                lang = get_user_lang(db, str(telegram_user_id))
                await notify_user_rank_upgrade(bot, telegram_user_id, new_rank.emoji, new_rank.name, lang=lang)
            except Exception as e:
                logger.error(f"[rank_service] upgrade notify failed for {telegram_user_id}: {e}")
    except Exception as e:
        logger.error(f"[rank_service] recompute_user_rank failed for {telegram_user_id}: {e}")
    return result
