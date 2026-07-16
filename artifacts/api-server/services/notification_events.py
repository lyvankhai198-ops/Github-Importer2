"""
Dedup/audit ledger for automatic "new product" / "restock" Telegram
broadcasts (see models.NotificationEvent).

Every real-world event that should ever trigger a customer-facing broadcast
gets one row, keyed by a unique `event_key`. Claiming an event_key is
atomic (relies on the DB's UNIQUE index + IntegrityError, the same pattern
used for customer-API order idempotency) so the same event can never be
announced twice — no matter how many times a scheduler tick, API sync, or
admin action re-triggers the underlying check, and regardless of process
restarts in between.
"""
import logging
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import NotificationEvent

logger = logging.getLogger(__name__)


def claim_event(
    db: Session,
    event_key: str,
    event_type: str,
    product_id: int | None = None,
    source_id: int | None = None,
    previous_stock: int | None = None,
    current_stock: int | None = None,
    added_quantity: int | None = None,
) -> bool:
    """
    Atomically claim `event_key`. Returns True if this call is the first to
    claim it (caller should proceed to send the notification); False if it
    was already claimed before (caller must skip — already sent, or another
    concurrent caller is handling it).
    """
    ev = NotificationEvent(
        event_type=event_type,
        product_id=product_id,
        source_id=source_id,
        previous_stock=previous_stock,
        current_stock=current_stock,
        added_quantity=added_quantity,
        event_key=event_key,
        sent_at=datetime.utcnow(),
        status="sent",
    )
    db.add(ev)
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        logger.info(f"NOTIFICATION_EVENT_DUPLICATE: event_key={event_key}")
        return False


def has_new_product_event(db: Session, product_id: int) -> bool:
    """True if a "new_product" announcement has ever been sent for this
    product — i.e. it has already been introduced to users at least once."""
    return db.query(NotificationEvent).filter(
        NotificationEvent.event_type == "new_product",
        NotificationEvent.product_id == product_id,
    ).first() is not None
