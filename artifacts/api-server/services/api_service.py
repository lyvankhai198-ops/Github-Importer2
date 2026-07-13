import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime
from sqlalchemy.orm import Session
from models import ApiConnection, ApiProduct, SourceType
from integrations.manager import api_manager
from database import SessionLocal
from services.normalize import normalize_product_data

logger = logging.getLogger(__name__)


async def sync_api_products(db: Session, api_connection_id: int) -> dict:
    conn = db.query(ApiConnection).filter(ApiConnection.id == api_connection_id).first()
    if not conn:
        return {"success": False, "message": "Connection not found"}
    adapter = api_manager.get_adapter(conn)
    logger.info(f"API_SYNC_STARTED: connection_id={api_connection_id}")
    try:
        products = await adapter.get_products()
        now = datetime.utcnow()
        synced = 0
        created_count = 0
        updated_count = 0
        error_count = 0
        # Track pre-sync stock for api_auto-linked products so we can detect
        # restock / out-of-stock transitions after this loop updates them.
        from models import ProductSource, DeliveryMode
        # Aggregated per-product (a product can have several sources on this
        # same connection) so restock/out-of-stock detection reflects the
        # product's total stock, not just a single source's.
        pre_sync_stock = defaultdict(int)
        for src in db.query(ProductSource).join(ApiProduct).filter(
                ApiProduct.api_connection_id == api_connection_id).all():
            if src.api_product:
                pre_sync_stock[src.product_id] += (src.api_product.external_stock or 0)
        for p in products:
            # Guard per-item so one malformed item (bad price, unexpected
            # shape, etc.) can't abort the whole sync — it's just counted
            # as an error and the rest of the products still sync normally.
            try:
                ext_id = str(p.get("id", ""))
                if not ext_id:
                    continue
                existing = db.query(ApiProduct).filter(
                    ApiProduct.api_connection_id == api_connection_id,
                    ApiProduct.external_product_id == ext_id
                ).first()
                raw = json.dumps(p.get("raw", p), ensure_ascii=False)
                if existing:
                    existing.external_name = p.get("name", "")
                    existing.external_description = p.get("description", "")
                    existing.external_price = p.get("price", 0)
                    existing.external_stock = p.get("stock", 0)
                    existing.external_min_quantity = p.get("min_quantity", 1)
                    existing.external_max_quantity = p.get("max_quantity")
                    existing.external_warranty = p.get("warranty", "")
                    existing.external_duration = p.get("duration", "")
                    existing.external_image_url = p.get("image_url", "")
                    existing.external_status = p.get("status", "")
                    if "item_type" in p:
                        existing.external_item_type = p.get("item_type")
                    if "seller" in p:
                        existing.external_seller = p.get("seller")
                    if "category" in p:
                        existing.external_category = p.get("category")
                    existing.raw_json = raw
                    existing.last_sync_at = now
                    existing.updated_at = now
                    updated_count += 1
                else:
                    new_prod = ApiProduct(
                        api_connection_id=api_connection_id,
                        external_product_id=ext_id,
                        external_name=p.get("name", ""),
                        external_description=p.get("description", ""),
                        external_price=p.get("price", 0),
                        external_stock=p.get("stock", 0),
                        external_min_quantity=p.get("min_quantity", 1),
                        external_max_quantity=p.get("max_quantity"),
                        external_warranty=p.get("warranty", ""),
                        external_duration=p.get("duration", ""),
                        external_image_url=p.get("image_url", ""),
                        external_status=p.get("status", ""),
                        external_item_type=p.get("item_type"),
                        external_seller=p.get("seller"),
                        external_category=p.get("category"),
                        raw_json=raw,
                        last_sync_at=now,
                    )
                    db.add(new_prod)
                    created_count += 1
                synced += 1
            except Exception as item_err:
                error_count += 1
                logger.error(f"API_SYNC_ITEM_ERROR: connection_id={api_connection_id} item={p} error={item_err}")

        # Also update ProductSource.last_stock for linked products, and
        # propagate image/description/warranty/duration onto the linked
        # Product itself — skipping any field the admin has manually edited
        # (see services/product_sync.py).
        from services.product_sync import sync_product_from_api_product, ensure_en_fields, auto_assign_icon_if_unlocked
        from models import DeliveryMode
        sources = db.query(ProductSource).join(ApiProduct).filter(
            ApiProduct.api_connection_id == api_connection_id
        ).all()
        transitions = []  # (product_id, back_in_stock: bool)
        restocks = []      # (product_id, added_qty, new_total) — any increase, not just 0→N
        post_sync_stock = defaultdict(int)
        for src in sources:
            if not src.api_product:
                continue
            new_stock = src.api_product.external_stock or 0
            src.last_stock = new_stock
            src.last_cost = src.api_product.external_price
            post_sync_stock[src.product_id] += new_stock

            if src.product and src.product.source_type == SourceType.api and \
                    src.product.delivery_mode == DeliveryMode.api_auto:
                sync_product_from_api_product(src.product, src.api_product)
                # Keep name_en/description_en auto-filled from the (possibly
                # just-updated) Vietnamese text, unless the admin has locked
                # either field with a hand-typed value.
                ensure_en_fields(src.product)
                # Auto-assign a name-keyword emoji unless the admin already
                # chose one manually.
                auto_assign_icon_if_unlocked(src.product)

        for product_id, new_total in post_sync_stock.items():
            old_total = pre_sync_stock.get(product_id, 0)
            if old_total <= 0 and new_total > 0:
                transitions.append((product_id, True))
            elif old_total > 0 and new_total <= 0:
                transitions.append((product_id, False))
            if new_total > old_total:
                restocks.append((product_id, new_total - old_total, new_total))

        conn.last_sync_at = now
        conn.last_success_at = now
        conn.last_error = None
        db.commit()
        logger.info(
            f"API_SYNC_COMPLETED: connection_id={api_connection_id} synced={synced} "
            f"created={created_count} updated={updated_count} errors={error_count}"
        )

        for product_id, back_in_stock in transitions:
            if back_in_stock:
                logger.info(f"PRODUCT_RESTOCKED: product_id={product_id} connection_id={api_connection_id}")
            else:
                logger.info(f"PRODUCT_OUT_OF_STOCK: product_id={product_id} connection_id={api_connection_id}")
            try:
                await _handle_api_stock_transition(product_id, back_in_stock)
            except Exception as e:
                logger.error(f"[api_service] stock transition handler error for product {product_id}: {e}")

        # Broadcast a "🔄 ĐÃ BỔ SUNG HÀNG" announcement to all active users
        # for every genuine stock increase (separate from the admin-only /
        # waiting-list-only notification above).
        from services.broadcast_service import notify_restock_broadcast
        for product_id, added_qty, new_total in restocks:
            try:
                await notify_restock_broadcast(product_id, added_qty, new_total)
            except Exception as e:
                logger.error(f"[api_service] restock broadcast error for product {product_id}: {e}")

        return {
            "success": True,
            "synced": synced,
            "created": created_count,
            "updated": updated_count,
            "errors": error_count,
            "message": f"Synced {synced} products ({created_count} new, {updated_count} updated"
                       + (f", {error_count} errors" if error_count else "") + ")",
        }
    except Exception as e:
        conn.last_sync_at = datetime.utcnow()
        conn.last_error = str(e)
        db.commit()
        logger.error(f"API_SYNC_FAILED: connection_id={api_connection_id} error={e}")
        return {"success": False, "message": str(e)}


async def _handle_api_stock_transition(product_id: int, back_in_stock: bool):
    """
    Mirrors the local-inventory restock/out-of-stock handling (see
    services/inventory_service.py Section 12) for api_auto-linked products:
      - always notify admin of the transition,
      - on restock, ping users with paid_waiting_stock orders for this
        product (gated on notify_users_when_restocked) and retry delivery.
    """
    from services.inventory_service import notify_restock_if_enabled, _get_bot_config
    from services.bot_service import bot_manager
    from models import Product, Order, OrderStatus

    db = SessionLocal()
    try:
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product:
            return

        # Admin notification (independent of the user-facing notify toggle)
        if bot_manager.is_running():
            cfg = _get_bot_config(db)
            admin_id = cfg.admin_telegram_id if cfg else ""
            if admin_id:
                icon = "🔔" if back_in_stock else "⚠️"
                label = "đã có hàng trở lại" if back_in_stock else "đã hết hàng"
                try:
                    await bot_manager.send_message(
                        admin_id,
                        f"{icon} Sản phẩm \"{product.name}\" {label} (nguồn API).",
                    )
                except Exception:
                    pass

        if not back_in_stock:
            return

        await notify_restock_if_enabled(product_id, back_in_stock=True)

        waiting = (
            db.query(Order)
            .filter(Order.product_id == product_id, Order.status == OrderStatus.paid_waiting_stock)
            .order_by(Order.created_at.asc())
            .all()
        )
        if not waiting:
            return
        from services.payment_service import process_paid_order
        for order in waiting:
            try:
                await process_paid_order(order.id)
            except Exception as e:
                logger.error(f"[api_service] retry process_paid_order({order.id}) error: {e}")
    finally:
        db.close()


async def test_api_connection(db: Session, api_connection_id: int) -> dict:
    conn = db.query(ApiConnection).filter(ApiConnection.id == api_connection_id).first()
    if not conn:
        return {"success": False, "message": "Connection not found"}
    api_manager.invalidate(api_connection_id)
    adapter = api_manager.get_adapter(conn)
    return await adapter.test_connection()


async def get_api_balance(db: Session, api_connection_id: int) -> dict:
    conn = db.query(ApiConnection).filter(ApiConnection.id == api_connection_id).first()
    if not conn:
        return {"success": False, "message": "Connection not found", "balance": 0, "currency": "USD"}
    adapter = api_manager.get_adapter(conn)
    return await adapter.get_balance()


async def _sync_loop(api_connection_id: int, interval_minutes: int):
    while True:
        await asyncio.sleep(interval_minutes * 60)
        db = SessionLocal()
        try:
            conn = db.query(ApiConnection).filter(
                ApiConnection.id == api_connection_id,
                ApiConnection.is_active == True
            ).first()
            if conn:
                await sync_api_products(db, api_connection_id)
        except Exception:
            pass
        finally:
            db.close()


# ── On-demand full sync (used when a shopper opens the product list) ───────
# Short-lived cache so rapid repeated taps on "Products" don't re-hit every
# source; per-source timeout + isolated DB session so one slow/broken source
# can never hang the bot or block the others.
_SYNC_CACHE_SECONDS = 30
_SOURCE_TIMEOUT_SECONDS = 8
_last_full_sync_at: datetime | None = None
_full_sync_lock = asyncio.Lock()


async def sync_active_supplier_products(db: Session) -> dict:
    """
    Refresh every active API connection in parallel before the shopper sees
    the product list. Each source gets its own DB session and an ~8s
    timeout; a failing/slow source is skipped (its last-known DB data is
    kept as-is — never zeroed out) and reported in "failed" without
    affecting the others. Returns immediately (no re-sync) if the last full
    sync completed within the last 30s.
    """
    global _last_full_sync_at
    now = datetime.utcnow()
    async with _full_sync_lock:
        if _last_full_sync_at and (now - _last_full_sync_at).total_seconds() < _SYNC_CACHE_SECONDS:
            return {"ran": False, "failed": []}
        _last_full_sync_at = now

    connections = db.query(ApiConnection).filter(ApiConnection.is_active == True).all()
    if not connections:
        return {"ran": True, "failed": []}

    async def _sync_one(conn_id: int, conn_name: str):
        sess = SessionLocal()
        try:
            result = await asyncio.wait_for(sync_api_products(sess, conn_id), timeout=_SOURCE_TIMEOUT_SECONDS)
            return None if result.get("success") else conn_name
        except asyncio.TimeoutError:
            logger.error(f"API_SYNC_TIMEOUT: connection_id={conn_id} name={conn_name}")
            return conn_name
        except Exception as e:
            logger.error(f"API_SYNC_ON_DEMAND_ERROR: connection_id={conn_id} name={conn_name} error={e}")
            return conn_name
        finally:
            sess.close()

    outcomes = await asyncio.gather(
        *[_sync_one(c.id, c.name) for c in connections], return_exceptions=True
    )
    failed = [o for o in outcomes if isinstance(o, str)]
    return {"ran": True, "failed": failed}


_sync_tasks: dict = {}


def start_sync_scheduler(api_connection_id: int, interval_minutes: int):
    if api_connection_id in _sync_tasks:
        task = _sync_tasks[api_connection_id]
        if not task.done():
            return
    task = asyncio.create_task(_sync_loop(api_connection_id, interval_minutes))
    _sync_tasks[api_connection_id] = task


def stop_sync_scheduler(api_connection_id: int):
    task = _sync_tasks.pop(api_connection_id, None)
    if task and not task.done():
        task.cancel()


def stop_all_sync_schedulers():
    """Cancel every running sync loop — used on clean app shutdown so a
    restart never ends up with duplicate schedulers for the same connection."""
    for api_connection_id in list(_sync_tasks.keys()):
        stop_sync_scheduler(api_connection_id)
