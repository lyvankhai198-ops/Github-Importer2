import asyncio
import json
from datetime import datetime
from sqlalchemy.orm import Session
from models import ApiConnection, ApiProduct
from integrations.manager import api_manager
from database import SessionLocal


async def sync_api_products(db: Session, api_connection_id: int) -> dict:
    conn = db.query(ApiConnection).filter(ApiConnection.id == api_connection_id).first()
    if not conn:
        return {"success": False, "message": "Connection not found"}
    adapter = api_manager.get_adapter(conn)
    try:
        products = await adapter.get_products()
        now = datetime.utcnow()
        synced = 0
        for p in products:
            ext_id = str(p.get("id", ""))
            existing = db.query(ApiProduct).filter(
                ApiProduct.api_connection_id == api_connection_id,
                ApiProduct.external_product_id == ext_id
            ).first()
            raw = json.dumps(p.get("raw", p))
            if existing:
                existing.external_name = p.get("name", "")
                existing.external_price = p.get("price", 0)
                existing.external_stock = p.get("stock", 0)
                existing.external_status = p.get("status", "")
                existing.raw_json = raw
                existing.last_sync_at = now
                existing.updated_at = now
            else:
                new_prod = ApiProduct(
                    api_connection_id=api_connection_id,
                    external_product_id=ext_id,
                    external_name=p.get("name", ""),
                    external_price=p.get("price", 0),
                    external_stock=p.get("stock", 0),
                    external_status=p.get("status", ""),
                    raw_json=raw,
                    last_sync_at=now,
                )
                db.add(new_prod)
            synced += 1
        conn.last_sync_at = now
        conn.last_success_at = now
        conn.last_error = None
        db.commit()
        return {"success": True, "synced": synced, "message": f"Synced {synced} products"}
    except Exception as e:
        conn.last_sync_at = datetime.utcnow()
        conn.last_error = str(e)
        db.commit()
        return {"success": False, "message": str(e)}


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
            conn = db.query(ApiConnection).filter(ApiConnection.id == api_connection_id, ApiConnection.is_active == True).first()
            if conn:
                await sync_api_products(db, api_connection_id)
        except Exception:
            pass
        finally:
            db.close()


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
