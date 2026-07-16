import time
import httpx
from integrations.base import BaseAdapter
from services.normalize import normalize_aicenter_buyer_product


class AICenterBuyerAdapter(BaseAdapter):
    """
    Adapter for the "AI Center Buyer" supplier API
    (base URL: https://canboso.com — endpoints already include the
    /api/telegram-buyer/... prefix, so the base URL must NOT include /api).

    Auth: header `X-API-Key: <api_key>` only — never Authorization/Bearer,
    never access_token, never sent as a query param.

    Endpoints:
      - Test/balance: GET  /api/telegram-buyer/balance
      - Products:     GET  /api/telegram-buyer/products (reads response.products)
      - Purchase:     POST /api/telegram-buyer/purchase

    AI Center Buyer has no documented order-listing endpoint, so
    get_orders/get_order return explicit "not supported" responses rather
    than fabricating data (mirrors CanBosoAdapter's approach).
    """

    DEFAULT_BASE_URL = "https://canboso.com"

    def _headers(self) -> dict:
        return {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def test_connection(self) -> dict:
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(f"{self.base_url}/api/telegram-buyer/balance", headers=self._headers())
                latency = int((time.time() - start) * 1000)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("success", True):
                        return {"success": True, "message": "Connection successful", "latency_ms": latency, "data": data}
                    return {"success": False, "message": data.get("message", "success=false"), "latency_ms": latency}
                return {"success": False, "message": f"HTTP {r.status_code}: {r.text[:300]}", "latency_ms": latency}
        except httpx.TimeoutException:
            latency = int((time.time() - start) * 1000)
            return {"success": False, "message": "Timeout khi kết nối AI Center Buyer", "latency_ms": latency}
        except Exception as e:
            latency = int((time.time() - start) * 1000)
            return {"success": False, "message": str(e), "latency_ms": latency}

    async def get_balance(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(f"{self.base_url}/api/telegram-buyer/balance", headers=self._headers())
                if r.status_code == 200:
                    data = r.json()
                    return {
                        "success": bool(data.get("success", True)),
                        "balance": data.get("balance", 0),
                        "currency": data.get("currency", data.get("walletCurrency", "VND")),
                        "data": data,
                    }
                return {"success": False, "message": f"HTTP {r.status_code}: {r.text[:300]}", "balance": 0, "currency": "VND"}
        except httpx.TimeoutException:
            return {"success": False, "message": "Timeout khi kết nối AI Center Buyer", "balance": 0, "currency": "VND"}
        except Exception as e:
            return {"success": False, "message": str(e), "balance": 0, "currency": "VND"}

    async def get_products(self, **filters) -> list:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(f"{self.base_url}/api/telegram-buyer/products", headers=self._headers())
                if r.status_code != 200:
                    return []
                data = r.json()
                # AI Center Buyer's own response shape — always read
                # response.products, never Zampto's response.data/list shape.
                items = data.get("products", []) if isinstance(data, dict) else []
                wallet_currency = data.get("walletCurrency") if isinstance(data, dict) else None
                result = []
                for item in items:
                    normalized = normalize_aicenter_buyer_product(item, wallet_currency=wallet_currency)
                    normalized["raw"] = item
                    result.append(normalized)
                return result
        except Exception:
            return []

    async def buy_product(
        self,
        product_id: str,
        quantity: int,
        idempotency_key: str,
        buyer_email: str = None,
        requires_customer_email: bool = False,
        requires_slot_months: bool = False,
        slot_months: int = None,
        **kwargs,
    ) -> dict:
        """
        POST /api/telegram-buyer/purchase with a PurchaseRequest body.
        `customer_email`/`slot_months` are only included when the product
        actually requires them (per its requiresCustomerEmail /
        requiresSlotMonths flags from the products sync) — never sent as
        dead weight, and never dropped when required.

        No "key" field is sent: the API key is already authenticated via
        the X-API-Key header, and AI Center Buyer's PurchaseRequest does not
        document a body-level key requirement. If a future schema check
        shows the backend actually requires it, add it here rather than
        guessing.
        """
        try:
            payload = {
                "product_id": product_id,
                "quantity": quantity,
            }
            if requires_customer_email and buyer_email:
                payload["customer_email"] = buyer_email
            if requires_slot_months and slot_months:
                payload["slot_months"] = slot_months

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.base_url}/api/telegram-buyer/purchase",
                    headers=self._headers(),
                    json=payload,
                )
                if r.status_code in (200, 201):
                    data = r.json()
                    success = bool(data.get("success", True))
                    return {
                        "success": success,
                        "order_id": data.get("orderCode"),
                        "message": data.get("message", "") if not success else "",
                        "data": data,
                    }
                return {
                    "success": False,
                    "message": f"HTTP {r.status_code}: {r.text[:300]}",
                    "order_id": None,
                    "data": {},
                }
        except httpx.TimeoutException:
            return {"success": False, "message": "Timeout khi gọi AI Center Buyer", "order_id": None, "data": {}}
        except Exception as e:
            return {"success": False, "message": str(e), "order_id": None, "data": {}}

    async def get_orders(self, limit: int = 50) -> list:
        # No documented order-listing endpoint for AI Center Buyer.
        return []

    async def get_order(self, order_id: str) -> dict:
        return {"success": False, "message": "AI Center Buyer không hỗ trợ tra cứu đơn theo id", "data": {}}
