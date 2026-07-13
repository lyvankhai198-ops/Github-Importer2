"""
customer_api.py — inbound REST API for customers who hold a programmatic
API key (bot menu "🔗 API"). Authenticated via the `X-API-Key` header.

Every request that resolves to a known client is logged by the
`api_request_logger` ASGI middleware in main.py (keyed off
`request.state.api_client_id`), so no per-endpoint logging code is needed
here — this dependency only needs to set that state attribute.
"""
from fastapi import APIRouter, Request, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import ApiClient, ApiClientStatus, Product, Order, User
from services.api_key_service import hash_api_key
from services.api_client_service import (
    check_rate_limits, get_permissions, create_api_order,
    ApiOrderError, InsufficientBalanceApiError,
)
from services.product_service import get_active_products_for_bot, get_product_stock_status
from services import wallet_service
from datetime import datetime

router = APIRouter(prefix="/api/v1", tags=["customer-api"])


async def require_api_client(
    request: Request,
    db: Session = Depends(get_db),
    x_api_key: str = Header(None, alias="X-API-Key"),
) -> ApiClient:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    key_hash = hash_api_key(x_api_key)
    client = db.query(ApiClient).filter(ApiClient.key_hash == key_hash).first()
    if not client:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # From here on the client is identified — every outcome (locked, rate
    # limited, or successful) gets logged by the middleware.
    request.state.api_client_id = client.id

    if client.status != ApiClientStatus.active:
        raise HTTPException(status_code=401, detail=f"API key is {client.status.value}")

    limit_error = check_rate_limits(db, client)
    if limit_error:
        raise HTTPException(status_code=429, detail=limit_error)

    client.total_requests = (client.total_requests or 0) + 1
    client.last_used_at = datetime.utcnow()
    db.commit()
    return client


def _product_public_dict(product: Product, db: Session) -> dict:
    info = get_product_stock_status(product.id, db)
    return {
        "id": product.id,
        "name": product.name,
        "name_en": product.name_en,
        "description": product.description,
        "price_vnd": product.sale_price,
        "price_usdt": product.price_usdt,
        "min_quantity": product.min_quantity or 1,
        "warranty": product.warranty,
        "duration": product.duration,
        "status": info["status"],
        "stock": info["stock"] if info["status"] != "unavailable" else None,
    }


@router.get("/account")
async def get_account(client: ApiClient = Depends(require_api_client), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.telegram_id == client.telegram_user_id).first()
    return {
        "name": client.name or client.key_prefix,
        "status": client.status.value,
        "permissions": get_permissions(client),
        "rate_limit_per_minute": client.rate_limit_per_minute,
        "daily_limit": client.daily_limit,
        "balance": {
            "vnd": wallet_service.get_balance(user, "VND") if user else 0.0,
            "usdt": wallet_service.get_balance(user, "USDT") if user else 0.0,
        },
        "total_requests": client.total_requests,
        "total_orders": client.total_orders,
        "created_at": client.created_at.isoformat() if client.created_at else None,
    }


@router.get("/balance")
async def get_balance(client: ApiClient = Depends(require_api_client), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.telegram_id == client.telegram_user_id).first()
    return {
        "vnd": wallet_service.get_balance(user, "VND") if user else 0.0,
        "usdt": wallet_service.get_balance(user, "USDT") if user else 0.0,
    }


@router.get("/products")
async def list_products(client: ApiClient = Depends(require_api_client), db: Session = Depends(get_db)):
    items = get_active_products_for_bot(db, show_out_of_stock=True)
    return {"products": [_product_public_dict(item["product"], db) for item in items]}


@router.get("/products/{product_id}")
async def get_product(product_id: int, client: ApiClient = Depends(require_api_client),
                       db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id, Product.is_active == True).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return _product_public_dict(product, db)


@router.post("/orders")
async def create_order_endpoint(payload: dict, client: ApiClient = Depends(require_api_client),
                                 db: Session = Depends(get_db)):
    product_id = payload.get("product_id")
    quantity = payload.get("quantity", 1)
    client_order_id = payload.get("client_order_id")
    currency = payload.get("currency", "VND")

    if not product_id or not client_order_id:
        raise HTTPException(status_code=400, detail="product_id and client_order_id are required")
    try:
        product_id = int(product_id)
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="product_id and quantity must be integers")

    try:
        result = await create_api_order(client, product_id, quantity, str(client_order_id), currency)
        return result
    except ApiOrderError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})
    except InsufficientBalanceApiError as e:
        raise HTTPException(status_code=402, detail={
            "code": "insufficient_balance", "currency": e.currency,
            "balance": e.balance, "needed": e.needed,
        })


@router.get("/orders")
async def list_orders(client: ApiClient = Depends(require_api_client), db: Session = Depends(get_db),
                       limit: int = Query(50, le=200)):
    orders = (
        db.query(Order)
        .filter(Order.api_client_id == client.id)
        .order_by(Order.created_at.desc())
        .limit(limit)
        .all()
    )
    from services.api_client_service import _order_public_dict
    return {"orders": [_order_public_dict(o) for o in orders]}


@router.get("/orders/{order_code}")
async def get_order(order_code: str, client: ApiClient = Depends(require_api_client),
                     db: Session = Depends(get_db)):
    order = db.query(Order).filter(
        Order.order_code == order_code, Order.api_client_id == client.id,
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    from services.api_client_service import _order_public_dict
    return _order_public_dict(order)
