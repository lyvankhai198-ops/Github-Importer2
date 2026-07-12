"""
Binance Pay service.

Supports two modes:
  F1. Merchant API — creates real Binance Pay orders, receives webhook, auto-fulfills
  F2. Manual Pay ID — shows static Pay ID, admin must manually approve

Config stored in PaymentMethod.config_encrypted (JSON) for method_code="binance_pay":
{
  "mode": "manual" | "merchant",
  // manual:
  "pay_id": "123456789",
  "recipient_name": "NGUYEN VAN A",
  "qr_image_path": "",       // optional static QR image path
  "payment_instructions": "",
  // merchant:
  "merchant_id": "",
  "api_key": "",             // NEVER logged
  "secret_key": "",          // NEVER logged
  "webhook_cert": "",        // Binance public key for signature verification
  "currency": "USDT",
  "timeout_minutes": 30,
}
"""
import json
import hmac
import hashlib
import logging
import uuid
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def get_binance_config(db) -> dict | None:
    from models import PaymentMethod
    from crypto import decrypt
    pm = db.query(PaymentMethod).filter(
        PaymentMethod.method_code == "binance_pay",
        PaymentMethod.is_active == True,
    ).first()
    if not pm or not pm.config_encrypted:
        return None
    try:
        return json.loads(decrypt(pm.config_encrypted) or "{}")
    except Exception:
        return None


def is_binance_enabled(db) -> bool:
    from models import PaymentMethod
    pm = db.query(PaymentMethod).filter(
        PaymentMethod.method_code == "binance_pay",
        PaymentMethod.is_active == True,
    ).first()
    return bool(pm)


async def create_binance_merchant_order(
    api_key: str,
    secret_key: str,
    merchant_trade_no: str,
    amount_usdt: float,
    description: str = "",
    timeout_minutes: int = 30,
) -> dict:
    """
    Call Binance Pay Create Order API.
    Returns dict with prepayId, checkoutUrl, universalUrl, qrContent, etc.
    API reference: https://developers.binance.com/docs/binance-pay/api-order-create-v3
    """
    try:
        import httpx
        timestamp = int(time.time() * 1000)
        nonce = uuid.uuid4().hex[:32]

        body = json.dumps({
            "env": {"terminalType": "APP"},
            "merchantTradeNo": merchant_trade_no,
            "orderAmount": round(amount_usdt, 8),
            "currency": "USDT",
            "description": description[:256] if description else "Order",
            "goodsDetails": [{
                "goodsType": "02",
                "goodsCategory": "Z000",
                "referenceGoodsId": merchant_trade_no,
                "goodsName": "Digital Product",
                "goodsDetail": description[:256] if description else "",
            }],
        }, separators=(",", ":"))

        payload = f"{timestamp}\n{nonce}\n{body}\n"
        signature = hmac.new(
            secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest().upper()

        headers = {
            "Content-Type": "application/json",
            "BinancePay-Timestamp": str(timestamp),
            "BinancePay-Nonce": nonce,
            "BinancePay-Certificate-SN": api_key,
            "BinancePay-Signature": signature,
        }

        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://bpay.binanceapi.com/binancepay/openapi/v3/order",
                headers=headers,
                content=body,
            )
        data = r.json()
        if data.get("status") == "SUCCESS":
            return {"success": True, "data": data.get("data", {})}
        return {"success": False, "message": data.get("errorMessage", "Binance API error")}
    except Exception as e:
        logger.error(f"[binance] create_merchant_order error: {e}")
        return {"success": False, "message": str(e)}


def verify_binance_webhook_signature(
    timestamp: str,
    nonce: str,
    body: str,
    signature: str,
    secret_key: str,
) -> bool:
    """Verify Binance Pay webhook HMAC-SHA512 signature."""
    try:
        payload = f"{timestamp}\n{nonce}\n{body}\n"
        expected = hmac.new(
            secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest().upper()
        return hmac.compare_digest(expected, signature.upper())
    except Exception as e:
        logger.error(f"[binance] signature verification error: {e}")
        return False


async def query_binance_order_status(
    api_key: str,
    secret_key: str,
    merchant_trade_no: str,
) -> dict:
    """Query Binance Pay order status."""
    try:
        import httpx
        timestamp = int(time.time() * 1000)
        nonce = uuid.uuid4().hex[:32]
        body = json.dumps({"merchantTradeNo": merchant_trade_no}, separators=(",", ":"))
        payload = f"{timestamp}\n{nonce}\n{body}\n"
        signature = hmac.new(
            secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest().upper()
        headers = {
            "Content-Type": "application/json",
            "BinancePay-Timestamp": str(timestamp),
            "BinancePay-Nonce": nonce,
            "BinancePay-Certificate-SN": api_key,
            "BinancePay-Signature": signature,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "https://bpay.binanceapi.com/binancepay/openapi/v2/order/query",
                headers=headers,
                content=body,
            )
        return r.json()
    except Exception as e:
        logger.error(f"[binance] query_order_status error: {e}")
        return {}
