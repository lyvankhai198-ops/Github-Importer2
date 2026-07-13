import time
import httpx
from integrations.base import BaseAdapter
from services.normalize import normalize_canboso_product

# Safety cap so a misbehaving/never-ending paginated response can't loop
# forever during a full sync.
_MAX_SYNC_PAGES = 200


class CanBosoAdapter(BaseAdapter):
    """
    Adapter for the "CanBoSo Market" supplier API
    (default base URL: https://canboso.com/api/public/market).

    Auth: header `X-API-Key: <api_key>`.
    Products: GET /products (paginated; supports page, limit, search, sort,
    slotProductType, seller, emoji).
    Purchase: POST /products/{product_id}/buy with
    {"quantity": <int>, "email": <str>}.

    CanBoSo Market has no documented balance or order-listing endpoints, so
    get_balance/get_orders/get_order return explicit "not supported"
    responses rather than fabricating data.
    """

    DEFAULT_BASE_URL = "https://canboso.com/api/public/market"

    def _headers(self) -> dict:
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
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
                    return {"success": True, "message": "Connection successful", "latency_ms": latency, "data": r.json()}
                return {"success": False, "message": f"HTTP {r.status_code}: {r.text[:300]}", "latency_ms": latency}
        except httpx.TimeoutException:
            latency = int((time.time() - start) * 1000)
            return {"success": False, "message": "Timeout khi kết nối CanBoSo Market", "latency_ms": latency}
        except Exception as e:
            latency = int((time.time() - start) * 1000)
            return {"success": False, "message": str(e), "latency_ms": latency}

    async def get_balance(self) -> dict:
        # CanBoSo Market's public API has no balance endpoint — say so
        # explicitly instead of returning a fake/zero balance as if real.
        return {
            "success": False,
            "message": "CanBoSo Market không hỗ trợ tra cứu số dư qua API",
            "balance": 0,
            "currency": "VND",
        }

    async def _fetch_page(self, page: int, base_params: dict) -> list:
        params = dict(base_params)
        params["page"] = page
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(f"{self.base_url}/products", headers=self._headers(), params=params)
            if r.status_code != 200:
                return []
            data = r.json()
            items = data if isinstance(data, list) else data.get("data", data.get("products", []))
            return items or []

    async def get_products(
        self,
        search: str = None,
        sort: str = None,
        slotProductType: str = None,
        seller: str = None,
        emoji: str = None,
        limit: int = 100,
        page: int = None,
    ) -> list:
        """
        If `page` is given, fetch just that page. Otherwise sweep every page
        (bounded by _MAX_SYNC_PAGES) and return the combined, normalized
        list — this is what the periodic/on-demand sync calls.
        """
        base_params = {k: v for k, v in {
            "search": search, "sort": sort, "slotProductType": slotProductType,
            "seller": seller, "emoji": emoji, "limit": limit,
        }.items() if v is not None}

        try:
            if page is not None:
                items = await self._fetch_page(page, base_params)
                return [self._normalize(item) for item in items]

            all_items = []
            p = 1
            while p <= _MAX_SYNC_PAGES:
                items = await self._fetch_page(p, base_params)
                if not items:
                    break
                all_items.extend(items)
                if len(items) < limit:
                    break
                p += 1
            return [self._normalize(item) for item in all_items]
        except Exception:
            return []

    def _normalize(self, item: dict) -> dict:
        normalized = normalize_canboso_product(item)
        normalized["raw"] = item
        return normalized

    async def buy_product(self, product_id: str, quantity: int, idempotency_key: str, buyer_email: str = None, **kwargs) -> dict:
        try:
            payload = {"quantity": quantity, "email": buyer_email or "buyer@example.com"}
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.base_url}/products/{product_id}/buy",
                    headers=self._headers(),
                    json=payload,
                )
                if r.status_code in (200, 201):
                    data = r.json()
                    order_obj = data.get("order", data)
                    order_id = str(
                        data.get("order_id") or order_obj.get("order_id") or
                        order_obj.get("id") or data.get("id") or ""
                    )
                    success = data.get("success", True)
                    return {
                        "success": bool(success),
                        "order_id": order_id,
                        "message": data.get("message", ""),
                        "data": data,
                    }
                return {
                    "success": False,
                    "message": f"HTTP {r.status_code}: {r.text[:300]}",
                    "order_id": None,
                    "data": {},
                }
        except httpx.TimeoutException:
            return {"success": False, "message": "Timeout khi gọi CanBoSo Market", "order_id": None, "data": {}}
        except Exception as e:
            return {"success": False, "message": str(e), "order_id": None, "data": {}}

    async def get_orders(self, limit: int = 50) -> list:
        # No documented order-listing endpoint for CanBoSo Market.
        return []

    async def get_order(self, order_id: str) -> dict:
        # No documented GET /orders/{id} — the buy_product() response must
        # be self-contained (see process_paid_order's handling).
        return {"success": False, "message": "CanBoSo Market không hỗ trợ tra cứu đơn theo id", "data": {}}
