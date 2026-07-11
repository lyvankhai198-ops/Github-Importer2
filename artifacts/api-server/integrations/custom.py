import time
import httpx
from integrations.base import BaseAdapter


class CustomAdapter(BaseAdapter):
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def test_connection(self) -> dict:
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(f"{self.base_url}/me", headers=self._headers())
                latency = int((time.time() - start) * 1000)
                if r.status_code == 200:
                    return {"success": True, "message": "Connection successful", "latency_ms": latency, "data": r.json()}
                return {"success": False, "message": f"HTTP {r.status_code}: {r.text}", "latency_ms": latency}
        except Exception as e:
            latency = int((time.time() - start) * 1000)
            return {"success": False, "message": str(e), "latency_ms": latency}

    async def get_balance(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(f"{self.base_url}/balance", headers=self._headers())
                if r.status_code == 200:
                    data = r.json()
                    return {"success": True, "balance": data.get("balance", 0), "currency": data.get("currency", "USD"), "data": data}
                return {"success": False, "message": f"HTTP {r.status_code}: {r.text}", "balance": 0, "currency": "USD"}
        except Exception as e:
            return {"success": False, "message": str(e), "balance": 0, "currency": "USD"}

    async def get_products(self) -> list:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(f"{self.base_url}/products", headers=self._headers())
                if r.status_code == 200:
                    data = r.json()
                    items = data if isinstance(data, list) else data.get("data", data.get("products", []))
                    result = []
                    for item in items:
                        result.append({
                            "id": str(item.get("id", "")),
                            "name": item.get("name", item.get("title", "")),
                            "price": float(item.get("price", 0)),
                            "stock": int(item.get("stock", item.get("quantity", 0))),
                            "status": item.get("status", "active"),
                            "raw": item,
                        })
                    return result
                return []
        except Exception:
            return []

    async def buy_product(self, product_id: str, quantity: int, idempotency_key: str) -> dict:
        try:
            payload = {"product_id": product_id, "quantity": quantity, "idempotency_key": idempotency_key}
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(f"{self.base_url}/orders", headers=self._headers(), json=payload)
                if r.status_code in (200, 201):
                    data = r.json()
                    return {"success": True, "order_id": str(data.get("order_id", data.get("id", ""))), "data": data}
                return {"success": False, "message": f"HTTP {r.status_code}: {r.text}", "order_id": None, "data": {}}
        except Exception as e:
            return {"success": False, "message": str(e), "order_id": None, "data": {}}

    async def get_orders(self, limit: int = 50) -> list:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(f"{self.base_url}/orders", headers=self._headers(), params={"limit": limit})
                if r.status_code == 200:
                    data = r.json()
                    return data if isinstance(data, list) else data.get("data", [])
                return []
        except Exception:
            return []

    async def get_order(self, order_id: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(f"{self.base_url}/orders/{order_id}", headers=self._headers())
                if r.status_code == 200:
                    return {"success": True, "data": r.json()}
                return {"success": False, "message": f"HTTP {r.status_code}: {r.text}", "data": {}}
        except Exception as e:
            return {"success": False, "message": str(e), "data": {}}
