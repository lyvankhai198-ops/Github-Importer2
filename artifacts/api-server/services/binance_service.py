"""
Binance Pay verification via Binance API Management (read-only API Key + Secret Key).

Binance Pay Merchant API integration (Merchant ID, checkout-order creation,
webhook, certificate) has been fully removed. Payments are verified instead
by calling the account's own Binance Pay transaction history endpoint
(`/sapi/v1/pay/transactions`) with a signed request, and matching the
shopper-submitted transaction ID against it. No Binance Pay merchant
registration is required — only a personal/business Binance account with an
API Management key that has "Enable Reading" permission.

Config stored in PaymentMethod.config_encrypted (JSON, Fernet-encrypted) for
method_code="binance_pay":
{
  "api_key": "",                     // NEVER logged
  "secret_key": "",                  // NEVER logged
  "receiver_binance_id": "",         // the shop's own Binance ID that must receive the funds
  "default_coin": "USDT",
  "order_expiry_minutes": 30,
  "min_check_interval_seconds": 15,  // throttle for Pay History pulls
  "amount_tolerance": 0,             // admin-configurable Decimal tolerance, defaults to 0
  "qr_image_path": "",               // optional personal Binance Pay QR image
}
"""
import json
import hmac
import hashlib
import logging
import time
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

BASE_URL = "https://api.binance.com"

# Binance error codes translated into specific, non-leaky reasons.
_ERROR_REASON_MAP = {
    -1021: "timestamp_out_of_range",
    -1022: "invalid_signature",
    -2014: "invalid_key",
    -2015: "permission_denied",  # invalid API-key, IP, or permissions for this action
}


def get_binance_config(db) -> dict | None:
    """Return the decrypted Binance Pay config, or None if not active/configured."""
    from models import PaymentMethod
    from crypto import decrypt
    pm = db.query(PaymentMethod).filter(
        PaymentMethod.method_code == "binance_pay",
        PaymentMethod.is_active == True,
    ).first()
    if not pm or not pm.config_encrypted:
        return None
    try:
        cfg = json.loads(decrypt(pm.config_encrypted) or "{}")
    except Exception:
        return None
    return cfg or None


def is_binance_enabled(db) -> bool:
    cfg = get_binance_config(db)
    return bool(cfg and cfg.get("api_key") and cfg.get("secret_key") and cfg.get("receiver_binance_id"))


async def _server_time_offset_ms() -> int:
    """Return (binance_server_time_ms - local_time_ms) for clock-skew correction."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{BASE_URL}/api/v3/time")
        data = r.json()
        server_time = int(data.get("serverTime") or 0)
        if server_time:
            return server_time - int(time.time() * 1000)
    except Exception as e:
        logger.error(f"[binance] server time sync error: {e}")
    return 0


def _sign(secret_key: str, query_string: str) -> str:
    return hmac.new(secret_key.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()


async def _signed_get(path: str, api_key: str, secret_key: str, params: dict,
                       _retried: bool = False, _offset_ms: int = 0) -> dict:
    """
    Perform a signed Binance GET request.
    Returns {"success": True, "data": ...} or {"success": False, "reason": "...", "message": "..."}.
    On a timestamp-outside-recvWindow error, resyncs server time and retries exactly once.
    """
    import httpx
    ts = int(time.time() * 1000) + _offset_ms
    query = dict(params)
    query["timestamp"] = ts
    query.setdefault("recvWindow", 10000)
    query_string = urlencode(query)
    signature = _sign(secret_key, query_string)
    url = f"{BASE_URL}{path}?{query_string}&signature={signature}"
    headers = {"X-MBX-APIKEY": api_key}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers=headers)
        try:
            data = r.json()
        except Exception:
            data = {}

        if r.status_code == 200:
            return {"success": True, "data": data}

        code = data.get("code") if isinstance(data, dict) else None

        if code == -1021 and not _retried:
            offset = await _server_time_offset_ms()
            return await _signed_get(path, api_key, secret_key, params, _retried=True, _offset_ms=offset)

        reason = _ERROR_REASON_MAP.get(code, "api_error")
        if r.status_code == 403:
            reason = "ip_not_allowed"

        return {
            "success": False,
            "reason": reason,
            "message": data.get("msg") if isinstance(data, dict) else f"HTTP {r.status_code}",
            "http_status": r.status_code,
        }
    except httpx.TimeoutException:
        return {"success": False, "reason": "unavailable", "message": "Binance API timeout"}
    except Exception as e:
        logger.error(f"[binance] signed_get error: {e}")
        return {"success": False, "reason": "unavailable", "message": str(e)}


async def fetch_pay_transactions(api_key: str, secret_key: str, limit: int = 100) -> dict:
    """
    Call GET /sapi/v1/pay/transactions — the account's own Binance Pay history.
    Returns {"success": True, "transactions": [...]} or a failure dict with "reason".
    """
    result = await _signed_get("/sapi/v1/pay/transactions", api_key, secret_key, {"limit": limit})
    if not result.get("success"):
        return result
    data = result["data"]
    rows = data.get("data") if isinstance(data, dict) else data
    return {"success": True, "transactions": rows or []}


async def test_binance_connection(api_key: str, secret_key: str) -> dict:
    """
    Verify the configured API Key/Secret can read Pay History, without
    exposing any transaction contents to the caller.
    """
    if not api_key or not secret_key:
        return {"success": False, "reason": "invalid_key", "message": "Thiếu API Key hoặc Secret Key."}

    result = await fetch_pay_transactions(api_key, secret_key, limit=1)
    if result.get("success"):
        return {"success": True, "message": "✅ Kết nối thành công — API Key có quyền đọc lịch sử Pay History."}

    reason = result.get("reason", "unavailable")
    messages = {
        "invalid_key": "❌ API Key không hợp lệ.",
        "permission_denied": "❌ API Key hợp lệ nhưng không có quyền đọc Pay History (cần bật quyền 'Enable Reading').",
        "ip_not_allowed": "❌ IP hiện tại chưa được whitelist cho API Key này.",
        "invalid_signature": "❌ Secret Key không đúng — chữ ký không hợp lệ.",
        "timestamp_out_of_range": "❌ Lệch thời gian hệ thống — vui lòng thử lại.",
        "unavailable": "❌ Không thể kết nối tới Binance lúc này. Vui lòng thử lại sau.",
        "api_error": "❌ Binance từ chối yêu cầu — kiểm tra lại cấu hình API Key.",
    }
    return {"success": False, "reason": reason, "message": messages.get(reason, result.get("message") or "Lỗi không xác định")}
