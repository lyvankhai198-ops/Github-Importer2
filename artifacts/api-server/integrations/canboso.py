import time
import httpx
from integrations.base import BaseAdapter
from services.normalize import normalize_canboso_product


class CanBosoAdapter(BaseAdapter):
    """
    Adapter for CanBoSo's "Public Market API" (https://canboso.com/api/public/market),
    documented at canboso.com/cho -> "Public Market API" tab (Swagger: /api/public/market).

    Auth: header `X-API-Key: <api_key>` only.
    Rate limit: 60 requests/minute per API key (server-enforced; we don't
    throttle client-side — a 429 just surfaces as a normal HTTP error here).

    Endpoints:
      - Products: GET  /products
          query params: page, limit (max 100), search, sort
          (price_asc|price_desc|newest|available_desc|name_asc),
          slotProductType (account|slot), seller (seller username filter),
          emoji (category filter).
      - Buy:      POST /products/{id}/buy   body: {quantity, email}
          - "account" items deliver instantly: response is a flat
            BuyItemResponse {user, password, verifyEmail, expiryText, otherInfo}.
          - "slot" items require `email` (the seller manually adds it to the
            slot) and never deliver instantly: response is an order object
            (status "paid", items null) the seller must still fulfill.

    No documented balance/order-listing endpoint for this API — get_balance
    and get_orders/get_order return explicit "not supported" responses
    rather than fabricating data.
    """

    DEFAULT_BASE_URL = "https://canboso.com/api/public/market"

    # Safety cap on pagination — the API caps `limit` at 100/page; this
    # bounds get_products() to at most 5000 items so a runaway "available
    # forever" market page count can never hang a sync.
    _MAX_PAGES = 50

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
                r = await client.get(
                    f"{self.base_url}/products",
                    headers=self._headers(),
                    params={"limit": 1},
                )
                latency = int((time.time() - start) * 1000)
                if r.status_code == 200:
                    return {"success": True, "message": "Kết nối thành công", "latency_ms": latency}
                return {"success": False, "message": f"HTTP {r.status_code}: {r.text[:300]}", "latency_ms": latency}
        except httpx.TimeoutException:
            latency = int((time.time() - start) * 1000)
            return {"success": False, "message": "Timeout khi kết nối CanBoSo", "latency_ms": latency}
        except Exception as e:
            latency = int((time.time() - start) * 1000)
            return {"success": False, "message": str(e), "latency_ms": latency}

    async def get_balance(self) -> dict:
        return {
            "success": False,
            "message": "CanBoSo Public Market API không có endpoint số dư",
            "balance": 0,
            "currency": "VND",
        }

    async def get_products(self, **filters) -> list:
        """
        Fetches every page of /products (100/page, the API max) and
        normalizes each MarketProduct item. Accepted filters (all optional,
        passed straight through as query params): search, sort,
        slotProductType, seller, emoji.
        """
        result = []
        page = 1
        limit = 100
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                while page <= self._MAX_PAGES:
                    params = {"page": page, "limit": limit}
                    for key in ("search", "sort", "slotProductType", "seller", "emoji"):
                        if filters.get(key):
                            params[key] = filters[key]
                    r = await client.get(f"{self.base_url}/products", headers=self._headers(), params=params)
                    if r.status_code != 200:
                        break
                    data = r.json()
                    items = data.get("data") if isinstance(data, dict) else data
                    items = items or []
                    if not isinstance(items, list):
                        break
                    for item in items:
                        normalized = normalize_canboso_product(item)
                        normalized["raw"] = item
                        result.append(normalized)
                    if len(items) < limit:
                        break
                    page += 1
            return result
        except Exception:
            return result

    async def buy_product(
        self,
        product_id: str,
        quantity: int,
        idempotency_key: str,
        buyer_email: str = None,
        **kwargs,
    ) -> dict:
        """
        POST /products/{id}/buy with {quantity, email}. `email` is always
        sent when available — required for slot items (the seller adds it to
        the slot) and accepted (though not required) for account items per
        the API's own examples. No idempotency key is sent: the documented
        request body has no such field.
        """
        try:
            payload = {"quantity": quantity}
            if buyer_email:
                payload["email"] = buyer_email

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.base_url}/products/{product_id}/buy",
                    headers=self._headers(),
                    json=payload,
                )
                if r.status_code not in (200, 201):
                    return {
                        "success": False,
                        "message": f"HTTP {r.status_code}: {r.text[:300]}",
                        "order_id": None,
                        "data": {},
                    }
                data = r.json()
                if isinstance(data, dict) and data.get("success") is False:
                    return {
                        "success": False,
                        "message": data.get("message", "success=false"),
                        "order_id": None,
                        "data": {},
                    }

                # Account item: a flat BuyItemResponse (user/password/...).
                # Wrap it as {"accounts": [...]} so
                # normalize.normalize_delivery_items() — which looks for a
                # list under "accounts"/"items"/etc — finds it.
                if isinstance(data, dict) and ("user" in data or "password" in data):
                    return {"success": True, "order_id": None, "message": "", "data": {"accounts": [data]}}

                # Slot item: an order object (status=paid, items=null) the
                # seller must still fulfill — payment_service reads
                # order_code/order_id straight off this dict.
                order_data = data.get("order", data) if isinstance(data, dict) else {}
                order_id = (
                    order_data.get("order_id") or order_data.get("orderId") or
                    order_data.get("_id") or order_data.get("id")
                )
                return {"success": True, "order_id": order_id, "message": "", "data": data if isinstance(data, dict) else {}}
        except httpx.TimeoutException:
            return {"success": False, "message": "Timeout khi mua hàng CanBoSo", "order_id": None, "data": {}}
        except Exception as e:
            return {"success": False, "message": str(e), "order_id": None, "data": {}}

    async def get_orders(self, limit: int = 50) -> list:
        # No documented order-listing endpoint for this API.
        return []

    async def get_order(self, order_id: str) -> dict:
        return {"success": False, "message": "CanBoSo Market không hỗ trợ tra cứu đơn theo id", "data": {}}
