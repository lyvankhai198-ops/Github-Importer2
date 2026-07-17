"""
translation_alerts.py — ADMIN-ONLY Telegram alert when
services.product_sync.sync_translations() records a translation failure on
a product. Deduplicated via the existing notification_events ledger (see
services/notification_events.py) so repeated sync retries against the same
broken product/source text only alert once; a genuinely new failure (the
source text changed since the last alert) gets a fresh one. Never shown to
shoppers, never raises.
"""
import logging

from services.notification_events import claim_event

logger = logging.getLogger(__name__)


async def notify_admin_translation_failed(db, product) -> None:
    if not product or product.translation_status != "failed":
        return
    event_key = f"translation_failed:{product.id}:{product.translation_source_hash or 'nohash'}"
    if not claim_event(db, event_key, "translation_failed", product_id=product.id):
        return  # already alerted for this exact failing content
    try:
        from services.bot_service import bot_manager
        from services.price_sync_service import _get_bot_config
        cfg = _get_bot_config(db)
        admin_id = cfg.admin_telegram_id if cfg else None
        if not admin_id or not bot_manager.is_running():
            return
        direction = "vi→en" if (product.source_language or "vi") == "vi" else "en→vi"
        text = (
            "⚠️ PRODUCT TRANSLATION FAILED\n\n"
            f"📦 Product: {product.name}\n"
            f"🔁 Direction: {direction}\n"
            f"❗ Error: {(product.translation_error or '')[:300]}"
        )
        await bot_manager.send_message(admin_id, text)
    except Exception as e:
        logger.error(f"[translation] admin failure alert failed for product {getattr(product, 'id', '?')}: {e}")
